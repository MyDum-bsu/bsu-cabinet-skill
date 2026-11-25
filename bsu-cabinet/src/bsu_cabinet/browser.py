"""Сессия браузера для teacher-cabinet.bsu.by.

Playwright + системный Google Chrome (/usr/bin/google-chrome), постоянный профиль
~/.bsu-cabinet/chrome-profile — куки и сессия кабинета переживают перезапуск.

ВАЖНО: локальный прокси из env (127.0.0.1:<port>) не достаёт teacher-cabinet.bsu.by
— Chrome запускаем с --no-proxy-server.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

CHROME_PATH = "/usr/bin/google-chrome"
LOGIN_URL = "https://teacher-cabinet.bsu.by/Login"
BASE_URL = "https://teacher-cabinet.bsu.by"

STATE_DIR = Path.home() / ".bsu-cabinet"
PROFILE_DIR = STATE_DIR / "chrome-profile"
SHOTS_DIR = STATE_DIR / "shots"


def launch(pw: Playwright | None = None, headless: bool = False) -> tuple[Playwright, BrowserContext, Page]:
    """Открыть Chrome с постоянным профилем и вернуть (pw, context, page)."""
    pw = pw or sync_playwright().start()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    ctx = pw.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        executable_path=CHROME_PATH,
        headless=headless,
        args=["--no-proxy-server", "--no-first-run", "--no-default-browser-check"],
        no_viewport=True,
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    return pw, ctx, page


def shot(page: Page, name: str) -> Path:
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SHOTS_DIR / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)
    return path


def cabinet_path(url: str, path: str) -> bool:
    parts = urlsplit(url)
    return (parts.scheme, parts.netloc, parts.path) == ("https", urlsplit(BASE_URL).netloc, path)


def in_cabinet(page: Page) -> bool:
    """Положительный признак входа, не редирект на страницу ошибки."""
    return cabinet_path(page.url, "/PersonalCabinet")


def wait_text(page: Page, text: str, timeout_ms: int = 15_000) -> None:
    page.wait_for_function(
        "t => document.body && document.body.innerText.includes(t)",
        arg=text,
        timeout=timeout_ms,
    )


def fill_labeled(page: Page, label: str, value: str) -> None:
    """Заполнить поле по подписи (label/placeholder/name), без учёта регистра."""
    loc = page.get_by_label(label, exact=False)
    if loc.count() == 0:
        loc = page.locator(
            f'input[placeholder*="{label}" i], input[name*="{label}" i], textarea[name*="{label}" i]'
        )
    loc.first.fill(value)


def click_text(page: Page, text: str) -> None:
    """Клик по элементу с точным текстом (экранная клавиатура, кнопки)."""
    page.get_by_text(text, exact=True).first.click()
