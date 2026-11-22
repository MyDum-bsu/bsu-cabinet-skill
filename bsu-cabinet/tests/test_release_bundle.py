"""Комплект раздачи #135: один архив вне монорепо, установка из произвольного
каталога, рабочие точки входа и пример без путей владельца."""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG = REPO_ROOT / "bsu-cabinet"
BUILDER = PKG / "tools" / "build_kit.py"


def _kit_name() -> str:
    import tomllib

    version = tomllib.loads((PKG / "pyproject.toml").read_text())["project"]["version"]
    return f"bsu-cabinet-kit-{version}"

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
TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".txt", ".html"}
MD_LINK = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_kit", BUILDER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def kit(tmp_path_factory):
    """Собранный и распакованный в произвольный каталог вне git комплект."""
    build_kit = _load_builder()
    out = tmp_path_factory.mktemp("kit-out")
    archive = build_kit.build(REPO_ROOT, out)
    assert archive.name.endswith(".tar.gz")
    dest = tmp_path_factory.mktemp("install-here") / "произвольный каталог"
    dest.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(dest, filter="data")
    roots = list(dest.iterdir())
    assert len(roots) == 1 and roots[0].is_dir()
    return roots[0]


def test_build_does_not_clobber_existing_same_named_dir(tmp_path):
    kit_dir = tmp_path / _kit_name()
    kit_dir.mkdir()
    sentinel = kit_dir / "sentinel.txt"
    sentinel.write_text("keep me")
    archive = _load_builder().build(REPO_ROOT, tmp_path)
    assert sentinel.read_text() == "keep me"
    assert archive.exists()


def test_build_creates_fresh_output_dir(tmp_path):
    out = tmp_path / "new-output"
    archive = _load_builder().build(REPO_ROOT, out)
    assert archive.exists()
    assert archive.parent == out


def test_build_refuses_to_overwrite_existing_archive(tmp_path):
    archive = tmp_path / f"{_kit_name()}.tar.gz"
    archive.write_text("old archive")
    with pytest.raises((FileExistsError, RuntimeError), match="существ|не буд"):
        _load_builder().build(REPO_ROOT, tmp_path)
    assert archive.read_text() == "old archive"


def test_bundle_contains_wheel_skills_guides_example_and_readme(kit):
    wheels = list((kit / "dist").glob("*.whl"))
    assert len(wheels) == 1
    for skill in SKILLS:
        assert (kit / ".agents" / "skills" / skill / "SKILL.md").is_file(), skill
    for guide in GUIDES:
        assert (kit / "docs" / "bsu-cabinet" / guide).is_file(), guide
    assert (kit / "examples").is_dir() and any((kit / "examples").iterdir())
    assert (kit / "README.md").is_file()


def test_bundle_relative_links_resolve(kit):
    """Все относительные markdown-ссылки указывают на файлы внутри комплекта."""
    missing = []
    for md in kit.rglob("*.md"):
        for link in MD_LINK.findall(md.read_text(encoding="utf-8")):
            if link.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = (md.parent / link.split("#")[0]).resolve()
            if not target.exists():
                missing.append(f"{md.relative_to(kit)} -> {link}")
    assert missing == []


def test_bundle_has_no_owner_paths_or_machine_specific_instructions(kit):
    offenders = []
    for path in kit.rglob("*"):
        if path.suffix in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8", errors="replace")
            for marker in ("/home/", "/Users/", "mydum"):
                if marker in text:
                    offenders.append(f"{path.relative_to(kit)}: {marker}")
    assert offenders == []


def test_wheel_carries_setup_page_and_images(kit):
    wheel = next((kit / "dist").glob("*.whl"))
    names = set(zipfile.ZipFile(wheel).namelist())
    for asset in ("setup.html", "setup-hero.jpg", "setup-logo.png"):
        assert f"bsu_cabinet/assets/{asset}" in names, asset


@pytest.fixture(scope="module")
def installed(kit, tmp_path_factory):
    """Wheel, установленный в новый venv вне git. Требует сеть для зависимостей."""
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv нужен для сборки venv в тесте комплекта")
    env_dir = tmp_path_factory.mktemp("installed-env") / "env"
    wheel = next((kit / "dist").glob("*.whl"))
    subprocess.run([uv, "venv", str(env_dir)], check=True, capture_output=True)
    pip = subprocess.run(
        [uv, "pip", "install", "--python", str(env_dir / "bin" / "python"), str(wheel)],
        capture_output=True, text=True, timeout=600,
    )
    if pip.returncode:
        pytest.skip(f"Зависимости не установились (нет сети?): {pip.stderr[-400:]}")
    return env_dir


def _run(env_dir: Path, *argv: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(env_dir / "bin" / argv[0]), *argv[1:]],
        capture_output=True, text=True, timeout=timeout,
    )


def test_installed_entry_points_and_short_example_request(kit, installed):
    for prog in ("bsu-browser", "mo-cabinet"):
        res = _run(installed, prog, "--help")
        assert res.returncode == 0, res.stderr
        assert "/home/" not in res.stdout + res.stderr
    example = next((kit / "examples").glob("*.yaml"))
    res = _run(installed, "mo-cabinet", "card-plan", str(example))
    assert res.returncode == 0, res.stderr
    assert "Операций: " in res.stdout


def test_installed_package_resources_visible(kit, installed):
    res = subprocess.run(
        [
            str(installed / "bin" / "python"), "-c",
            "from importlib.resources import files\n"
            "for a in ('setup.html', 'setup-hero.jpg', 'setup-logo.png'):\n"
            "    assert (files('bsu_cabinet') / 'assets' / a).is_file(), a\n"
            "print('assets ok')",
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr
    assert "assets ok" in res.stdout
