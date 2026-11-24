from unittest.mock import Mock

import pytest

from bsu_cabinet import browser_cli, creds, login, sms
from bsu_cabinet.browser import BASE_URL, LOGIN_URL


@pytest.mark.parametrize("url", [
    "https://outside.example/Login",
    "https://teacher-cabinet.bsu.by.outside.example/Login",
    "http://teacher-cabinet.bsu.by/Login",
    "https://teacher-cabinet.bsu.by:9443/Login",
    f"{BASE_URL}/ErrorAccess",
])
def test_fill_login_refuses_other_documents_before_keyring(monkeypatch, url):
    page = Mock(url=url)
    stop = Mock()
    monkeypatch.setattr(browser_cli, "_connect", lambda: (stop, Mock(), page))
    saved = Mock(return_value="fixture-login")
    password = Mock(return_value="fixture-password")
    monkeypatch.setattr(creds, "stored_login", saved)
    monkeypatch.setattr(creds, "get_password", password)

    with pytest.raises((RuntimeError, SystemExit), match="страниц"):
        browser_cli.cmd_fill_login()

    saved.assert_not_called()
    password.assert_not_called()
    page.locator.assert_not_called()
    stop.stop.assert_called_once()


def test_new_state_does_not_use_legacy_login(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy-login"
    legacy.write_text("legacy-test-user\n")
    monkeypatch.setattr(creds, "LOGIN_FILE", tmp_path / "new-login")
    monkeypatch.setattr(creds, "LEGACY_LOGIN_FILE", legacy, raising=False)
    with pytest.raises(RuntimeError, match="setup"):
        creds.stored_login()


def test_unsupported_keyring_does_not_read_or_write(monkeypatch, tmp_path):
    backend = Mock()
    monkeypatch.setattr(creds.keyring, "get_keyring", lambda: backend)
    for method in ("get_password", "set_password"):
        monkeypatch.setattr(creds.keyring, method, Mock(side_effect=AssertionError("Unexpected keyring")))
    monkeypatch.setattr(creds, "LOGIN_FILE", tmp_path / "login")
    with pytest.raises(RuntimeError, match="хранилищ"):
        creds.get_password("fixture-login")
    with pytest.raises(RuntimeError, match="хранилищ"):
        creds.save_credentials("fixture-login", "fixture-password")
    assert backend.mock_calls == []


def test_manual_login_waits_for_human_without_touching_adb(monkeypatch):
    page = Mock(url=LOGIN_URL)
    def confirm_in_browser(*_args, **_kwargs):
        page.url = f"{BASE_URL}/PersonalCabinet"

    page.wait_for_url.side_effect = confirm_in_browser
    monkeypatch.setattr(login, "fill_login", Mock(), raising=False)
    monkeypatch.setattr(login, "stored_login", lambda: "fixture-login")
    monkeypatch.setattr(login, "get_password", lambda _login: "fixture-password")
    monkeypatch.setattr(login, "wait_text", Mock())
    for name in ("adb_available", "wait_for_code"):
        monkeypatch.setattr(sms, name, Mock(side_effect=AssertionError("Unexpected ADB call")))

    assert login.login(page).endswith("/PersonalCabinet")
    sms.adb_available.assert_not_called()
    sms.wait_for_code.assert_not_called()


def test_login_does_not_accept_redirect_to_error_as_success(monkeypatch):
    page = Mock(url=f"{BASE_URL}/ErrorAccess")
    password = Mock()
    monkeypatch.setattr(creds, "get_password", password)
    with pytest.raises(RuntimeError, match="страниц"):
        login.login(page)
    password.assert_not_called()


def _session(state, monkeypatch, *, alive=True, own=True, port_open=True,
             port_owner="self", token="self", record_token=True,
             ss_out=None, lsof_out=None, missing_tools=False,
             ss_rc=0, lsof_rc=0, ss_err="", lsof_err=""):
    """Заглушки здоровья сессии: процесс, аргументы, запуск, владелец порта.

    port_owner="self" — порт слушает записанный pid; число — чужой pid;
    None — ОС владельца не даёт (тогда только отказ, без доверия).
    token="self" — токен запуска совпадает с записанным pstart; None —
    ОС доказательства не дала; другая строка — pid переиспользован.
    record_token=False имитирует session.json без pstart (старый формат).
    ss_out/lsof_out — дословная выдача ss/lsof: тогда настоящий
    _port_owner_pid разбирает подставные строки (None — команда недоступна);
    missing_tools=True — обе команды недоступны при настоящем разборе;
    ss_rc/lsof_rc — returncode и ss_err/lsof_err — stderr подставного
    CompletedProcess.
    """
    if "pid" in state and record_token:
        state.setdefault("pstart", "launch-tok")
    monkeypatch.setattr(browser_cli, "_state", lambda: dict(state))
    monkeypatch.setattr(browser_cli, "_pid_alive", lambda _pid: alive)
    monkeypatch.setattr(browser_cli, "_pid_is_own_chrome", lambda _pid: own)
    monkeypatch.setattr(
        browser_cli, "_proc_start_token",
        lambda _pid: state.get("pstart") if token == "self" else token,
    )
    monkeypatch.setattr(browser_cli, "_port_open", lambda _port: port_open)
    if ss_out is None and lsof_out is None and not missing_tools:
        monkeypatch.setattr(
            browser_cli, "_port_owner_pid",
            lambda _port: state.get("pid") if port_owner == "self" else port_owner,
        )
    else:
        def fake_run(argv, **_kwargs):
            if argv[0] == "ss":
                if ss_out is None:
                    raise FileNotFoundError("ss unavailable")
                return Mock(returncode=ss_rc, stdout=ss_out, stderr=ss_err)
            if argv[0] == "lsof":
                if lsof_out is None:
                    raise FileNotFoundError("lsof unavailable")
                return Mock(returncode=lsof_rc, stdout=lsof_out, stderr=lsof_err)
            raise AssertionError(f"unexpected command: {argv}")
        monkeypatch.setattr(browser_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(browser_cli, "_save_state", lambda **kw: state.update(kw))


def test_headless_setup_stops_before_opening_form(monkeypatch):
    _session({"port": 9377, "pid": 4242, "mode": "headless"}, monkeypatch, own=False)
    connect = Mock()
    monkeypatch.setattr(browser_cli, "_connect", connect)
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)
    with pytest.raises(SystemExit, match="BSU_PORT|чуж"):
        browser_cli.cmd_setup()
    connect.assert_not_called()
    kill.assert_not_called()


def test_login_switches_own_headless_to_headed_before_sms(monkeypatch, tmp_path):
    login_file = tmp_path / "login"
    login_file.write_text("fixture-login\n")
    monkeypatch.setattr(creds, "LOGIN_FILE", login_file)
    state = {"port": 9377, "pid": 4242, "mode": "headless"}
    _session(state, monkeypatch, alive=True, own=True, port_open=True)
    calls = []
    alive = {"pid": True}
    monkeypatch.setattr(browser_cli, "_pid_alive", lambda _pid: alive["pid"])
    monkeypatch.setattr(browser_cli, "_port_open", lambda _port: alive["pid"])

    def fake_kill(pid, sig):
        calls.append(("kill", pid, sig))
        alive["pid"] = False

    def fake_start(headless=None):
        calls.append(("start", headless))
        state.update(mode="headed", pid=7777, port=9377)
        alive["pid"] = True

    monkeypatch.setattr(browser_cli.os, "kill", fake_kill)
    monkeypatch.setattr(browser_cli, "cmd_start", fake_start)
    page = Mock(url=LOGIN_URL)
    monkeypatch.setattr(browser_cli, "_connect", lambda: (Mock(), Mock(), page))
    run = Mock(return_value=BASE_URL + "/PersonalCabinet")
    monkeypatch.setattr(login, "login", run)

    browser_cli.cmd_login()

    import signal

    assert calls == [("kill", 4242, signal.SIGTERM), ("start", False)]
    run.assert_called_once()
    assert run.call_args.kwargs["visible"] is True
    assert run.call_args.kwargs["adb_serial"] is None


def test_login_refuses_foreign_pid_without_killing_or_sms(monkeypatch, tmp_path):
    login_file = tmp_path / "login"
    login_file.write_text("fixture-login\n")
    monkeypatch.setattr(creds, "LOGIN_FILE", login_file)
    _session({"port": 9377, "pid": 4242, "mode": "headless"}, monkeypatch, own=False)
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)
    connect = Mock()
    monkeypatch.setattr(browser_cli, "_connect", connect)
    run = Mock()
    monkeypatch.setattr(login, "login", run)

    with pytest.raises(SystemExit, match="BSU_PORT|чуж"):
        browser_cli.cmd_login()

    kill.assert_not_called()
    connect.assert_not_called()
    run.assert_not_called()


def test_login_refuses_when_port_held_by_unknown_chrome(monkeypatch, tmp_path):
    login_file = tmp_path / "login"
    login_file.write_text("fixture-login\n")
    monkeypatch.setattr(creds, "LOGIN_FILE", login_file)
    # Записанный pid умер, но на порту кто-то слушает — чужой, не трогаем.
    _session({"port": 9377, "pid": 4242, "mode": "headless"}, monkeypatch, alive=False, port_open=True)
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)
    connect = Mock()
    monkeypatch.setattr(browser_cli, "_connect", connect)
    run = Mock()
    monkeypatch.setattr(login, "login", run)

    with pytest.raises(SystemExit, match="BSU_PORT|чуж"):
        browser_cli.cmd_login()

    kill.assert_not_called()
    connect.assert_not_called()
    run.assert_not_called()


def test_setup_without_display_refuses_before_connect(monkeypatch):
    _session({}, monkeypatch, port_open=False)

    def no_display(headless=None):
        raise SystemExit("Нет доступного экрана (DISPLAY/Wayland).")

    monkeypatch.setattr(browser_cli, "cmd_start", no_display)
    connect = Mock()
    monkeypatch.setattr(browser_cli, "_connect", connect)

    with pytest.raises(SystemExit, match="экран"):
        browser_cli.cmd_setup(5)

    connect.assert_not_called()


def test_setup_switches_own_headless_before_opening_form(monkeypatch):
    state = {"port": 9377, "pid": 4242, "mode": "headless"}
    _session(state, monkeypatch, alive=True, own=True, port_open=True)
    calls = []
    alive = {"pid": True}
    monkeypatch.setattr(browser_cli, "_pid_alive", lambda _pid: alive["pid"])
    monkeypatch.setattr(browser_cli, "_port_open", lambda _port: alive["pid"])

    def fake_kill(pid, sig):
        calls.append(("kill", pid))
        alive["pid"] = False

    def fake_start(headless=None):
        calls.append(("start", headless))
        state.update(mode="headed", pid=7777, port=9377)
        alive["pid"] = True

    monkeypatch.setattr(browser_cli.os, "kill", fake_kill)
    monkeypatch.setattr(browser_cli, "cmd_start", fake_start)
    setup_tab = Mock()
    original = Mock()
    original.context.new_page.return_value = setup_tab
    monkeypatch.setattr(browser_cli, "_connect", lambda: (Mock(), Mock(), original))

    # До формы дошли: таба создана; сохранение не подтверждено — отказ setup'а ожидаем.
    with pytest.raises(SystemExit, match="Не удалось"):
        browser_cli.cmd_setup(5)

    assert ("kill", 4242) in calls and ("start", False) in calls
    assert calls.index(("kill", 4242)) < calls.index(("start", False))
    setup_tab.close.assert_called_once()


def test_login_with_credentials_does_not_reopen_setup(monkeypatch, tmp_path):
    login_file = tmp_path / "login"
    login_file.write_text("fixture-login\n")
    monkeypatch.setattr(creds, "LOGIN_FILE", login_file)
    _session({"port": 9377, "pid": 4242, "mode": "headed"}, monkeypatch)
    setup = Mock()
    monkeypatch.setattr(browser_cli, "cmd_setup", setup)
    page = Mock(url=LOGIN_URL)
    monkeypatch.setattr(browser_cli, "_connect", lambda: (Mock(), Mock(), page))
    run = Mock(return_value=BASE_URL + "/PersonalCabinet")
    monkeypatch.setattr(login, "login", run)

    browser_cli.cmd_login()

    setup.assert_not_called()
    run.assert_called_once()
    assert run.call_args.kwargs["visible"] is True


def test_stop_does_not_kill_process_that_is_not_our_chrome(monkeypatch):
    _session({"port": 9377, "pid": 4242, "mode": "headless"}, monkeypatch, own=False)
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)

    browser_cli.cmd_stop()

    kill.assert_not_called()


def test_stop_does_not_kill_when_pid_unverifiable(monkeypatch):
    # cmdline прочитать не удалось — подтверждения нет, процесс не трогаем.
    _session({"port": 9377, "pid": 4242, "mode": "headless"}, monkeypatch, own=None)
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)

    browser_cli.cmd_stop()

    kill.assert_not_called()


def test_health_rejects_port_owned_by_another_process(monkeypatch, tmp_path):
    # Записанный pid 123 наш, но порт слушает чужой 456: «live» недопустим.
    login_file = tmp_path / "login"
    login_file.write_text("fixture-login\n")
    monkeypatch.setattr(creds, "LOGIN_FILE", login_file)
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch, port_owner=456)
    connect = Mock()
    monkeypatch.setattr(browser_cli, "_connect", connect)
    run = Mock()
    monkeypatch.setattr(login, "login", run)

    assert browser_cli._session_health() == "foreign"
    with pytest.raises(SystemExit):
        browser_cli.cmd_login()
    connect.assert_not_called()
    run.assert_not_called()


def test_connect_does_not_attach_to_cdp_of_unverified_session(monkeypatch):
    import playwright.sync_api as pw_api

    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch, port_owner=456)
    pw_start = Mock()
    monkeypatch.setattr(pw_api, "sync_playwright", pw_start)

    with pytest.raises(SystemExit):
        browser_cli._connect()

    pw_start.assert_not_called()


def test_unknown_owner_is_not_treated_as_own(monkeypatch):
    # ОС не показывает владельца слушателя — порт открыт, но неизвестно чей.
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             port_owner=None)
    assert browser_cli._session_health() == "unknown"
    with pytest.raises(SystemExit):
        browser_cli._connect()


def test_devtools_marker_alone_does_not_prove_port_owner(monkeypatch, tmp_path):
    # Подставленный файловый маркер: DevToolsActivePort называет порт, но
    # не доказывает владельца сокета — здоровье не «live», CDP закрыт.
    profile = tmp_path / "chrome-profile-cli"
    profile.mkdir()
    (profile / "DevToolsActivePort").write_text("9377\n/devtools/browser/x\n")
    monkeypatch.setattr(browser_cli, "PROFILE_DIR", profile)
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             port_owner=None, port_open=True)
    assert browser_cli._session_health() != "live"
    with pytest.raises(SystemExit):
        browser_cli._connect()


def test_health_ours_when_own_process_has_no_confirmed_port(monkeypatch):
    _session({"port": 9377, "pid": 123, "mode": "headless"}, monkeypatch,
             port_open=False, port_owner=None)
    assert browser_cli._session_health() == "ours"
    with pytest.raises(SystemExit):
        browser_cli._connect()


def test_pid_is_own_chrome_matches_exact_profile_argument(monkeypatch, tmp_path):
    profile = tmp_path / "chrome-profile-cli"
    monkeypatch.setattr(browser_cli, "PROFILE_DIR", profile)
    cases = [
        ([f"--user-data-dir={profile}"], True),
        (["--flag", f"--user-data-dir={profile}", "--headless=new"], True),
        ([f"--user-data-dir={profile}-other"], False),
        ([f"--user-data-dir={profile} extra"], False),
        (["--flag"], False),
    ]
    for args, expected in cases:
        monkeypatch.setattr(browser_cli, "_pid_args", lambda _pid, a=args: a)
        assert browser_cli._pid_is_own_chrome(1) is expected, args
    monkeypatch.setattr(browser_cli, "_pid_args", lambda _pid: None)
    assert browser_cli._pid_is_own_chrome(1) is None


def test_reused_pid_with_same_profile_args_is_foreign(monkeypatch):
    # Аргумент профиля совпадает, но запуск не наш: токен старта другой —
    # ни сигнала, ни CDP.
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             token="other-launch")
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)

    assert browser_cli._session_health() == "foreign"
    browser_cli.cmd_stop()
    kill.assert_not_called()
    with pytest.raises(SystemExit):
        browser_cli._connect()


def test_unproven_launch_identity_refuses_signal_and_connect(monkeypatch):
    # ОС не даёт токен запуска — идентичность не доказана: ни kill, ни CDP.
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch, token=None)
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)

    assert browser_cli._session_health() == "unknown"
    browser_cli.cmd_stop()
    kill.assert_not_called()
    with pytest.raises(SystemExit):
        browser_cli._connect()


def test_state_without_launch_token_is_unknown(monkeypatch):
    # session.json без pstart (старый/подделанный формат) — недоверенно.
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             record_token=False)
    assert browser_cli._session_health() == "unknown"
    with pytest.raises(SystemExit):
        browser_cli._connect()


def test_empty_state_does_not_connect(monkeypatch):
    _session({}, monkeypatch, port_open=False)
    assert browser_cli._session_health() == "dead"
    with pytest.raises(SystemExit):
        browser_cli._connect()


def test_dead_pid_with_foreign_listener_stops_without_connect(monkeypatch):
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             alive=False, port_open=True)
    assert browser_cli._session_health() == "foreign"
    with pytest.raises(SystemExit):
        browser_cli._connect()


def test_start_connects_only_after_port_owner_confirmed(monkeypatch, tmp_path):
    state = {}
    monkeypatch.setattr(browser_cli, "_state", lambda: dict(state))
    monkeypatch.setattr(browser_cli, "_save_state", lambda **kw: state.update(kw))
    monkeypatch.setattr(browser_cli, "_session_health", lambda *_a: "dead")
    monkeypatch.setattr(browser_cli, "_chrome_path", lambda: "/fake/chrome")
    monkeypatch.setattr(browser_cli, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(browser_cli, "_proc_start_token", lambda _pid: "tok")
    proc = Mock()
    proc.pid = 4242
    monkeypatch.setattr(browser_cli.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(browser_cli, "_port_owner_pid", lambda _p: proc.pid)
    pw = Mock()
    monkeypatch.setattr(browser_cli, "_connect_test", lambda: (pw, Mock(), Mock()))
    monkeypatch.setattr(browser_cli.time, "sleep", lambda _s: None)

    browser_cli.cmd_start()

    assert state["pid"] == 4242
    assert state["pstart"] == "tok"
    pw.stop.assert_called_once()


def test_start_does_not_connect_when_owner_undetectable(monkeypatch, tmp_path):
    state = {}
    monkeypatch.setattr(browser_cli, "_state", lambda: dict(state))
    monkeypatch.setattr(browser_cli, "_save_state", lambda **kw: state.update(kw))
    monkeypatch.setattr(browser_cli, "_session_health", lambda *_a: "dead")
    monkeypatch.setattr(browser_cli, "_chrome_path", lambda: "/fake/chrome")
    monkeypatch.setattr(browser_cli, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(browser_cli, "_proc_start_token", lambda _pid: "tok")
    proc = Mock()
    proc.pid = 4242
    monkeypatch.setattr(browser_cli.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(browser_cli, "_port_owner_pid", lambda _p: None)
    connect = Mock()
    monkeypatch.setattr(browser_cli, "_connect_test", connect)
    monkeypatch.setattr(browser_cli.time, "sleep", lambda _s: None)

    with pytest.raises(SystemExit, match="не поднялся"):
        browser_cli.cmd_start()

    connect.assert_not_called()
    proc.terminate.assert_called_once()


def test_connect_refuses_when_state_changes_before_attach(monkeypatch):
    # Подмена session.json между чтением адреса и проверкой: новый pid/порт
    # «подтверждены» — но подключаться к старому адресу нельзя.
    import playwright.sync_api as pw_api

    box = {"st": {"port": 9377, "pid": 123, "mode": "headed", "pstart": "t1"}}
    monkeypatch.setattr(browser_cli, "_state", lambda: dict(box["st"]))

    def swapped_health(st):
        box["st"] = {"port": 9400, "pid": 999, "mode": "headed", "pstart": "t2"}
        return "live"  # подменённое состояние «проверилось»
    monkeypatch.setattr(browser_cli, "_session_health", swapped_health)
    pw_start = Mock()
    monkeypatch.setattr(pw_api, "sync_playwright", pw_start)

    with pytest.raises(SystemExit):
        browser_cli._connect()

    pw_start.assert_not_called()


def test_stop_signals_only_the_verified_snapshot_pid(monkeypatch):
    # Файл подменён после проверки здоровья: сигнал должен уйти только в
    # проверенный снимок, а не в записанный туда чужой pid.
    state = {"port": 9377, "pid": 123, "mode": "headless", "pstart": "t1"}
    _session(state, monkeypatch)
    alive = {"v": True}
    monkeypatch.setattr(browser_cli, "_pid_alive", lambda _p: alive["v"])
    monkeypatch.setattr(browser_cli, "_port_open", lambda _p: alive["v"])
    killed = []

    def fake_kill(pid, sig):
        killed.append(pid)
        alive["v"] = False

    monkeypatch.setattr(browser_cli.os, "kill", fake_kill)
    orig = browser_cli._session_health

    def swap_after_check(st):
        health = orig(st)
        state["pid"] = 456  # метаданные подменены после подтверждения
        return health
    monkeypatch.setattr(browser_cli, "_session_health", swap_after_check)

    browser_cli.cmd_stop()

    assert killed == [123]


def test_signal_refused_when_process_identity_rotated(monkeypatch):
    # Между проверкой и сигналом pid переиспользовали: токен уже другой —
    # сигнала нет вообще.
    _session({"port": 9377, "pid": 123, "mode": "headless", "pstart": "t1"},
             monkeypatch, token="rotated")
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)
    snapshot = {"port": 9377, "pid": 123, "mode": "headless", "pstart": "t1"}

    assert browser_cli._stop_own_session(snapshot) is False
    kill.assert_not_called()


_SS_ROW = 'LISTEN 0 511 {addr}:9377 0.0.0.0:* users:(("{name}",pid={pid},fd=7))'
def _lsof_row(addr, name, pid):
    family = "IPv6" if addr.startswith("[") or ":" in addr else "IPv4"
    return f"{name:<7} {pid:>5} me    7u  {family} 222222      0t0  TCP {addr}:9377 (LISTEN)"


def _ss(*rows):
    return "\n".join(rows) + "\n"


def _lsof(*rows):
    return "COMMAND     PID   USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n" + "\n".join(rows) + "\n"


@pytest.mark.parametrize("other_addr", ["127.0.0.2", "[::1]", "10.0.0.5"])
@pytest.mark.parametrize("swap", [False, True])
def test_port_owner_bound_to_exact_cdp_address(monkeypatch, tmp_path, other_addr, swap):
    # Наш процесс слушает другой локальный адрес того же порта, а точный
    # CDP-адрес 127.0.0.1 держит чужой: в любом порядке строк отказ до
    # CDP и до сигнала — pid с другого адреса не доказывает владельца.
    login_file = tmp_path / "login"
    login_file.write_text("fixture-login\n")
    monkeypatch.setattr(creds, "LOGIN_FILE", login_file)
    ours = _SS_ROW.format(addr=other_addr, name="python", pid=123)
    foreign = _SS_ROW.format(addr="127.0.0.1", name="chrome", pid=456)
    rows = [foreign, ours] if swap else [ours, foreign]
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             ss_out=_ss(*rows))
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)
    import playwright.sync_api as pw_api
    pw_start = Mock()
    monkeypatch.setattr(pw_api, "sync_playwright", pw_start)
    run = Mock()
    monkeypatch.setattr(login, "login", run)

    assert browser_cli._session_health() == "foreign"
    browser_cli.cmd_stop()
    kill.assert_not_called()
    # cmd_stop очистил session.json — восстанавливаем ту же чужую картину.
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             ss_out=_ss(*rows))
    with pytest.raises(SystemExit):
        browser_cli.cmd_login()
    with pytest.raises(SystemExit):
        browser_cli._connect()
    pw_start.assert_not_called()
    run.assert_not_called()


@pytest.mark.parametrize("other_addr", ["127.0.0.2", "[::1]"])
@pytest.mark.parametrize("swap", [False, True])
def test_lsof_fallback_bound_to_exact_cdp_address(monkeypatch, other_addr, swap):
    # ss недоступен: владелец берётся из lsof по адресу в NAME, а не из
    # голого pid — строка с чужого локального адреса не переносится.
    ours = _lsof_row(other_addr, "python3", 123)
    foreign = _lsof_row("127.0.0.1", "chrome", 456)
    rows = [foreign, ours] if swap else [ours, foreign]
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             lsof_out=_lsof(*rows))
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)

    assert browser_cli._session_health() == "foreign"
    browser_cli.cmd_stop()
    kill.assert_not_called()
    # cmd_stop очистил session.json — восстанавливаем ту же чужую картину.
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             lsof_out=_lsof(*rows))
    with pytest.raises(SystemExit):
        browser_cli._connect()


def test_cdp_row_without_pid_is_not_own(monkeypatch):
    # На точном адресе слушатель есть, но его pid скрыт — строку нашего
    # процесса с другого адреса нельзя подставить вместо владельца CDP.
    rows = _ss(
        "LISTEN 0      511          127.0.0.1:9377       0.0.0.0:*",
        _SS_ROW.format(addr="127.0.0.2", name="python", pid=123),
    )
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch, ss_out=rows)
    assert browser_cli._session_health() == "unknown"
    with pytest.raises(SystemExit):
        browser_cli._connect()


_SS_HIDDEN_OWNER = "LISTEN 0 128 127.0.0.1:9377 0.0.0.0:*\n"


@pytest.mark.parametrize(("ss_rc", "ss_out", "ss_err"), [
    (0, _SS_HIDDEN_OWNER, ""),
    (-15, _SS_HIDDEN_OWNER, ""),
    (-15, "", "Output information may be incomplete.\n"),
], ids=["hidden-owner", "interrupted-output", "interrupted-diagnostic"])
def test_hidden_ss_owner_is_not_overridden_by_weaker_lsof(monkeypatch, ss_rc, ss_out, ss_err):
    lsof_out = _lsof(_lsof_row("127.0.0.1", "chrome", 123))
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             ss_out=ss_out, ss_rc=ss_rc, ss_err=ss_err, lsof_out=lsof_out, port_open=False)
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)
    monkeypatch.setattr("playwright.sync_api.sync_playwright",
                        Mock(side_effect=AssertionError("CDP при неподтверждённом владельце")))
    assert browser_cli._session_health() == "unknown"
    with pytest.raises(SystemExit, match="нельзя подтвердить"):
        browser_cli.cmd_status()
    browser_cli.cmd_stop()
    kill.assert_not_called()


def test_pidless_cdp_row_makes_owner_ambiguous(monkeypatch):
    # Две строки одного точного адреса: одна без pid — рядом с читаемым
    # pid скрывается ещё один слушатель → неоднозначно, не «live».
    rows = _ss(
        "LISTEN 0      128          127.0.0.1:9377       0.0.0.0:*",
        _SS_ROW.format(addr="127.0.0.1", name="a", pid=123),
    )
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch, ss_out=rows)
    assert browser_cli._session_health() == "unknown"
    with pytest.raises(SystemExit):
        browser_cli._connect()


def test_ipv6_wildcard_alone_does_not_prove_ipv4_cdp_owner(monkeypatch):
    # Единственная строка — IPv6 `*:port` в lsof: без доказательства
    # dual-stack это не владелец IPv4-адреса 127.0.0.1 → unknown.
    lsof_out = _lsof(
        "chrome    123 fixture    4u  IPv6   1234      0t0  TCP *:9377 (LISTEN)"
    )
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             lsof_out=lsof_out)
    assert browser_cli._session_health() == "unknown"
    with pytest.raises(SystemExit):
        browser_cli._connect()


@pytest.mark.parametrize("iface", ["eth0", "wlan0", "lo", "lo0"])
def test_interface_bound_listener_semantics(monkeypatch, iface):
    # Привязка к интерфейсу сохраняет семантику: применимость к точному
    # endpoint из имени не доказывается — ни %eth0, ни %lo; владельцем
    # считается только однозначный unscoped 127.0.0.1.
    row = (f'LISTEN 0      128          0.0.0.0%{iface}:19505  0.0.0.0:*   '
           'users:(("chrome",pid=123,fd=4))')
    _session({"port": 19505, "pid": 123, "mode": "headed"}, monkeypatch,
             ss_out=_ss(row), port_open=True)
    assert browser_cli._session_health() == "unknown"


def test_nonapplicable_ss_observation_is_not_weakened_by_lsof(monkeypatch):
    # ss завершил наблюдение и доказал неприменимость слушателя к
    # 127.0.0.1 (привязка к eth0). Грубая строка lsof `*:port` это
    # наблюдение не ослабляет: fallback не отменяет состоявшийся разбор.
    ss_out = _ss('LISTEN 0      128    0.0.0.0%eth0:19505       0.0.0.0:*   '
                 'users:(("chrome",pid=123,fd=4))')
    lsof_out = _lsof(
        "chrome    123 fixture    4u  IPv4   1234      0t0  TCP *:19505 (LISTEN)"
    )
    _session({"port": 19505, "pid": 123, "mode": "headed"}, monkeypatch,
             ss_out=ss_out, lsof_out=lsof_out, port_open=True)
    import playwright.sync_api as pw_api
    pw_start = Mock()
    monkeypatch.setattr(pw_api, "sync_playwright", pw_start)
    assert browser_cli._session_health() != "live"
    with pytest.raises(SystemExit):
        browser_cli._connect()
    pw_start.assert_not_called()


def test_unavailable_tools_are_not_observed_absence(monkeypatch):
    # ss и lsof не запускаются, probe порта не прошёл: наблюдение не
    # состоялось — недоступный источник ≠ доказанное отсутствие.
    # Не «ours», без сигнала и без CDP.
    _session({"port": 19505, "pid": 123, "mode": "headed"}, monkeypatch,
             missing_tools=True, port_open=False)
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)
    assert browser_cli._session_health() == "unknown"
    browser_cli.cmd_stop()
    kill.assert_not_called()
    with pytest.raises(SystemExit):
        browser_cli._connect()


def test_unambiguous_lsof_exact_owner_when_ss_missing(monkeypatch):
    # ss недоступен, но lsof даёт однозначного владельца точного
    # 127.0.0.1 — проверенный точный IP остаётся доказательством.
    lsof_out = _lsof(
        "chrome    123 fixture    4u  IPv4   1234      0t0  TCP 127.0.0.1:9377 (LISTEN)"
    )
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             lsof_out=lsof_out)
    assert browser_cli._session_health() == "live"


def test_reliably_observed_absence_allows_ours(monkeypatch):
    # ss отработал и показал: слушателей порта нет вообще — доказанное
    # отсутствие, а не неизвестность; probe закрыт → «ours» допустим.
    _session({"port": 9377, "pid": 123, "mode": "headless"}, monkeypatch,
             ss_out="", port_open=False)
    assert browser_cli._session_health() == "ours"


def test_hidden_owner_with_failed_probe_stays_unknown(monkeypatch):
    # ss видит слушателя точного адреса, но pid скрыт, а TCP-probe порта
    # не прошёл: неудачный probe неизвестность не снимает — не «ours»,
    # сигнала нет, CDP закрыт.
    ss_out = "LISTEN 0      128          127.0.0.1:19505       0.0.0.0:*\n"
    _session({"port": 19505, "pid": 123, "mode": "headed"}, monkeypatch,
             ss_out=ss_out, port_open=False)
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)
    assert browser_cli._session_health() == "unknown"
    browser_cli.cmd_stop()
    kill.assert_not_called()
    with pytest.raises(SystemExit):
        browser_cli._connect()


def test_shared_cdp_address_is_ambiguous_not_own(monkeypatch):
    # Два pid на точном адресе (SO_REUSEPORT): неоднозначность — не «live».
    rows = _ss(
        _SS_ROW.format(addr="127.0.0.1", name="a", pid=123),
        _SS_ROW.format(addr="127.0.0.1", name="b", pid=456),
    )
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch, ss_out=rows)
    assert browser_cli._session_health() == "unknown"
    with pytest.raises(SystemExit):
        browser_cli._connect()


def test_own_listener_on_other_address_alone_is_not_cdp_proof(monkeypatch):
    # Наш pid виден только на другом локальном адресе того же порта:
    # владелец 127.0.0.1 неизвестен → unknown, а не live.
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             ss_out=_ss(_SS_ROW.format(addr="127.0.0.2", name="python", pid=123)))
    assert browser_cli._session_health() == "unknown"
    with pytest.raises(SystemExit):
        browser_cli._connect()


_MAYBE_SS_ROWS = [
    'LISTEN 0      128              [::]:19505       0.0.0.0:*   users:(("chrome",pid={pid},fd=4))',
    'LISTEN 0      128       0.0.0.0%eth0:19505       0.0.0.0:*   users:(("chrome",pid={pid},fd=4))',
    'LISTEN 0      128          0.0.0.0%lo:19505      0.0.0.0:*   users:(("chrome",pid={pid},fd=4))',
    'LISTEN 0      128            0.0.0.0:19505       0.0.0.0:*   users:(("chrome",pid={pid},fd=4))',
    'LISTEN 0      128                *:19505        0.0.0.0:*   users:(("chrome",pid={pid},fd=4))',
]


@pytest.mark.parametrize("maybe", _MAYBE_SS_ROWS)
@pytest.mark.parametrize("probe", [True, False])
def test_maybe_applicable_listener_is_unknown_at_any_probe(monkeypatch, maybe, probe):
    # Строка, которая может обслуживать 127.0.0.1, но это недоказуемо
    # (wildcard любого семейства, привязка к интерфейсу) — потенциальный
    # слушатель: unknown при любом TCP-probe, без сигнала и без CDP.
    _session({"port": 19505, "pid": 123, "mode": "headed"}, monkeypatch,
             ss_out=_ss(maybe.format(pid=123)), port_open=probe)
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)
    import playwright.sync_api as pw_api
    pw_start = Mock()
    monkeypatch.setattr(pw_api, "sync_playwright", pw_start)
    assert browser_cli._session_health() == "unknown"
    browser_cli.cmd_stop()
    kill.assert_not_called()
    with pytest.raises(SystemExit):
        browser_cli._connect()
    pw_start.assert_not_called()


@pytest.mark.parametrize("maybe", _MAYBE_SS_ROWS)
def test_maybe_row_next_to_known_pid_is_still_unknown(monkeypatch, maybe):
    # Потенциально применимая строка рядом с доказанным pid точного
    # адреса — владелец endpoint неоднозначен → unknown, не live.
    rows = _ss(
        _SS_ROW.format(addr="127.0.0.1", name="chrome", pid=123).replace(":9377", ":19505"),
        maybe.format(pid=456),
    )
    _session({"port": 19505, "pid": 123, "mode": "headed"}, monkeypatch, ss_out=rows)
    assert browser_cli._session_health() == "unknown"
    with pytest.raises(SystemExit):
        browser_cli._connect()


@pytest.mark.parametrize(("stdout", "stderr"), [
    ("", "Output information may be incomplete.\n"),
    ("COMMAND PID USER FD\n", ""),
    (_lsof(), ""),
    ("", ""),
], ids=["warning", "truncated-header", "header-only", "empty"])
def test_incomplete_lsof_output_is_not_reliable_absence(monkeypatch, capsys, stdout, stderr):
    _session({"port": 19505, "pid": 123, "mode": "headed"}, monkeypatch,
             lsof_out=stdout, lsof_err=stderr, port_open=False)
    unexpected = Mock(side_effect=AssertionError("Действие без подтверждённого владельца"))
    monkeypatch.setattr(browser_cli.os, "kill", unexpected)
    monkeypatch.setattr("playwright.sync_api.sync_playwright", unexpected)

    with pytest.raises(SystemExit, match="нельзя подтвердить"):
        browser_cli.cmd_status()
    browser_cli.cmd_stop()
    assert "процесс не останавливаю" in capsys.readouterr().err


@pytest.mark.parametrize("truncated", [
    "LISTEN 0      128              [::]:195",       # обрыв адреса того же порта
    "LISTEN 0      128              [::]",           # обрыв без порта
    "LISTEN 0      128",                             # обрыв до адреса
    "LISTEN 0 128 127.0.0.2:19505",
    "LISTEN 0 128 127.0.:19505 0.0.0.0:*",
    'LISTEN 0 128 127.0.0.1:19505 0.0.0.0:* users:(("chrome",pid=123',
    'LISTEN 0 128 127.0.0.2:19505 0.0.0.0:* users:(("chrome",pid=123',
    'LISTEN 0 128 127.0.0.1:19505 0.0.:* users:(("chrome",pid=123,fd=7))',
    'LISTEN 0 128 [[127.0.0.1]]:19505 0.0.0.0:* users:(("chrome",pid=123,fd=7))',
    'LISTEN 0 128 [[::ffff:127.0.0.1]]:19505 0.0.0.0:* users:(("chrome",pid=123,fd=7))',
])
@pytest.mark.parametrize("with_exact", [False, True], ids=["alone", "with-exact"])
def test_truncated_ss_row_makes_observation_unknown(monkeypatch, truncated, with_exact):
    rows = [truncated]
    if with_exact:
        rows.insert(0, _SS_ROW.format(addr="127.0.0.1", name="chrome", pid=123).replace(":9377", ":19505"))
    _session({"port": 19505, "pid": 123, "mode": "headed"}, monkeypatch,
             ss_out=_ss(*rows), port_open=False)
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)
    assert browser_cli._session_health() == "unknown"
    browser_cli.cmd_stop()
    kill.assert_not_called()
    with pytest.raises(SystemExit):
        browser_cli._connect()


@pytest.mark.parametrize("name_field", ["garbage-no-endpoint", "127.0.0.1:9377 (LIST"])
def test_garbled_lsof_row_next_to_positive_is_unknown(monkeypatch, name_field):
    lsof_out = _lsof(
        "chrome    123 fixture    4u  IPv4   1234      0t0  TCP 127.0.0.1:9377 (LISTEN)",
        f"chrome    123 fixture    5u  IPv4   2345      0t0  TCP {name_field}",
    )
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             lsof_out=lsof_out)
    assert browser_cli._session_health() == "unknown"
    with pytest.raises(SystemExit):
        browser_cli._connect()


def test_start_does_not_stop_when_trust_check_fails_in_connect(monkeypatch):
    # cmd_start: здоровье live, повторная оценка внутри _connect —
    # unknown. Отказ на границе доверия не даёт остановить сессию по
    # устаревшему live: ни сигнала, ни CDP, ни нового запуска.
    calls = {"n": 0}

    def health(_st=None):
        calls["n"] += 1
        return "live" if calls["n"] == 1 else "unknown"

    st = {"port": 9377, "pid": 4242, "mode": "headless", "pstart": "t1"}
    monkeypatch.setattr(browser_cli, "_state", lambda: dict(st))
    monkeypatch.setattr(browser_cli, "_session_health", health)
    monkeypatch.setattr(browser_cli, "_save_state", lambda **kw: st.update(kw))
    stop = Mock(return_value=True)
    monkeypatch.setattr(browser_cli, "_stop_own_session", stop)
    popen = Mock(return_value=Mock(pid=4243))
    monkeypatch.setattr(browser_cli.subprocess, "Popen", popen)
    # на случай старого кода: запуск прошёл — владелец порта чужой,
    # цикл ожидания завершится отказом, а не 90-секундным poll'ом
    monkeypatch.setattr(browser_cli, "_port_owner_pid", lambda _p: 999)
    monkeypatch.setattr(browser_cli, "_port_open", lambda _p: True)
    monkeypatch.setattr(browser_cli.time, "sleep", lambda _s: None)
    import playwright.sync_api as pw_api
    pw_start = Mock()
    monkeypatch.setattr(pw_api, "sync_playwright", pw_start)

    with pytest.raises(SystemExit):
        browser_cli.cmd_start()

    stop.assert_not_called()
    popen.assert_not_called()
    pw_start.assert_not_called()


def test_start_refuses_cdp_when_launch_token_unavailable(monkeypatch, tmp_path):
    # ОС не дала токен запуска для только что созданного процесса:
    # без подтверждения собственного запуска ни CDP, ни публикации
    # состояния; допустима только остановка своего дочернего процесса.
    state = {}
    monkeypatch.setattr(browser_cli, "_state", lambda: dict(state))
    monkeypatch.setattr(browser_cli, "_save_state", lambda **kw: state.update(kw))
    monkeypatch.setattr(browser_cli, "_session_health", lambda *_a: "dead")
    monkeypatch.setattr(browser_cli, "_chrome_path", lambda: "/fake/chrome")
    monkeypatch.setattr(browser_cli, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(browser_cli, "_proc_start_token", lambda _pid: None)
    proc = Mock()
    proc.pid = 4242
    monkeypatch.setattr(browser_cli.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(browser_cli, "_port_owner_pid", lambda _p: proc.pid)
    connect = Mock()
    monkeypatch.setattr(browser_cli, "_connect_test", connect)
    monkeypatch.setattr(browser_cli.time, "sleep", lambda _s: None)

    with pytest.raises(SystemExit):
        browser_cli.cmd_start()

    connect.assert_not_called()
    proc.terminate.assert_called_once()
    assert not state.get("pid") and not state.get("port")


_LSOF_BAD_ROWS = [
    "COMMAND PID USER FD",
    "chrome    456 fixture    7u  ???   222222      0t0  TCP 127.0.0.2:19505 (LISTEN)",
    "chrome    456 fixture    7u  IPv4  222222      0t0  UDP 127.0.0.2:19505 (LISTEN)",
    "chrome    456 fixture    7u  IPv4  222222      0t0  TCP 127.0.0.2:19505",
]


@pytest.mark.parametrize("bad_row", _LSOF_BAD_ROWS)
@pytest.mark.parametrize("probe", [True, False])
def test_unverified_lsof_row_is_unknown_at_any_probe(monkeypatch, bad_row, probe):
    # Структура записи не подтверждена (семейство вне IPv4/IPv6, не TCP
    # или нет маркера LISTEN): адрес не классифицируется → unknown при
    # любом probe, без сигнала и CDP, а не доказанное отсутствие.
    _session({"port": 19505, "pid": 123, "mode": "headed"}, monkeypatch,
             lsof_out=_lsof(bad_row), port_open=probe)
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)
    import playwright.sync_api as pw_api
    pw_start = Mock()
    monkeypatch.setattr(pw_api, "sync_playwright", pw_start)
    assert browser_cli._session_health() == "unknown"
    browser_cli.cmd_stop()
    kill.assert_not_called()
    with pytest.raises(SystemExit):
        browser_cli._connect()
    pw_start.assert_not_called()


@pytest.mark.parametrize("bad_row", _LSOF_BAD_ROWS)
def test_unverified_lsof_row_next_to_positive_is_unknown(monkeypatch, bad_row):
    # Неверифицированная запись рядом с валидной точной строкой делает
    # выдачу недостоверной → unknown, не live.
    lsof_out = _lsof(
        "chrome    123 fixture    4u  IPv4   1234      0t0  TCP 127.0.0.1:19505 (LISTEN)",
        bad_row,
    )
    _session({"port": 19505, "pid": 123, "mode": "headed"}, monkeypatch,
             lsof_out=lsof_out)
    assert browser_cli._session_health() == "unknown"
    with pytest.raises(SystemExit):
        browser_cli._connect()


def test_interrupted_lsof_is_not_observed_absence(monkeypatch):
    # lsof прерван сигналом (returncode=-15, пустой вывод), ss недоступен:
    # ненулевой rc — не доказательство отсутствия, наблюдение не
    # состоялось → unknown, без сигнала и CDP даже при закрытом probe.
    _session({"port": 19505, "pid": 123, "mode": "headed"}, monkeypatch,
             lsof_out="", lsof_rc=-15, port_open=False)
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)
    import playwright.sync_api as pw_api
    pw_start = Mock()
    monkeypatch.setattr(pw_api, "sync_playwright", pw_start)
    assert browser_cli._session_health() == "unknown"
    browser_cli.cmd_stop()
    kill.assert_not_called()
    with pytest.raises(SystemExit):
        browser_cli._connect()
    pw_start.assert_not_called()


def test_wildcard_listener_answers_cdp_and_counts_as_owner(monkeypatch):
    # 0.0.0.0:port потенциально обслуживает 127.0.0.1, но по контракту
    # владельцем доказан только точный адрес — wildcard → unknown,
    # CDP по-прежнему закрыт.
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             ss_out=_ss(_SS_ROW.format(addr="0.0.0.0", name="chrome", pid=456)))
    assert browser_cli._session_health() == "unknown"
    with pytest.raises(SystemExit):
        browser_cli._connect()


def test_exact_cdp_listener_confirms_own_session(monkeypatch):
    # Однозначный слушатель 127.0.0.1:port с записанным pid — путь к live.
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             ss_out=_ss(_SS_ROW.format(addr="127.0.0.1", name="chrome", pid=123)))
    assert browser_cli._session_health() == "live"


def test_harness_paths_point_at_isolated_dirs():
    import os

    from bsu_cabinet import browser

    state = os.environ["BSU_STATE_DIR"]
    assert str(browser_cli.STATE_DIR) == state
    assert str(browser_cli.PROFILE_DIR).startswith(state + "/")
    assert str(creds.LOGIN_FILE).startswith(state + "/")
    assert browser_cli.DEBUG_PORT == int(os.environ["BSU_PORT"])
    assert str(browser.STATE_DIR).startswith(os.environ["HOME"] + "/")


def test_adb_mode_requires_selected_device(monkeypatch, capsys):
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    monkeypatch.setattr(browser_cli.sys, "argv", ["bsu-browser", "login", "--sms", "adb"])
    connect = Mock()
    monkeypatch.setattr(browser_cli, "_connect", connect)
    with pytest.raises(SystemExit) as exc:
        browser_cli.main()
    assert exc.value.code == 2
    assert "ANDROID_SERIAL" in capsys.readouterr().err
    connect.assert_not_called()


def test_cli_manual_mode_does_not_use_saved_phone(monkeypatch):
    launch = Mock()
    monkeypatch.setenv("ANDROID_SERIAL", "paired-fixture")
    monkeypatch.setattr(browser_cli, "cmd_login", launch)
    browser_cli.login_command([])
    launch.assert_called_once_with()


def test_cli_rejects_token_argument_without_echoing_it(capsys):
    with pytest.raises(SystemExit) as exc:
        browser_cli.login_command(["1234"])
    assert exc.value.code == 2
    assert "1234" not in capsys.readouterr().err


def test_manual_headless_refuses_before_filling_or_sending_sms(monkeypatch):
    page = Mock(url=LOGIN_URL)
    fill = Mock()
    monkeypatch.setattr(login, "fill_login", fill)
    with pytest.raises(RuntimeError, match="--headed"):
        login.login(page, visible=False)
    fill.assert_not_called()
    page.get_by_text.assert_not_called()


def test_missing_credentials_opens_setup_before_login(monkeypatch, tmp_path):
    monkeypatch.setattr(creds, "LOGIN_FILE", tmp_path / "login")
    _session({"port": 9377, "pid": 4242, "mode": "headed"}, monkeypatch)
    original = Mock()
    monkeypatch.setattr(browser_cli, "_connect", lambda: (Mock(), Mock(), original))
    calls = Mock()
    monkeypatch.setattr(browser_cli, "cmd_setup", calls.setup)
    monkeypatch.setattr(login, "login", calls.login)

    browser_cli.cmd_login()

    assert [call[0] for call in calls.mock_calls] == ["setup", "login"]
    calls.login.assert_called_once_with(original.context.new_page.return_value, adb_serial=None, visible=True)


def test_selected_phone_is_not_replaced_by_another_device(monkeypatch):
    run = Mock(return_value=Mock(returncode=1, stdout="offline"))
    monkeypatch.setattr(sms, "_run", run)
    assert not sms.adb_available("paired-fixture")
    run.assert_called_once_with("-s", "paired-fixture", "get-state", timeout=5)


def test_adb_failure_after_submit_keeps_manual_dialog_without_resending(monkeypatch):
    page = Mock(url=LOGIN_URL)
    page.wait_for_url.side_effect = lambda *_a, **_kw: setattr(page, "url", BASE_URL + "/PersonalCabinet")
    monkeypatch.setattr(login, "fill_login", Mock())
    monkeypatch.setattr(login, "wait_text", Mock())
    monkeypatch.setattr(sms, "adb_available", lambda _serial: True)
    monkeypatch.setattr(sms, "wait_for_code", Mock(side_effect=sms.SmsUnavailableError("offline")))
    monkeypatch.setattr(login, "enter_sms_code", Mock())

    login.login(page, adb_serial="paired-fixture", visible=True)

    page.get_by_text.return_value.first.click.assert_called_once()
    login.enter_sms_code.assert_not_called()


# --- Матрица наблюдения/доверия: группа 1 (прерванная команда) ---

@pytest.mark.parametrize("rc", [-15, -9])
def test_interrupted_ss_does_not_fall_back_to_lsof(monkeypatch, rc):
    # ss убит сигналом (отрицательный rc, вывода нет): наблюдение
    # прервано — fallback на точную строку lsof его не достраивает,
    # владелец остаётся unknown. Отличается от OSError «команды нет».
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             ss_out="", ss_rc=rc,
             lsof_out=_lsof(_lsof_row("127.0.0.1", "chrome", 123)))
    assert browser_cli._session_health() == "unknown"
    with pytest.raises(SystemExit):
        browser_cli._connect()


@pytest.mark.parametrize("failure", [
    browser_cli.subprocess.TimeoutExpired("ss", 5, output=b"LISTEN 0 128 127.0.0.1:9377"),
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
], ids=["timeout", "undecodable-output"])
def test_failed_ss_capture_does_not_fall_back(monkeypatch, failure):
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             missing_tools=True, port_open=False)
    run = Mock(side_effect=[failure, AssertionError("Нет основания переходить к lsof")])
    monkeypatch.setattr(browser_cli.subprocess, "run", run)
    assert browser_cli._session_health() == "unknown"
    assert run.call_count == 1


# --- группа 2 (структура lsof-записи) ---

def test_lsof_row_without_header_is_untrusted(monkeypatch):
    # Строка без штатного заголовка — выдача недостоверна, точный pid
    # из неё не доказывает владельца.
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             ss_out=None,
             lsof_out="chrome    123 me    7u  IPv4 222222      0t0  TCP 127.0.0.1:9377 (LISTEN)\n")
    assert browser_cli._session_health() == "unknown"
    with pytest.raises(SystemExit):
        browser_cli._connect()


@pytest.mark.parametrize("probe", [True, False])
def test_lsof_invalid_fd_field_is_untrusted(monkeypatch, probe):
    # Поле FD не в документированном виде <цифры><r|w|u>: запись не
    # классифицируется даже как посторонняя — unknown, не absent.
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             ss_out=None, port_open=probe,
             lsof_out=_lsof("chrome    456 me not-a-fd  IPv4 222222      0t0  TCP 127.0.0.2:9377 (LISTEN)"))
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)
    assert browser_cli._session_health() == "unknown"
    browser_cli.cmd_stop()
    kill.assert_not_called()


@pytest.mark.parametrize("family,addr", [
    ("IPv4", "[::ffff:127.0.0.1]"),   # семейство IPv4, адрес — IPv6-литерал
    ("IPv6", "127.0.0.1"),            # семейство IPv6, адрес — IPv4
])
def test_lsof_family_mismatch_is_untrusted(monkeypatch, family, addr):
    # Поле TYPE противоречит форме адреса в NAME — запись недостоверна.
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             ss_out=None,
             lsof_out=_lsof(f"chrome    123 me    7u  {family} 222222      0t0  TCP {addr}:9377 (LISTEN)"))
    assert browser_cli._session_health() == "unknown"
    with pytest.raises(SystemExit):
        browser_cli._connect()


# --- группа 3 (ASCII-числа, диапазон порта, wildcard только на месте) ---

@pytest.mark.parametrize("peer", ["0.0.0.0:99999", "127.0.0.2%eth0:*"])
def test_ss_peer_must_be_wildcard(monkeypatch, peer):
    row = f'LISTEN 0 511 127.0.0.1:9377 {peer} users:(("chrome",pid=123,fd=7))'
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             ss_out=_ss(row))
    assert browser_cli._session_health() == "unknown"
    with pytest.raises(SystemExit):
        browser_cli._connect()


@pytest.mark.parametrize("tool,row", [
    ("ss", 'LISTEN 0 511 127.0.0.1:９３７７ 0.0.0.0:* users:(("chrome",pid=123,fd=7))'),
    ("lsof", "chrome    123 me    7u  IPv4 222222      0t0  TCP 127.0.0.1:９３７７ (LISTEN)"),
    ("lsof", "chrome    123 me    7u  IPv4 222222      0t0  TCP 127.0.0.1:² (LISTEN)"),
    ("lsof", "chrome    123 me    7u  IPv4 222222      0t0  TCP 127.0.0.1:99999 (LISTEN)"),
    pytest.param("ss", _SS_ROW.format(addr="127.0.0.1", name="chrome", pid=123)
                 .replace(":9377", ":" + "9" * 5000), id="oversized-port"),
    ("lsof", "chrome    １２３ me    7u  IPv4 222222      0t0  TCP 127.0.0.1:9377 (LISTEN)"),
    ("lsof", "chrome    ² me    7u  IPv4 222222      0t0  TCP 127.0.0.1:9377 (LISTEN)"),
    ("ss", 'LISTEN 0 511 127.0.0.1:9377 0.0.0.0:* users:(("chrome",pid=１２３,fd=7))'),
    pytest.param("ss", _SS_ROW.format(addr="127.0.0.1", name="chrome", pid="9" * 5000),
                 id="oversized-ss-pid"),
    pytest.param("lsof", _lsof_row("127.0.0.1", "chrome", "9" * 5000), id="oversized-lsof-pid"),
    pytest.param("ss", _SS_ROW.format(addr="127.0.0.1", name="chrome", pid=123)
                 .replace("fd=7", "fd=" + "9" * 5000), id="oversized-ss-fd"),
    pytest.param("lsof", _lsof_row("127.0.0.1", "chrome", 123).replace("7u", "9" * 5000 + "u"),
                 id="oversized-lsof-fd"),
])
def test_invalid_numeric_fields_are_untrusted(monkeypatch, tool, row):
    kwargs = {"ss_out": _ss(row)} if tool == "ss" else {"ss_out": None, "lsof_out": _lsof(row)}
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch, **kwargs)
    assert browser_cli._session_health() == "unknown"


# --- группа 4 (снимок состояния един внутри команды) ---

def test_start_refuses_when_snapshot_changes_before_connect(monkeypatch):
    # cmd_start проверил снимок A (live), но к вызову _connect состояние
    # сменилось на B: отказ до CDP и до остановки — подтверждение B не
    # разрешает действия по A.
    a = {"port": 9377, "pid": 4242, "mode": "headed", "pstart": "tok-a"}
    b = {"port": 9377, "pid": 5555, "mode": "headed", "pstart": "tok-b"}
    monkeypatch.setattr(browser_cli, "_state", Mock(side_effect=[a, b, b, b, b]))
    monkeypatch.setattr(browser_cli, "_session_health", lambda *_a: "live")
    import playwright.sync_api as pw_api
    pw = Mock()
    pw.chromium.connect_over_cdp.side_effect = Exception("transport down")
    pw_start = Mock(return_value=pw)
    monkeypatch.setattr(pw_api, "sync_playwright", pw_start)
    stop = Mock(return_value=True)
    monkeypatch.setattr(browser_cli, "_stop_own_session", stop)
    popen = Mock()
    monkeypatch.setattr(browser_cli.subprocess, "Popen", popen)

    with pytest.raises(SystemExit):
        browser_cli.cmd_start()

    stop.assert_not_called()
    popen.assert_not_called()
    pw_start.assert_not_called()


def test_start_restart_same_snapshot_after_transport_failure(monkeypatch, tmp_path):
    # Позитив: тот же снимок, транспорт CDP упал — перезапуск своей
    # подтверждённой сессии допустим (stop + новый запуск).
    a = {"port": 9377, "pid": 4242, "mode": "headed", "pstart": "tok-a"}
    monkeypatch.setattr(browser_cli, "_state", lambda: dict(a))
    monkeypatch.setattr(browser_cli, "_session_health", lambda *_a: "live")
    import playwright.sync_api as pw_api
    pw = Mock()
    pw.start.return_value.chromium.connect_over_cdp.side_effect = Exception("transport down")
    monkeypatch.setattr(pw_api, "sync_playwright", Mock(return_value=pw))
    stop = Mock(return_value=True)
    monkeypatch.setattr(browser_cli, "_stop_own_session", stop)
    monkeypatch.setattr(browser_cli, "_chrome_path", lambda: "/fake/chrome")
    monkeypatch.setattr(browser_cli, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(browser_cli, "_proc_start_token", lambda _pid: "tok-b")
    proc = Mock()
    proc.pid = 5555
    monkeypatch.setattr(browser_cli.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(browser_cli, "_port_owner_pid", lambda _p: proc.pid)
    monkeypatch.setattr(browser_cli, "_connect_test", lambda: (Mock(), Mock(), Mock()))
    monkeypatch.setattr(browser_cli.time, "sleep", lambda _s: None)

    browser_cli.cmd_start()

    stop.assert_called_once_with(a)
    pw.start.return_value.chromium.connect_over_cdp.assert_called_once()


# --- группа 5 (записанная сессия без порта — неизвестность) ---

def test_recorded_pid_without_port_is_unknown(monkeypatch):
    # pid+pstart без port в session.json — доказанный процесс, но
    # endpoint не записан: неизвестность, а не отсутствие; сигнала нет.
    _session({"pid": 4242, "mode": "headed"}, monkeypatch, port_open=False)
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)
    import playwright.sync_api as pw_api
    pw_start = Mock()
    monkeypatch.setattr(pw_api, "sync_playwright", pw_start)
    assert browser_cli._session_health() == "unknown"
    browser_cli.cmd_stop()
    kill.assert_not_called()
    with pytest.raises(SystemExit):
        browser_cli._connect()
    pw_start.assert_not_called()


# --- группа 1 (fallback только при отсутствии команды) ---

@pytest.mark.parametrize("rc", [1, 2])
def test_nonzero_rc_is_terminal_unknown(monkeypatch, rc):
    # Молчаливый ненулевой rc — неудача исполнения, не «команды нет»:
    # терминальная неизвестность, слабый lsof её не достраивает.
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             ss_out="", ss_rc=rc,
             lsof_out=_lsof(_lsof_row("127.0.0.1", "chrome", 123)))
    assert browser_cli._session_health() == "unknown"
    with pytest.raises(SystemExit):
        browser_cli._connect()


@pytest.mark.parametrize("failure", [
    InterruptedError("interrupted"),
    PermissionError("denied"),
    OSError("generic io error"),
], ids=["interrupted", "permission", "other-oserror"])
def test_failed_ss_spawn_is_terminal_unknown(monkeypatch, failure):
    # Любой OSError кроме FileNotFoundError — команда есть, но исполнение
    # не дало наблюдения: терминальная неизвестность без перехода к lsof.
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             missing_tools=True)
    run = Mock(side_effect=[failure, AssertionError("fallback запрещён")])
    monkeypatch.setattr(browser_cli.subprocess, "run", run)
    assert browser_cli._session_health() == "unknown"
    assert run.call_count == 1


# --- группа 2 (ограниченные числовые поля записи) ---

@pytest.mark.parametrize("counters", ["4294967296 128", "0 4294967296"])
def test_ss_queue_counters_must_be_bounded(monkeypatch, counters):
    # Recv-Q/Send-Q — uint32: переполненное значение делает запись
    # недостоверной до классификации адреса → unknown, не live.
    row = f'LISTEN {counters} 127.0.0.1:9377 0.0.0.0:* users:(("chrome",pid=123,fd=7))'
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch, ss_out=_ss(row))
    assert browser_cli._session_health() == "unknown"
    with pytest.raises(SystemExit):
        browser_cli._connect()


_LSOF_BAD_NUMERIC_ROWS = [
    "chrome    123 me    7u  IPv4 222222      0t-  TCP 127.0.0.1:9377 (LISTEN)",
    "chrome    123 me    7u  IPv4 222222      0t-  TCP 127.0.0.2:9377 (LISTEN)",
    "chrome    123 me    7u  IPv4 notnum       0t0  TCP 127.0.0.1:9377 (LISTEN)",
    "chrome    123 me    7u  IPv4 4294967296   0t0  TCP 127.0.0.1:9377 (LISTEN)",
    "chrome    123 me    7u  IPv4 222222      0t9999999999999999999999  TCP 127.0.0.1:9377 (LISTEN)",
]


@pytest.mark.parametrize("row", _LSOF_BAD_NUMERIC_ROWS)
@pytest.mark.parametrize("probe", [True, False])
def test_lsof_numeric_fields_must_be_bounded(monkeypatch, row, probe):
    # Усечённые/нечисловые DEVICE и SIZE/OFF не доказывают ни владельца,
    # ни отсутствие — unknown при любом probe и на любом адресе.
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             ss_out=None, lsof_out=_lsof(row), port_open=probe)
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)
    assert browser_cli._session_health() == "unknown"
    browser_cli.cmd_stop()
    kill.assert_not_called()
    with pytest.raises(SystemExit):
        browser_cli._connect()


# --- группа 3 (одно решение на снимок) ---

def test_ensure_headed_makes_single_health_decision(monkeypatch):
    # Переоценка здоровья того же снимка запрещена: unknown не может
    # превратиться в live на втором чтении и разрешить остановку.
    a = {"port": 9377, "pid": 4242, "mode": "headed", "pstart": "tok"}
    monkeypatch.setattr(browser_cli, "_state", lambda: dict(a))
    health = Mock(side_effect=["unknown", "live"])
    monkeypatch.setattr(browser_cli, "_session_health", health)
    stop = Mock(return_value=True)
    monkeypatch.setattr(browser_cli, "_stop_own_session", stop)
    popen = Mock()
    monkeypatch.setattr(browser_cli.subprocess, "Popen", popen)
    import playwright.sync_api as pw_api
    pw_start = Mock()
    monkeypatch.setattr(pw_api, "sync_playwright", pw_start)

    with pytest.raises(SystemExit):
        browser_cli._ensure_headed()

    health.assert_called_once_with(a)
    stop.assert_not_called()
    popen.assert_not_called()
    pw_start.assert_not_called()


# --- группа 1 (скрытый pid — не доказательство ни присутствия, ни отсутствия) ---

@pytest.mark.parametrize("probe", [True, False])
@pytest.mark.parametrize("extra", [None, "valid"])
def test_hidden_pid_ss_row_is_no_evidence(monkeypatch, probe, extra):
    # Запись слушателя без users:(...) — владелец скрыт ОС: строка не
    # доказывает ни присутствие, ни отсутствие, даже на постороннем
    # адресе и даже рядом с валидной точной записью.
    rows = ["LISTEN 0 511 127.0.0.2:9377 0.0.0.0:*"]
    if extra:
        rows.append(_SS_ROW.format(addr="127.0.0.1", name="chrome", pid=123))
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch,
             ss_out=_ss(*rows), port_open=probe)
    kill = Mock()
    monkeypatch.setattr(browser_cli.os, "kill", kill)
    assert browser_cli._session_health() == "unknown"
    browser_cli.cmd_stop()
    kill.assert_not_called()
    with pytest.raises(SystemExit):
        browser_cli._connect()


# --- группа 2 (fd привязан к одному endpoint) ---

_SS_CONFLICT = (
    "LISTEN 0 511 {addr}:9377 0.0.0.0:*"
    ' users:(("chrome",pid=123,fd={fd}))'
)


@pytest.mark.parametrize("tool", ["ss", "lsof"])
@pytest.mark.parametrize("fd2", ["7u", "7r"])
def test_same_fd_on_conflicting_endpoints_is_untrusted(monkeypatch, tool, fd2):
    # Один и тот же дескриптор процесса (pid,fd) на двух локальных
    # endpoint выдачи — противоречивое наблюдение: unknown, не pid.
    # Сравнение по числовому fd — суффикс доступа u/r его не меняет.
    if tool == "ss":
        rows = _ss(
            _SS_CONFLICT.format(addr="127.0.0.1", fd=7),
            _SS_CONFLICT.format(addr="127.0.0.2", fd=7),
        )
        kwargs = {"ss_out": rows}
    else:
        kwargs = {"ss_out": None, "lsof_out": _lsof(
            "chrome    123 me    7u  IPv4 222222      0t0  TCP 127.0.0.1:9377 (LISTEN)",
            f"chrome    123 me    {fd2}  IPv4 222222      0t0  TCP 127.0.0.2:9377 (LISTEN)",
        )}
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch, **kwargs)
    assert browser_cli._session_health() == "unknown"
    with pytest.raises(SystemExit):
        browser_cli._connect()


@pytest.mark.parametrize("tool", ["ss", "lsof"])
def test_different_fd_or_duplicate_descriptor_no_conflict(monkeypatch, tool):
    # Тот же (pid,fd) на том же адресе — дубликат той же записи, а разные
    # fd — разные сокеты: конфликта нет, однозначный exact остаётся pid.
    if tool == "ss":
        rows = _ss(
            _SS_CONFLICT.format(addr="127.0.0.1", fd=7),
            _SS_CONFLICT.format(addr="127.0.0.1", fd=7),
            _SS_CONFLICT.format(addr="127.0.0.2", fd=8),
        )
        kwargs = {"ss_out": rows}
    else:
        kwargs = {"ss_out": None, "lsof_out": _lsof(
            "chrome    123 me    7u  IPv4 222222      0t0  TCP 127.0.0.1:9377 (LISTEN)",
            "chrome    123 me    8u  IPv4 222222      0t0  TCP 127.0.0.2:9377 (LISTEN)",
        )}
    _session({"port": 9377, "pid": 123, "mode": "headed"}, monkeypatch, **kwargs)
    assert browser_cli._session_health() == "live"


# --- группа 3 (остановка после транспортного сбоя — тот же снимок) ---

def test_start_transport_failure_stops_only_same_snapshot(monkeypatch):
    # _connect подтвердил A, транспорт упал, но снимок сменился на B:
    # решение об остановке A недействительно — отказ без stop и запуска.
    a = {"port": 9377, "pid": 4242, "mode": "headed", "pstart": "tok-a"}
    b = {"port": 9377, "pid": 5555, "mode": "headed", "pstart": "tok-b"}
    monkeypatch.setattr(browser_cli, "_state", Mock(side_effect=[a, a, a, b]))
    monkeypatch.setattr(browser_cli, "_session_health", lambda *_a: "live")
    import playwright.sync_api as pw_api
    pw = Mock()
    pw.start.return_value.chromium.connect_over_cdp.side_effect = Exception("down")
    pw_start = Mock(return_value=pw)
    monkeypatch.setattr(pw_api, "sync_playwright", pw_start)
    stop = Mock(return_value=True)
    monkeypatch.setattr(browser_cli, "_stop_own_session", stop)
    popen = Mock()
    monkeypatch.setattr(browser_cli.subprocess, "Popen", popen)

    with pytest.raises(SystemExit):
        browser_cli.cmd_start()

    stop.assert_not_called()
    popen.assert_not_called()


def test_browser_cli_missing_fill_argument_is_clean_error(monkeypatch, capsys):
    monkeypatch.setattr(browser_cli.sys, "argv", ["bsu-browser", "fill", "Логин"])
    with pytest.raises(SystemExit) as exc:
        browser_cli.main()
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "Использование: bsu-browser fill <label> <value>" in captured.err
    assert "Traceback" not in captured.err
