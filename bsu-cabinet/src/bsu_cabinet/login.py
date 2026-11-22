"""Вход в кабинет с привязкой к доверенной странице и явным выбором SMS."""

from __future__ import annotations

import re
import sys
import time

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .browser import LOGIN_URL, cabinet_path, in_cabinet, wait_text
from .creds import get_password, stored_login

SMS_MARKER = "Введите число из sms"
SMS_WAIT_MS = 120_000


def _require_login_page(page: Page) -> None:
    if not cabinet_path(page.url, "/Login"):
        raise RuntimeError("Ввод данных возможен только на странице входа кабинета БГУ (/Login).")


def fill_login(page: Page) -> None:
    _require_login_page(page)
    login_field = page.locator(
        "input:not([type]):visible, input[type=text]:visible, input[type=email]:visible"
    )
    password_field = page.locator("input[type=password]:visible")
    # Handles фиксируют конкретный документ: локатор после навигации мог бы найти поле на другом сайте.
    user_input = login_field.element_handle(timeout=5_000)
    password_input = password_field.element_handle(timeout=5_000)
    _require_login_page(page)
    user = stored_login()
    password = get_password(user)
    _require_login_page(page)
    user_input.fill(user, timeout=5_000)
    _require_login_page(page)
    password_input.fill(password, timeout=5_000)


def enter_sms_code(page: Page, code: str) -> None:
    """Ввод кода экранной клавиатурой внутри модального диалога.

    Цифры — DIV'ы клавиатуры под `ui-widget-overlay`; обычный locator-click
    блокируется оверлеем, поэтому кликаем mouse-координатами центра кнопки.
    """
    if not re.fullmatch(r"[0-9]{4}", code):
        raise RuntimeError("Некорректный формат SMS-кода кабинета.")
    for digit in code:
        _require_login_page(page)
        xy = page.evaluate(
            """(d) => {
              const els = [...document.querySelectorAll('div')].filter(el => {
                const r = el.getBoundingClientRect();
                return r.width > 10 && r.height > 10 && (el.innerText || '').trim() === d
                  && el.closest('.ui-dialog, [role=dialog]');
              });
              const el = els[els.length - 1];
              if (!el) return null;
              const r = el.getBoundingClientRect();
              return [r.x + r.width / 2, r.y + r.height / 2];
            }""",
            digit,
        )
        if not xy:
            raise RuntimeError("Кнопка экранной клавиатуры не найдена в диалоге SMS")
        page.mouse.click(xy[0], xy[1])
        page.wait_for_timeout(200)
    _require_login_page(page)
    page.get_by_text("Подтвердить", exact=True).first.click()


def _confirm_by_phone(page: Page, serial: str, since_ms: int) -> None:
    from .sms import wait_for_code

    try:
        wait_text(page, SMS_MARKER, SMS_WAIT_MS)
    except PlaywrightTimeout:
        if in_cabinet(page):
            return
        raise
    _require_login_page(page)
    enter_sms_code(page, wait_for_code(serial, since_ms=since_ms))


def _finish_login(page: Page, phone: str | None, since_ms: int, *, visible: bool) -> str:
    from .sms import SmsUnavailableError

    if in_cabinet(page):
        return page.url
    if phone is None:
        print("Введи SMS-код в окне Chrome и нажми «Подтвердить».", flush=True)
    else:
        try:
            _confirm_by_phone(page, phone, since_ms)
        except (SmsUnavailableError, TimeoutError) as exc:
            if not visible:
                raise RuntimeError("Авто-SMS недоступно. Повторного запроса SMS не было.") from exc
            print("Авто-SMS недоступно. Введи код в уже открытом окне Chrome.", flush=True)
    page.wait_for_url(
        lambda url: cabinet_path(url, "/PersonalCabinet"),
        timeout=SMS_WAIT_MS if visible else 15_000,
    )
    if not in_cabinet(page):
        raise RuntimeError("Кабинет не подтвердил вход.")
    return page.url


def login(page: Page, *, adb_serial: str | None = None, visible: bool = True) -> str:
    """Обычный вход ждёт ручного подтверждения; доступ к adb только по явному выбору."""
    from .sms import adb_available

    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
    if in_cabinet(page):
        return page.url
    _require_login_page(page)
    phone = adb_serial if adb_serial and adb_available(adb_serial) else None
    if phone is None and not visible:
        raise RuntimeError("Для ручного ввода SMS открой Chrome через bsu-browser start --headed.")
    fill_login(page)
    _require_login_page(page)
    sms_requested_ms = time.time_ns() // 1_000_000
    page.get_by_text("Вход", exact=True).first.click()
    return _finish_login(page, phone, sms_requested_ms, visible=visible)


if __name__ == "__main__":
    from .browser_cli import login_command

    login_command(sys.argv[1:])
