import sqlite3
import subprocess
from shlex import split
from unittest.mock import Mock

import pytest

from bsu_cabinet import login, sms


@pytest.mark.parametrize(
    "body",
    [
        "Код для другого сервиса: 1234",
        "CabinetBSU: 1234",
        "Vash kod vxoda v CabinetBSU: 12345",
        "Vash kod vxoda v CabinetBSU: 1234 extra text",
        "Vash kod vxoda v CabinetBSU: １２３４",
    ],
)
def test_extract_code_rejects_messages_outside_cabinet_template(body):
    assert sms.extract_code(body) is None


@pytest.mark.parametrize("sender", ["+15555550101", "+15555550102", "BSU"])
def test_wait_for_code_uses_fresh_cabinet_message_regardless_of_sender(monkeypatch, sender):
    def run(flag, device, transport, command):
        assert (flag, device, transport) == ("-s", "paired-fixture", "shell")
        args = split(command)
        columns = args[args.index("--projection") + 1].split(":")
        where = args[args.index("--where") + 1] if "--where" in args else "1"
        order = args[args.index("--sort") + 1]
        assert columns == ["body", "date"]
        with sqlite3.connect(":memory:") as inbox:
            inbox.execute("CREATE TABLE sms (address TEXT, body TEXT, date INTEGER)")
            inbox.executemany(
                "INSERT INTO sms VALUES (?, ?, ?)",
                [
                    (sender, "Vash kod vxoda v CabinetBSU: 0123", 2001),
                    (sender, "Код другого сервиса: 5000", 2003),
                    (sender, "Vash kod vxoda v CabinetBSU: 50000", 2002),
                    (sender, "Vash kod vxoda v CabinetBSU: 4000", 1999),
                ],
            )
            rows = inbox.execute(f"SELECT body, date FROM sms WHERE {where} ORDER BY {order}").fetchall()
        assert len(rows) == 2
        stdout = "\n".join(f"Row: {i} body={body}, date={date}" for i, (body, date) in enumerate(rows))
        return subprocess.CompletedProcess([], 0, stdout, "")

    monkeypatch.setattr(sms, "_run", run)
    assert sms.wait_for_code("paired-fixture", since_ms=2000, timeout_s=1) == "0123"


def test_login_looks_for_sms_from_before_form_submit(monkeypatch):
    page = Mock(url="https://teacher-cabinet.bsu.by/Login")
    submit = page.get_by_text.return_value.first
    check_sms = Mock(return_value="0123")

    def after_submit():
        clock.return_value = 1_100_000_000_000

    def after_sms(_page, _code):
        page.url = "https://teacher-cabinet.bsu.by/PersonalCabinet"

    clock = Mock(return_value=1_000_000_000_000)
    submit.click.side_effect = after_submit
    page.wait_for_url = Mock()
    monkeypatch.setattr(login.time, "time_ns", clock)
    monkeypatch.setattr(login, "fill_login", Mock())
    monkeypatch.setattr(login, "wait_text", Mock())
    monkeypatch.setattr(login, "enter_sms_code", after_sms)
    available = Mock(return_value=True)
    monkeypatch.setattr(sms, "adb_available", available)
    monkeypatch.setattr(sms, "wait_for_code", check_sms)

    assert login.login(page, adb_serial="paired-fixture").endswith("/PersonalCabinet")
    available.assert_called_once_with("paired-fixture")
    check_sms.assert_called_once_with("paired-fixture", since_ms=1_000_000)
