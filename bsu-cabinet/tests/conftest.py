"""Изоляция обвязки от рабочего профиля владельца.

env выставляется до импорта bsu_cabinet: HOME, XDG_CONFIG_HOME, BSU_STATE_DIR
и отдельный BSU_PORT указывают в заранее созданные временные каталоги, поэтому
запись в ~/.bsu-cabinet из тестов невозможна. Процессы, сигналы и CDP в тестах —
подставные.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

TEST_ROOT = Path(tempfile.mkdtemp(prefix="bsu-cabinet-tests-"))

for _name, _path in {
    "HOME": TEST_ROOT / "home",
    "XDG_CONFIG_HOME": TEST_ROOT / "xdg-config",
    "XDG_DATA_HOME": TEST_ROOT / "xdg-data",
    "BSU_STATE_DIR": TEST_ROOT / "state",
}.items():
    _path.mkdir(parents=True, exist_ok=True)
    os.environ[_name] = str(_path)

os.environ["BSU_PORT"] = "19377"
