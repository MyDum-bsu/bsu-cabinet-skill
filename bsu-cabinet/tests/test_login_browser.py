from unittest.mock import Mock

import pytest
from bsu_cabinet import browser_cli, creds, login, sms
from bsu_cabinet.browser import BASE_URL, LOGIN_URL
from playwright.sync_api import sync_playwright

OTHER = "https://outside.example/Login"
FORM = '<form><input id="login" type="text"><input id="password" type="password"></form>'


@pytest.fixture
def context():
    try:
        chrome = browser_cli._chrome_path()
    except SystemExit:
        pytest.skip("Установленный Chrome нужен для локальных браузерных тестов")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=chrome,
            headless=True,
            args=["--no-proxy-server", "--disable-background-networking"],
        )
        try:
            ctx = browser.new_context(service_workers="block")
            ctx.set_default_timeout(5_000)
            ctx.route(
                "**/*",
                lambda route: route.continue_() if route.request.url.startswith("file:") else route.abort(),
            )
            for url in (LOGIN_URL, OTHER):
                ctx.route(url, lambda route: route.fulfill(content_type="text/html", body=FORM))
            yield ctx
        finally:
            browser.close()


@pytest.mark.parametrize("navigate_away", [False, True])
def test_fill_pins_credentials_to_the_login_document(context, monkeypatch, navigate_away):
    page = context.new_page()
    page.goto(LOGIN_URL)
    monkeypatch.setattr(login, "stored_login", lambda: "fixture-login")

    def password(_login):
        if navigate_away:
            page.goto(OTHER)
        return "fixture-password"

    monkeypatch.setattr(login, "get_password", password)
    if navigate_away:
        with pytest.raises(RuntimeError, match="страниц"):
            login.fill_login(page)
        assert page.locator("#login").input_value() == ""
        assert page.locator("#password").input_value() == ""
    else:
        login.fill_login(page)
        assert page.locator("#login").input_value() == "fixture-login"
        assert page.locator("#password").input_value() == "fixture-password"


def test_manual_login_reaches_only_the_expected_portal_page(context, monkeypatch):
    page = context.new_page()
    home = f"{BASE_URL}/PersonalCabinet"
    # Имитируем ввод владельцем после показа диалога, без настоящего SMS.
    script = """
      <button id="enter">Вход</button>
      <button id="confirm" hidden onclick="location.href='/PersonalCabinet'">Подтвердить</button>
      <script>
        const confirmButton = document.getElementById('confirm');
        document.getElementById('enter').onclick = () => {
          confirmButton.hidden = false;
          setTimeout(() => confirmButton.click(), 50);
        };
      </script>
    """
    content_type = "text/html; charset=utf-8"
    page.route(LOGIN_URL, lambda route: route.fulfill(body=FORM + script, content_type=content_type))
    page.route(home, lambda route: route.fulfill(body="Тестовый кабинет", content_type=content_type))
    monkeypatch.setattr(login, "stored_login", lambda: "fixture-login")
    monkeypatch.setattr(login, "get_password", lambda _user: "fixture-password")
    probe = Mock(side_effect=AssertionError("Unexpected ADB call"))
    monkeypatch.setattr(sms, "adb_available", probe)

    assert login.login(page) == home
    probe.assert_not_called()


@pytest.mark.parametrize("storage_fails", [False, True])
def test_setup_waits_for_storage_and_disposes_only_its_tab(context, monkeypatch, capsys, storage_fails):
    original = context.new_page()
    stop = Mock()
    setup_tabs = []
    writes = []

    def drive_setup(tab):
        setup_tabs.append(tab)
        tab.on("domcontentloaded", lambda: tab.evaluate("""() => {
            document.getElementById('l').value = 'fixture-login';
            document.getElementById('p').value = 'fixture-password';
            document.getElementById('save').click();
        }"""))

    def save(username, password):
        assert setup_tabs[0].locator("#p").input_value() == ""
        assert setup_tabs[0].locator("#l").input_value() == ""
        assert setup_tabs[0].evaluate("() => !('__creds' in window)")
        assert setup_tabs[0].get_by_text("Готово", exact=True).count() == 0
        writes.append((username, password))
        if storage_fails:
            raise creds.CredentialError("Хранилище закрыто.")

    context.on("page", drive_setup)
    monkeypatch.setattr(browser_cli, "_ensure_headed", Mock())
    monkeypatch.setattr(browser_cli, "_connect", lambda: (stop, Mock(), original))
    monkeypatch.setattr(creds, "save_credentials", save)

    if storage_fails:
        with pytest.raises(SystemExit, match="Хранилище закрыто"):
            browser_cli.cmd_setup(5)
    else:
        browser_cli.cmd_setup(5)

    assert writes == [("fixture-login", "fixture-password")]
    assert len(setup_tabs) == 1 and setup_tabs[0].is_closed()
    assert context.pages == [original]
    stop.stop.assert_called_once()
    out = capsys.readouterr().out
    assert ("Данные входа сохранены" in out) is not storage_fails
    assert "fixture-password" not in out
