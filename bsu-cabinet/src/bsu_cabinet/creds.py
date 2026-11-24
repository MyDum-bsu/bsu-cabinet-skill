"""Логин текущего профиля на диске, пароль в системном keyring."""

from __future__ import annotations

import getpass
import os
import warnings
from pathlib import Path

import keyring
from keyring.backend import KeyringBackend

SERVICE = "bsu-cabinet"
STATE_DIR = Path(os.environ.get("BSU_STATE_DIR", Path.home() / ".bsu-cabinet"))
LOGIN_FILE = STATE_DIR / "login"
SYSTEM_KEYRINGS = {
    "keyring.backends.Windows",
    "keyring.backends.macOS",
    "keyring.backends.SecretService",
    "keyring.backends.libsecret",
    "keyring.backends.kwallet",
}


class CredentialError(RuntimeError):
    """Сообщение, которое можно показать без раскрытия учётных данных."""


class SetupRequiredError(CredentialError):
    """Текущий профиль ещё не настроен."""


def _system_keyring() -> KeyringBackend:
    backend = keyring.get_keyring()
    if type(backend).__module__ not in SYSTEM_KEYRINGS:
        raise CredentialError("Включи системное хранилище паролей ОС и повтори настройку.")
    return backend


def stored_login() -> str:
    try:
        login = LOGIN_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise SetupRequiredError("В этом профиле не настроен вход: bsu-browser setup") from exc
    if not login:
        raise SetupRequiredError("В этом профиле не настроен вход: bsu-browser setup")
    return login


def get_password(login: str) -> str:
    backend = _system_keyring()
    try:
        password = backend.get_password(SERVICE, login)
    except Exception as exc:  # noqa: BLE001 — backend keyring скрыт за безопасной ошибкой
        raise CredentialError(f"Не удалось прочитать пароль из keyring ({type(exc).__name__}).") from None
    if not password:
        raise SetupRequiredError("Пароль не найден в keyring: bsu-browser setup")
    return password


def save_credentials(login: str, password: str) -> None:
    login = login.strip()
    if not login or not password or "\n" in login or "\r" in login:
        raise CredentialError("Заполни логин и пароль; логин должен занимать одну строку.")
    backend = _system_keyring()
    try:
        STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        backend.set_password(SERVICE, login, password)
        LOGIN_FILE.write_text(login + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — backend keyring скрыт за безопасной ошибкой
        raise CredentialError(f"Не удалось сохранить данные входа ({type(exc).__name__}).") from None


def prompt_and_save(login: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            password = getpass.getpass("Пароль кабинета: ")
        except getpass.GetPassWarning:
            raise CredentialError("Терминал не может скрыть ввод. Используй bsu-browser setup.") from None
    save_credentials(login, password)
    print("Данные входа сохранены в системном хранилище.")
