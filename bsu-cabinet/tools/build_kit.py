#!/usr/bin/env python3
"""Сборка единого комплекта bsu-cabinet-kit-<версия>.tar.gz.

Архив содержит wheel, четыре скилла, гайды с сохранённой структурой ссылок,
синтетический пример и README на русском. Запуск из любого каталога:

    python bsu-cabinet/tools/build_kit.py --out <каталог>
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_NAME = "bsu-cabinet"

SKILLS = (
    "bsu-cabinet",
    "bsu-cabinet-login",
    "bsu-cabinet-lesson-card",
    "bsu-cabinet-individual-plan",
)
GUIDES = (
    "auto-sms.md",
    "cabinet-map.md",
    "individual-plan-guide.md",
    "lesson-card-guide.md",
    "runbook.md",
)


def _version(pkg_dir: Path) -> str:
    data = tomllib.loads((pkg_dir / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def build(repo_root: Path, out_dir: Path) -> Path:
    """Собрать архив и вернуть его путь."""
    repo_root = Path(repo_root)
    out_dir = Path(out_dir)
    pkg = repo_root / PKG_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"bsu-cabinet-kit-{_version(pkg)}"
    archive = out_dir / f"{name}.tar.gz"
    if archive.exists():
        raise FileExistsError(
            f"Архив уже существует и не будет перезаписан: {archive}"
        )
    # staging — всегда собственный временный каталог: совпавшее имя в --out
    # не является нашим артефактом и не удаляется.
    staging = Path(tempfile.mkdtemp(prefix=f"{name}-"))
    try:
        (staging / "dist").mkdir(parents=True)
        subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(staging / "dist"), str(pkg)],
            check=True,
        )
        wheels = list((staging / "dist").glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"Ожидался один wheel, получено: {wheels}")

        for skill in SKILLS:
            dst = staging / ".agents" / "skills" / skill
            dst.mkdir(parents=True)
            shutil.copy2(repo_root / ".agents" / "skills" / skill / "SKILL.md", dst / "SKILL.md")

        docs_dst = staging / "docs" / "bsu-cabinet"
        docs_dst.mkdir(parents=True)
        for guide in GUIDES:
            shutil.copy2(repo_root / "docs" / "bsu-cabinet" / guide, docs_dst / guide)
        site_js = repo_root / "docs" / "bsu-cabinet" / "site-js"
        if site_js.is_dir():
            shutil.copytree(site_js, docs_dst / "site-js")

        kit_src = pkg / "kit"
        shutil.copy2(kit_src / "README.md", staging / "README.md")
        shutil.copytree(kit_src / "examples", staging / "examples")

        with tarfile.open(archive, "x:gz") as tar:
            tar.add(staging, arcname=name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return archive


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Собрать комплект bsu-cabinet-kit")
    parser.add_argument("--out", default="dist-kit", help="каталог результата")
    parser.add_argument("--root", default=str(REPO_ROOT), help="корень репо")
    args = parser.parse_args(argv)
    print(build(Path(args.root), Path(args.out)))


if __name__ == "__main__":
    main()
