# Runbook: прогон скиллов кабинета вне TUI

## Окружение

- Из корня рабочего дерева `uv sync --all-groups` ставит `bsu-browser`
  и `mo-cabinet` в `.venv`. Команды ниже выполняются через `uv run`.
- Chrome ищется сам: `BSU_CHROME` env → PATH → типовые пути Linux/Mac/Win.
- **Эта машина**: env-прокси `127.0.0.1:25347` сайт не достаёт → для `start`
  нужен `export BSU_NO_PROXY=1` (флаг `--no-proxy-server`; это НАШ кейс,
  не часть системы). Для curl: `curl --noproxy '*'`.
- `bsu-browser login` открывает видимую настройку, если в текущем профиле
  ещё нет логина. Пароль сохраняется в нативном keyring (`bsu-cabinet`).
  Старые учётные данные `bsu-agent` не подхватываются.
- SMS по умолчанию вводит владелец в Chrome. Авто-SMS — только явный выбор
  [выбранного телефона](auto-sms.md) через `--sms adb --adb-serial`.

## Платформы

- Linux: проверено. headed — XWayland (cookie `.mutter-Xwaylandauth.*`),
  headless — без окна.
- Windows/macOS: headed — обычное окно Chrome без env-магии; adb/keyring —
  нативные пути ОС (Credential Manager / Keychain). Не проверено живьём.

## Сессия

```bash
uv run bsu-browser start --headed       # окно для настройки и ручного SMS
uv run bsu-browser login               # ручной SMS в этом окне, без adb
uv run bsu-browser status              # после входа ожидается /PersonalCabinet
```

Стоп: `uv run bsu-browser stop`. Статус: `uv run bsu-browser status`.

## Прогон в bb-треде

1. В новом треде: `uv sync --all-groups` из корня checkout.
2. Прочитать `.agents/skills/bsu-cabinet-login/SKILL.md` и выполнить шаги —
   все команды через `uv run bsu-browser …` в shell.
3. Скиллы: bsu-cabinet-login / bsu-cabinet-lesson-card /
   bsu-cabinet-individual-plan в `.agents/skills/`.

## Известные провалы

- `bsu-browser start` без флагов запускает headless. Настройка и ручной
  вход сами переводят подтверждённо свою headless-сессию в видимое окно
  до отправки SMS; чужой или непроверяемый процесс на порту — отказ
  без остановки и без SMS (подсказка про `BSU_PORT`).
- Chrome первый раз поднимает CDP ~10–30 с (инициализация профиля).
- Если явно выбранный телефон недоступен, проверь его по
  [инструкции adb](auto-sms.md). При уже открытом диалоге введи код в этом
  окне Chrome, не запрашивая второй.
