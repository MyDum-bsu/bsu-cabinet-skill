"""bsu-browser — CLI-слой браузера кабинета БГУ для любого харнесса.

Модель: `bsu-browser start` поднимает Chrome (постоянный профиль,
remote-debugging порт); остальные команды подключаются к нему по CDP —
сессия живёт между вызовами CLI. Секреты — keyring, модель пароль не видит.

Команды:
  start [--headed]       поднять Chrome headless (дефолт); --headed —
                         окно на экране (смотреть процесс при разработке)
  stop                   закрыть Chrome
  status                 жив ли браузер + текущий URL
  navigate <url>         открыть страницу
  snapshot               текстовая сводка страницы (поля/кнопки/ссылки/заголовки)
  snapshot plan          структурный снимок ИП → JSON bsu.snapshot/1 (только чтение)
  fill <label> <value>   заполнить поле по подписи (label/placeholder/name)
  fill-login             заполнить логин+пароль из keyring (значения не выводятся)
  login [--sms adb --adb-serial SERIAL]  войти с ручным SMS в Chrome (дефолт)
                         авто-SMS только для явно выбранного телефона
  creds <login>          сохранить креды: пароль → keyring ОС, логин → ~/.bsu-cabinet/login
  setup                  онбординг для человека: форма в окне Chrome → креды в keyring
  click <text>           клик по элементу с точным текстом
  click-xy <x> <y>       клик по координатам (экранная клавиатура SMS)
  key <key>              нажать клавишу (Escape и т.п.)
  wait-text <text> [ms]  ждать текст на странице
  wait-url-not <substr>  ждать, пока URL перестанет содержать подстроку
  confirm                клик «Подтвердить»
  eval <js>              выполнить JS в контексте страницы, вывести JSON
  shot <name>            скриншот → путь
  url / title / text     URL / заголовок / видимый текст страницы
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import time
from importlib.resources import as_file, files
from ipaddress import IPv6Address, ip_address
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout

STATE_DIR = Path(os.environ.get("BSU_STATE_DIR", Path.home() / ".bsu-cabinet"))
PROFILE_DIR = STATE_DIR / "chrome-profile-cli"
STATE_FILE = STATE_DIR / "session.json"
SHOTS_DIR = STATE_DIR / "shots"
DEBUG_PORT = int(os.environ.get("BSU_PORT", 9377))


def _state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def _save_state(**kw) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    st = {**_state(), **kw}
    STATE_FILE.write_text(json.dumps(st))


class _CdpUnreachable(SystemExit):
    """CDP-транспорт не отвечает при уже подтверждённой сессии — отказ
    транспорта, а не отказ доверия. Отказ доверия (foreign/unknown/
    подмена снимка) — обычный SystemExit и никогда не разрешает
    остановку по устаревшему «live»."""


def _connect(expected: dict | None = None):
    from playwright.sync_api import sync_playwright

    st = _state()
    if expected is not None and st != expected:
        # подтверждение вызывающего относится к другому снимку: нельзя
        # ни подключаться, ни (по возврату отказа) останавливать чужое
        raise SystemExit(
            "Состояние сессии изменилось между проверкой и подключением — отменяю."
        )
    if not st.get("port"):
        raise SystemExit("Браузер не поднят: bsu-browser start")
    health = _session_health(st)
    if health == "dead":
        raise SystemExit("Браузер не поднят: bsu-browser start")
    if health != "live":
        raise SystemExit(_FOREIGN_SESSION)
    if _state() != st:
        # метаданные подменили между чтением адреса и проверкой —
        # подтверждение относится к другому экземпляру
        raise SystemExit(
            "Состояние сессии изменилось между проверкой и подключением — отменяю."
        )
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{st['port']}")
    except Exception:
        pw.stop()
        raise _CdpUnreachable("Chrome не отвечает на CDP — перезапусти: bsu-browser start") from None
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    page = _pick_page(ctx)
    try:
        page.bring_to_front()
    except Exception:
        pass
    return pw, browser, page


def _pick_page(ctx):
    """Вкладка реального окна: у фоновых CDP-таргетов (omnibox, оторванные
    страницы) нет живого windowState — берём страницу видимого окна."""
    pages = ctx.pages
    if not pages:
        return ctx.new_page()
    live = []
    for p in pages:
        try:
            cdp = ctx.new_cdp_session(p)
            info = cdp.send("Browser.getWindowForTarget")
            live.append((info["windowId"], info.get("windowState"), p))
        except Exception:
            pass
    if not live:
        return pages[-1]
    visible = [t for t in live if t[1] != "minimized"] or live
    win = max(set(w for w, _, _ in visible), key=lambda w: sum(x[0] == w for x in visible))
    in_win = [p for w, _, p in visible if w == win]
    normal = [p for p in in_win if not p.url.startswith("chrome://")]
    return (normal or in_win)[-1]


def _chrome_path() -> str:
    """Путь к Chrome: BSU_CHROME env → PATH → типовые пути ОС."""
    import shutil

    if p := os.environ.get("BSU_CHROME"):
        return p
    for name in ("google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser"):
        if p := shutil.which(name):
            return p
    if sys.platform == "darwin":
        p = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    elif sys.platform == "win32":
        for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(env_name)
            if root:
                p = str(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
                if Path(p).exists():
                    return p
        p = None
    else:
        for p in ("/usr/bin/google-chrome", "/opt/google/chrome/chrome", "/usr/bin/chromium"):
            if Path(p).exists():
                return p
        p = None
    if not p:
        raise SystemExit("Chrome не найден: задай BSU_CHROME=/путь/к/chrome")
    return p


def _jsonable(v):
    try:
        json.dumps(v)
        return v
    except TypeError:
        return str(v)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _pid_args(pid: int) -> list[str] | None:
    """Аргументы процесса как есть: /proc (Linux), ps (macOS/*BSD).
    None — прочитать не удалось."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
    except OSError:
        pass
    try:
        out = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        return shlex.split(out.stdout)
    except ValueError:
        return out.stdout.split()


def _pid_is_own_chrome(pid: int) -> bool | None:
    """True — процесс запущен с точным аргументом профиля комплекта;
    False — точно чужой; None — проверить не удалось (тогда не трогаем)."""
    args = _pid_args(pid)
    if args is None:
        return None
    return f"--user-data-dir={PROFILE_DIR}" in args


def _port_open(port: int) -> bool:
    try:
        with socket.socket() as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False


_MAX_PID_OR_FD = 2**31 - 1
_MAX_UINT32 = 2**32 - 1
_MAX_UINT64 = 2**64 - 1


def _bounded_uint(token: str, limit: int) -> int | None:
    if len(token) > len(str(limit)) or not token.isascii() or not token.isdecimal():
        return None
    value = int(token)
    return value if value <= limit else None


def _split_host_port(token: str) -> tuple[str, int] | None:
    """Числовой `addr:port` слушателя; порт 0 не может быть привязан."""
    host, sep, port = token.rpartition(":")
    pnum = _bounded_uint(port, 65535) if sep else None
    return (host, pnum) if pnum else None


def _endpoint_kind(host: str) -> str:
    """Класс адреса слушателя относительно CDP-endpoint 127.0.0.1:port.

    "exact" — точный 127.0.0.1 без привязки к интерфейсу;
    "maybe" — адрес с недоказуемой применимостью (wildcard, `%ifname`);
    "foreign" — заведомо посторонний адрес; "invalid" — битое поле."""
    host = host.strip().lower()
    if host.startswith("["):
        bracketed = re.fullmatch(r"\[([^\[\]]+)\](%[^\[\]%]+)?", host)
        if bracketed is None or ":" not in bracketed[1].partition("%")[0]:
            return "invalid"
        host = bracketed[1] + (bracketed[2] or "")
    host, scoped, interface = host.partition("%")
    if scoped and (not interface or any(c in interface for c in "%[]")):
        return "invalid"
    if host == "*":
        return "maybe"
    try:
        address = ip_address(host)
    except ValueError:
        return "invalid"
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if scoped or address.is_unspecified:
        return "maybe"
    return "exact" if str(address) == "127.0.0.1" else "foreign"


_SS_PROCESS = r'\("[^"]*",pid=([1-9][0-9]{0,9}),fd=([0-9]{1,10})\)'
_SS_WILDCARD_PEERS = {"*:*", "0.0.0.0:*", "[::]:*", ":::*"}


def _ss_process_pids(owners: str) -> set[tuple[int, int]] | None:
    """Пары (pid, fd) из users:(...); None — битое числовое поле."""
    pairs = set()
    for pid_text, fd_text in re.findall(_SS_PROCESS, owners):
        pid = _bounded_uint(pid_text, _MAX_PID_OR_FD)
        fd = _bounded_uint(fd_text, _MAX_PID_OR_FD)
        if not pid or fd is None:
            return None
        pairs.add((pid, fd))
    return pairs


def _ss_owner_pids(out: str, port: int) -> tuple[str, int | None]:
    """Разбор `ss -tlnpH sport = :port` → (вердикт, pid):

    "pid" — однозначный владелец точного unscoped 127.0.0.1; "unknown" —
    слушатель endpoint обнаружен, но владелец скрыт/неоднозначен, рядом
    есть потенциально применимая строка ("maybe"), либо выдача
    недостоверна (неразборчивая/оборванная запись или строка чужого
    порта — по частичному разбору нельзя получить ни однозначный pid, ни
    достоверное отсутствие); "absent" — наблюдение состоялось и
    подходящих слушателей нет (пустой фильтрованный вывод или строки
    порта лишь на заведомо посторонних адресах).
    """
    if not out.strip():
        return "absent", None
    exact: set[int] = set()
    maybe_seen = False
    descriptor_owner: dict[tuple[int, int], str] = {}
    row = (
        r"LISTEN\s+([0-9]+)\s+([0-9]+)\s+(\S+)\s+(\S+)"
        rf"(?:\s+(?P<owners>users:\({_SS_PROCESS}(?:,{_SS_PROCESS})*\)))?"
    )
    for line in out.splitlines():
        if not line.strip():
            continue
        fields = re.fullmatch(row, line.strip())
        if (
            fields is None
            or _bounded_uint(fields[1], _MAX_UINT32) is None
            or _bounded_uint(fields[2], _MAX_UINT32) is None
            or fields[4] not in _SS_WILDCARD_PEERS
        ):
            return "unknown", None
        ep = _split_host_port(fields[3])
        if ep is None or ep[1] != port:
            return "unknown", None
        kind = _endpoint_kind(ep[0])
        pairs = _ss_process_pids(fields["owners"] or "")
        if kind == "invalid" or pairs is None or not pairs:
            # битое поле или скрытый владелец — не доказательство ни
            # присутствия, ни отсутствия, до классификации адреса
            return "unknown", None
        for pair in pairs:
            if descriptor_owner.get(pair, fields[3]) != fields[3]:
                # один (pid,fd) на двух endpoint — противоречие
                return "unknown", None
            descriptor_owner[pair] = fields[3]
        if kind == "maybe":
            maybe_seen = True
        elif kind == "exact":
            exact.update(pid for pid, _fd in pairs)
    if len(exact) == 1 and not maybe_seen:
        return "pid", next(iter(exact))
    if exact or maybe_seen:
        return "unknown", None
    return "absent", None


def _lsof_owner_pids(out: str, port: int) -> tuple[str, int | None]:
    """Тот же контракт, что у _ss_owner_pids, для `lsof -nP -iTCP:port
    -sTCP:LISTEN`: голый pid (`-t`) адреса не несёт, разбирается колонка
    NAME; `*`/wildcard — потенциально применим, но не доказан. Только
    полный заголовок в начале таблицы допустим; пустая таблица lsof
    не подтверждает отсутствие подходящих слушателей."""
    rows = [line.split() for line in out.splitlines() if line.strip()]
    header = "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME".split()
    # полный штатный заголовок обязателен — без него положение колонок
    # не подтверждено и ни одна строка не доказательна
    if not rows or rows[0] != header:
        return "unknown", None
    rows = rows[1:]
    if not rows:
        return "unknown", None
    exact: set[int] = set()
    exact_seen = maybe_seen = False
    descriptor_owner: dict[tuple[int, str], str] = {}
    for fields in rows:
        if len(fields) != 10:
            return "unknown", None
        pid = _bounded_uint(fields[1], _MAX_PID_OR_FD)
        fd = fields[3]
        fd_num = _bounded_uint(fd[:-1], _MAX_PID_OR_FD)
        size_off = fields[6]
        if (
            not pid
            or fd[-1] not in "rwu"
            or fd_num is None
            or fields[4] not in ("IPv4", "IPv6")
            or _bounded_uint(fields[5], _MAX_UINT32) is None
            or not size_off.startswith("0t")
            or _bounded_uint(size_off[2:], _MAX_UINT64) is None
            or fields[7] != "TCP"
            or fields[9] != "(LISTEN)"
        ):
            return "unknown", None
        ep = _split_host_port(fields[8])
        if ep is None or ep[1] != port:
            return "unknown", None
        kind = _endpoint_kind(ep[0])
        if kind == "invalid":
            return "unknown", None
        # дескриптор — числовой fd, суффикс доступа u/r/w его не меняет
        if descriptor_owner.get((pid, fd_num), fields[8]) != fields[8]:
            # один (pid,fd) на двух endpoint — противоречивое наблюдение
            return "unknown", None
        descriptor_owner[(pid, fd_num)] = fields[8]
        # семейство TYPE обязано совпадать с формой адреса в NAME
        # (`*` — wildcard обоих семейств): IPv4 + `[::…]` недостоверны
        host = ep[0].strip().lower()
        if host != "*":
            v6_form = host.startswith("[") or ":" in host.partition("%")[0]
            if v6_form != (fields[4] == "IPv6"):
                return "unknown", None
        if kind == "maybe":
            maybe_seen = True
        elif kind == "exact":
            exact_seen = True
            exact.add(pid)
    if exact_seen:
        if len(exact) == 1 and not maybe_seen:
            return "pid", next(iter(exact))
        return "unknown", None
    if maybe_seen:
        return "unknown", None
    return "absent", None


class _UnresolvedOwner:
    """Владелец CDP-endpoint не доказан: подходящий слушатель обнаружен,
    но неизвестен/неоднозначен, либо ни один источник наблюдения не дал.
    Неудачный TCP-probe это состояние не снимает и к «ours» не сводит."""


_UNRESOLVED_OWNER = _UnresolvedOwner()


def _port_owner_pid(port: int) -> int | _UnresolvedOwner | None:
    """pid процесса, слушающего именно CDP-адрес 127.0.0.1:port
    (ss, затем lsof).

    None — наблюдение состоялось и подходящего слушателя нет (доказанное
    отсутствие); _UNRESOLVED_OWNER — слушатель обнаружен, но владелец
    неизвестен/неоднозначен, либо ни один источник наблюдения не дал.
    Fallback допустим, только если команда отсутствует
    (FileNotFoundError до исполнения); любой ненулевой код, иной сбой
    запуска или диагностика — терминальная неизвестность без fallback.
    Другие адреса владельца не доказывают."""
    for argv, parse in (
        (["ss", "-tlnpH", f"sport = :{port}"], _ss_owner_pids),
        (["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], _lsof_owner_pids),
    ):
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=5)
        except FileNotFoundError:
            continue  # команда отсутствует — единственный повод для fallback
        except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
            return _UNRESOLVED_OWNER
        stdout = out.stdout or ""
        if out.returncode != 0 or (out.stderr or "").strip():
            return _UNRESOLVED_OWNER
        verdict, pid = parse(stdout, port)
        if verdict == "pid":
            return pid
        if verdict == "absent":
            return None
        return _UNRESOLVED_OWNER
    return _UNRESOLVED_OWNER


def _proc_start_token(pid: int) -> str | None:
    """Токен момента запуска процесса: starttime из /proc (Linux),
    lstart через ps (macOS/*BSD); None — ОС доказательства не дала.

    Нужен, чтобы отличать наш запуск от процесса, переиспользовавшего
    записанный pid с тем же аргументом профиля.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return stat.rsplit(")", 1)[1].split()[19]  # поле 22 — starttime
    except (OSError, IndexError):
        pass
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    token = out.stdout.strip()
    return token if out.returncode == 0 and token else None


def _session_health(st: dict | None = None) -> str:
    """live — подтверждённый собственный запуск, слушающий записанный порт;
    ours — запуск подтверждён, но порт не занят (завершить можно,
    подключаться нельзя); dead — ничего не занято; foreign — точно чужой
    pid, слушатель или переиспользованный pid; unknown — независимо
    доказать владельца нельзя (fail closed, без доверия и без сигналов).

    Проверяется ровно переданный снимок st (pid, порт, pstart): адрес и
    цель действия обязаны совпадать с подтверждёнными."""
    if st is None:
        st = _state()
    pid = st.get("pid")
    port = st.get("port")
    if pid:
        if not _pid_alive(pid):
            return "foreign" if port and _port_open(port) else "dead"
        own = _pid_is_own_chrome(pid)
        if own is not True:
            return "unknown" if own is None else "foreign"
        expected = st.get("pstart")
        token = _proc_start_token(pid)
        if expected is None or token is None:
            return "unknown"  # идентичность запуска недоказуема
        if token != expected:
            return "foreign"  # pid переиспользован другим запуском
        if not port:
            return "unknown"  # записанный pid без endpoint не проверить
        owner = _port_owner_pid(port)
        if owner is _UNRESOLVED_OWNER:
            return "unknown"  # слушатель есть, владелец скрыт/неоднозначен
        if owner is not None:
            return "live" if owner == pid else "foreign"
        return "unknown" if _port_open(port) else "ours"
    return "foreign" if port and _port_open(port) else "dead"


_FOREIGN_SESSION = (
    "На порту уже отвечает браузер или процесс, который нельзя подтвердить как "
    "собственную сессию bsu-browser: чужой Chrome не закрываю и не использую. "
    "Освободи порт сам или задай другой BSU_PORT и повтори команду."
)


def _stop_own_session(st: dict, timeout_s: float = 15) -> bool:
    """SIGTERM только процессу, подтверждённому прямо перед сигналом по
    зафиксированному снимку: pid + аргумент профиля + токен запуска.
    Любое несовпадение — отказ, чужой процесс не трогаем."""
    pid = st.get("pid")
    port = st.get("port") or DEBUG_PORT
    if (
        not pid
        or not st.get("pstart")
        or not _pid_alive(pid)
        or _pid_is_own_chrome(pid) is not True
        or _proc_start_token(pid) != st["pstart"]
    ):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return True
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _pid_alive(pid) and not _port_open(port):
            return True
        time.sleep(0.2)
    return False


def _ensure_headed() -> None:
    """Видимая сессия для участия владельца.

    Свою headless-сессию завершает и поднимает headed с тем же профилем до
    любого запроса SMS; чужой или непроверяемый Chrome не закрывает —
    понятный отказ с указанием следующего шага.
    """
    st = _state()
    health = _session_health(st)  # одно решение на снимок, без переоценки
    if st.get("mode") == "headed" and health == "live":
        return
    if health in ("live", "ours"):
        if not _stop_own_session(st):
            raise SystemExit(
                "Своя headless-сессия не завершилась. Закрой её: bsu-browser stop."
            )
    elif health in ("foreign", "unknown"):
        raise SystemExit(_FOREIGN_SESSION)
    _save_state(port=None, pid=None)
    cmd_start(headless=False)
    st2 = _state()
    if not (st2.get("mode") == "headed" and _session_health(st2) == "live"):
        raise SystemExit(
            "Видимую сессию поднять не удалось. Настройка и ручной SMS требуют "
            "окна: bsu-browser start --headed."
        )


def _ensure_own_session(headless: bool) -> None:
    """Любая живая собственная сессия либо новый запуск; чужую не трогаем."""
    st = _state()
    health = _session_health(st)
    if health in ("foreign", "unknown"):
        raise SystemExit(_FOREIGN_SESSION)
    if health == "live":
        return
    if health == "ours":
        # свой процесс жив, но порт ему не подтверждён — перезапускаем свой
        if not _stop_own_session(st):
            raise SystemExit("Своя сессия не завершилась. Закрой её: bsu-browser stop.")
    _save_state(port=None, pid=None)
    cmd_start(headless=headless)


def cmd_start(headless: bool | None = None) -> None:
    st = _state()
    if st.get("port"):
        health = _session_health(st)
        if health == "live":
            switch = headless is False and st.get("mode") == "headless"
            if not switch:
                try:
                    pw, browser, page = _connect(st)
                    print(f"уже поднят: {page.url}")
                    pw.stop()
                    return
                except _CdpUnreachable:
                    if _state() != st:
                        # подтверждённый снимок сменился — решение о
                        # перезапуске относится к другому экземпляру
                        raise SystemExit(
                            "Состояние сессии изменилось между проверкой "
                            "и подключением — отменяю."
                        ) from None
                    # свой процесс жив, но CDP не отвечает — перезапускаем
            if not _stop_own_session(st):
                raise SystemExit("Своя сессия не завершилась. Закрой её: bsu-browser stop.")
        elif health == "ours":
            if not _stop_own_session(st):
                raise SystemExit("Своя сессия не завершилась. Закрой её: bsu-browser stop.")
        elif health in ("foreign", "unknown"):
            raise SystemExit(_FOREIGN_SESSION)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    args = [
        _chrome_path(),
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if os.environ.get("BSU_NO_PROXY"):  # локальный случай: битый env-прокси
        args.append("--no-proxy-server")
    env = None
    if headless is None:
        headless = True  # дефолт — без окна; --headed для наблюдения
    if not headless:
        env, extra = _headed_env_args()
        args += extra
    else:
        args.append("--headless=new")
    proc = subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env
    )
    # Без токена запуска ОС не подтверждает наш процесс: CDP не
    # используем, состояние не публикуем; гасим только своего дочернего.
    pstart = _proc_start_token(proc.pid)
    if pstart is None:
        proc.terminate()
        raise SystemExit(
            "ОС не подтвердила запуск Chrome — сессию не публикую."
        )
    _save_state(
        port=DEBUG_PORT, pid=proc.pid,
        mode="headless" if headless else "headed",
        pstart=pstart,
    )
    for _ in range(180):
        owner = _port_owner_pid(DEBUG_PORT)
        if owner is not None and owner != proc.pid:
            proc.terminate()
            raise SystemExit(_FOREIGN_SESSION)
        # к CDP подключаемся только когда ОС независимо подтверждает, что
        # порт слушает именно наш pid; без доказательства — ждём/отказ
        if owner == proc.pid:
            try:
                pw, browser, page = _connect_test()
                pw.stop()
                break
            except Exception:
                pass
        time.sleep(0.5)
    else:
        proc.terminate()
        raise SystemExit("Chrome не поднялся за 90 с")
    print(f"started pid={proc.pid} port={DEBUG_PORT} headless={headless}")


def _wayland_env() -> bool:
    """Хватает ли Wayland для headed-режима (Linux)."""
    if sys.platform != "linux":
        return False
    return bool(
        os.environ.get("WAYLAND_DISPLAY")
        or (Path("/run/user") / str(os.getuid()) / "wayland-0").exists()
    )


def _headed_env_args() -> tuple[dict, list[str]]:
    """env+args для окна. Win/Mac — ничего не надо (Chrome сам покажет окно).
    Linux — если env агента беднее, берём активную сессию пользователя."""
    env = os.environ.copy()
    args: list[str] = []
    if sys.platform != "linux" or env.get("DISPLAY"):
        return env, args
    rt = Path("/run/user") / str(os.getuid())
    # XWayland проверенно мапит окно; ozone-wayland в этой сессии окно не даёт
    for auth in rt.glob(".mutter-Xwaylandauth.*"):
        env["XAUTHORITY"] = str(auth)
        env["DISPLAY"] = ":1"
        if "DBUS_SESSION_BUS_ADDRESS" not in env and (rt / "bus").exists():
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={rt}/bus"
        return env, args
    if (rt / "wayland-0").exists():
        env["XDG_RUNTIME_DIR"] = str(rt)
        env["WAYLAND_DISPLAY"] = env.get("WAYLAND_DISPLAY") or "wayland-0"
        if "DBUS_SESSION_BUS_ADDRESS" not in env and (rt / "bus").exists():
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={rt}/bus"
        args.append("--ozone-platform=wayland")
        return env, args
    raise SystemExit(
        "Нет доступного экрана (DISPLAY/Wayland): видимый Chrome не запустить. "
        "Настройка и ручной ввод SMS требуют машины с экраном."
    )


def _connect_test():
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
    for ctx in browser.contexts:
        if ctx.pages:
            return pw, browser, ctx.pages[0]
    pw.stop()
    raise RuntimeError("CDP жив, но страниц ещё нет")


def cmd_stop() -> None:
    st = _state()
    health = _session_health(st)
    if health in ("live", "ours") and st.get("pid"):
        if not _stop_own_session(st):
            print(
                "Процесс перед остановкой подтвердить не удалось — не трогаю.",
                file=sys.stderr,
            )
    elif health in ("foreign", "unknown"):
        print(
            "Сессию нельзя подтвердить как собственную — процесс не останавливаю.",
            file=sys.stderr,
        )
    _save_state(port=None, pid=None)
    print("stopped")


def cmd_status() -> None:
    pw, browser, page = _connect()
    print(f"ok mode={_state().get('mode', '?')} url={page.url} title={page.title()}")
    pw.stop()


def cmd_navigate(url: str) -> None:
    pw, browser, page = _connect()
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(1500)
    print(page.url)
    pw.stop()


def cmd_snapshot() -> None:
    pw, browser, page = _connect()
    snap = page.evaluate(
        """() => {
          const vis = e => e.getBoundingClientRect().width > 0;
          const texts = sel => [...document.querySelectorAll(sel)].filter(vis)
            .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' ')).filter(Boolean).slice(0, 40);
          return {
            title: document.title,
            headings: texts('h1,h2,h3'),
            fields: [...document.querySelectorAll('input,textarea,select')].filter(vis)
              .map(e => `${e.type}: ${e.placeholder || e.name || e.id || '(без подписи)'}`).slice(0, 40),
            buttons: texts('button,input[type=submit],[role=button]'),
            links: texts('a[href]').slice(0, 30),
          };
        }"""
    )
    print(f"URL: {page.url}")
    print(f"Заголовок: {snap['title']}")
    for k in ("headings", "fields", "buttons", "links"):
        if snap[k]:
            print(f"{k}:")
            for t in snap[k]:
                print(f"  {t}")
    pw.stop()


def cmd_snapshot_plan() -> None:
    """Структурный снимок плана (read-only). Текстовый snapshot не считается
    снимком данных; нераспознанная страница → loaded=false, а не «пусто»."""
    from .api import collect_plan_raw
    from .plan import snapshot_document
    from .schema import dumps, exit_code

    pw, _, page = _connect()
    try:
        doc = snapshot_document(collect_plan_raw(page))
        print(dumps(doc))
        raise SystemExit(exit_code(doc))
    finally:
        pw.stop()


def cmd_fill(label: str, value: str) -> None:
    pw, browser, page = _connect()
    loc = page.get_by_label(label, exact=False)
    if loc.count() == 0:
        loc = page.locator(f'input[placeholder*="{label}" i], input[name*="{label}" i]')
    loc.first.fill(value)
    print("ok")
    pw.stop()


def cmd_fill_login() -> None:
    from .login import fill_login

    pw, _, page = _connect()
    try:
        fill_login(page)
        print("[credentials filled]")
    finally:
        pw.stop()


def cmd_login(adb_serial: str | None = None) -> None:
    from .creds import LOGIN_FILE
    from .login import login

    if adb_serial is None or not LOGIN_FILE.is_file():
        _ensure_headed()  # настройка и ручной SMS — только видимое окно
    else:
        _ensure_own_session(headless=True)
    if not LOGIN_FILE.is_file():
        cmd_setup()
    pw, _, current_page = _connect()
    try:
        page = current_page.context.new_page()
        page.bring_to_front()
        login(page, adb_serial=adb_serial, visible=_state().get("mode") == "headed")
        print("Вход в кабинет выполнен.")
    finally:
        pw.stop()


def login_command(args: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="bsu-browser login", allow_abbrev=False, exit_on_error=False)
    parser.add_argument("--sms", choices=("manual", "adb"), default="manual")
    parser.add_argument("--adb-serial", help="выбранный телефон; иначе ANDROID_SERIAL")
    try:
        options, extra = parser.parse_known_args(args)
    except argparse.ArgumentError:
        parser.error("Используй --sms manual или --sms adb с выбранным устройством.")
    if extra:
        parser.error("SMS-код вводится только в окне Chrome; см. login --help.")
    if options.sms == "manual":
        if options.adb_serial is not None:
            parser.error("--adb-serial допустим только с --sms adb")
        cmd_login()
        return
    serial = options.adb_serial if options.adb_serial is not None else os.environ.get("ANDROID_SERIAL", "")
    if not serial.strip():
        parser.error("для --sms adb укажи --adb-serial или ANDROID_SERIAL")
    cmd_login(serial.strip())


def cmd_creds(login: str) -> None:
    from .creds import prompt_and_save

    prompt_and_save(login)


def cmd_setup(timeout_s: int = 300) -> None:
    """Принимает секреты только из отдельной вкладки с локальной формой."""
    from .creds import CredentialError, save_credentials

    if timeout_s <= 0:
        raise SystemExit("Время ожидания настройки должно быть больше нуля.")
    _ensure_headed()
    pw, _, current_page = _connect()
    try:
        setup = current_page.context.new_page()
        try:
            with as_file(files("bsu_cabinet").joinpath("assets")) as assets:
                setup_url = (assets / "setup.html").resolve().as_uri()
                saved = False
                error = None

                def submit(source, login: str, password: str) -> bool:
                    nonlocal saved, error
                    if saved or error or source["frame"] != setup.main_frame or setup.url != setup_url:
                        return False
                    if not isinstance(login, str) or not isinstance(password, str):
                        return False
                    try:
                        save_credentials(login, password)
                    except CredentialError as exc:
                        error = exc
                        return False
                    saved = True
                    return True

                setup.expose_binding("bsuSaveCredentials", submit)
                setup.goto(setup_url, wait_until="domcontentloaded", timeout=15_000)
                setup.bring_to_front()
                print("В окне Chrome открыта форма настройки входа.", flush=True)
                setup.wait_for_function(
                    "() => document.getElementById('card')?.dataset.setupResult",
                    timeout=timeout_s * 1000,
                )
                if error:
                    raise SystemExit(str(error))
                if not saved:
                    raise SystemExit("Не удалось сохранить данные входа.")
                print("Данные входа сохранены в системном хранилище.")
        except PlaywrightTimeout as exc:
            raise SystemExit("Форма настройки не завершена вовремя.") from exc
        finally:
            setup.close()
    finally:
        pw.stop()


def cmd_click(text: str) -> None:
    pw, browser, page = _connect()
    loc = page.get_by_text(text, exact=True).first
    try:
        loc.click(timeout=10_000)
    except Exception:
        loc.evaluate("e => { e.scrollIntoView({block:'center'}); e.click(); }")
    print("ok")
    pw.stop()


def cmd_click_xy(x: float, y: float) -> None:
    pw, browser, page = _connect()
    page.mouse.click(x, y)
    print("ok")
    pw.stop()


def cmd_key(key: str) -> None:
    pw, browser, page = _connect()
    page.keyboard.press(key)
    print("ok")
    pw.stop()


def cmd_wait_text(text: str, timeout_ms: int = 15_000) -> None:
    pw, browser, page = _connect()
    page.wait_for_function(
        "t => document.body && document.body.innerText.includes(t)",
        arg=text,
        timeout=timeout_ms,
    )
    print("ok")
    pw.stop()


def cmd_wait_url_not(substr: str, timeout_ms: int = 15_000) -> None:
    pw, browser, page = _connect()
    page.wait_for_url(lambda url: substr not in url, timeout=timeout_ms)
    print(page.url)
    pw.stop()


def cmd_eval(js: str) -> None:
    pw, browser, page = _connect()
    res = page.evaluate(js)
    print(json.dumps(_jsonable(res), ensure_ascii=False))
    pw.stop()


def cmd_shot(name: str) -> None:
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    pw, browser, page = _connect()
    path = SHOTS_DIR / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)
    print(path)
    pw.stop()


def cmd_url() -> None:
    pw, browser, page = _connect()
    print(page.url)
    pw.stop()


def cmd_title() -> None:
    pw, browser, page = _connect()
    print(page.title())
    pw.stop()


def cmd_text() -> None:
    pw, browser, page = _connect()
    print(page.locator("body").inner_text())
    pw.stop()


USAGE = __doc__


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        sys.exit(0 if args else 2)
    cmd, rest = args[0], args[1:]
    if cmd == "start":
        cmd_start(headless=False if "--headed" in rest else None)
    elif cmd == "stop":
        cmd_stop()
    elif cmd == "status":
        cmd_status()
    elif cmd == "navigate":
        cmd_navigate(rest[0])
    elif cmd == "snapshot" and rest[:1] == ["plan"]:
        cmd_snapshot_plan()
    elif cmd == "snapshot":
        cmd_snapshot()
    elif cmd == "fill":
        cmd_fill(rest[0], rest[1])
    elif cmd == "fill-login":
        cmd_fill_login()
    elif cmd == "login":
        login_command(rest)
    elif cmd == "creds":
        cmd_creds(rest[0])
    elif cmd == "setup":
        cmd_setup(int(rest[0]) if rest else 300)
    elif cmd == "click":
        cmd_click(rest[0])
    elif cmd == "click-xy":
        cmd_click_xy(float(rest[0]), float(rest[1]))
    elif cmd == "key":
        cmd_key(rest[0])
    elif cmd == "confirm":
        cmd_click("Подтвердить")
    elif cmd == "wait-text":
        cmd_wait_text(rest[0], int(rest[1]) if len(rest) > 1 else 15_000)
    elif cmd == "wait-url-not":
        cmd_wait_url_not(rest[0])
    elif cmd == "eval":
        cmd_eval(rest[0])
    elif cmd == "shot":
        cmd_shot(rest[0])
    elif cmd == "url":
        cmd_url()
    elif cmd == "title":
        cmd_title()
    elif cmd == "text":
        cmd_text()
    else:
        print(f"Неизвестная команда: {cmd}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
