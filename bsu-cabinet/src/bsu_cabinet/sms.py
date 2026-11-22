"""Auto-SMS: чтение кода кабинета с телефона через adb (wireless debugging).

Проверено на Samsung S24 (SM_S9210): `adb shell content query
content://sms/inbox` читает входящие SMS без root и дефолт-SMS-роли.
SMS кабинета: «Vash kod vxoda v CabinetBSU: NNNN». Номер отправителя меняется.

Доступ разрешён только к явно выбранному ранее сопряжённому устройству.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from shlex import quote


def _adb() -> str:
    """adb: BSU_ADB env → PATH → старый путь platform-tools."""
    import os

    if p := os.environ.get("BSU_ADB"):
        return p
    if p := shutil.which("adb"):
        return p
    legacy = Path.home() / ".local" / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")
    return str(legacy)


CABINET_SMS_PREFIX = "Vash kod vxoda v CabinetBSU:"
CABINET_SMS_RE = re.compile(re.escape(CABINET_SMS_PREFIX) + r"\s*([0-9]{4})", re.IGNORECASE)
POLL_INTERVAL_S = 3
TIMEOUT_S = 120


def _run(*args: str, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    return subprocess.run([_adb(), *args], capture_output=True, text=True, timeout=timeout)


class SmsUnavailableError(RuntimeError):
    """Выбранный телефон или его SMS недоступны."""


def adb_available(serial: str) -> bool:
    if not serial.strip():
        return False
    try:
        out = _run("-s", serial, "get-state", timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0 and out.stdout.strip() == "device"


def latest_sms_since(serial: str, since_ms: int) -> list[str]:
    """Тексты свежих сообщений CabinetBSU только с выбранного устройства."""
    if not serial.strip():
        raise SmsUnavailableError("Не задано устройство для авто-SMS.")
    where = quote(f"date >= {since_ms:d} AND body LIKE '{CABINET_SMS_PREFIX}%'")
    try:
        out = _run(
            "-s", serial, "shell",
            "content query --uri content://sms/inbox "
            f"--projection body:date --where {where} --sort 'date DESC LIMIT 10'",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SmsUnavailableError(f"Не удалось прочитать SMS ({type(exc).__name__}).") from None
    if out.returncode or "Error while accessing provider" in out.stderr + out.stdout:
        raise SmsUnavailableError("Не удалось прочитать SMS с выбранного телефона.")
    messages = re.finditer(r"^Row: \d+ body=(.*), date=(\d+)$", out.stdout, re.MULTILINE)
    return [m[1] for m in messages if int(m[2]) >= since_ms]


def extract_code(body: str) -> str | None:
    match = CABINET_SMS_RE.fullmatch(body.strip())
    return match[1] if match else None


def wait_for_code(serial: str, *, since_ms: int, timeout_s: int = TIMEOUT_S) -> str:
    """Ждать код кабинета на выбранном устройстве, не выводя его."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for body in latest_sms_since(serial, since_ms):
            code = extract_code(body)
            if code:
                return code
        time.sleep(max(0, min(POLL_INTERVAL_S, deadline - time.monotonic())))
    raise TimeoutError(f"SMS с кодом не пришло за {timeout_s} с")
