"""Validate the actual transparent light and dark PNG logos."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"


def test_both_logos_are_real_transparent_pngs():
    for theme in ("light", "dark"):
        filename = ASSETS / f"social-supplement-{theme}.png"
        with Image.open(filename) as logo:
            assert logo.format == "PNG"
            assert logo.size == (512, 512)
            assert logo.convert("RGBA").getchannel("A").getextrema() == (0, 255)


def test_readme_references_real_png_files():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("<picture>")
    for theme in ("light", "dark"):
        assert f"assets/social-supplement-{theme}.png" in readme


def test_failed_logo_formats_removed():
    for old_name in (
        "icon-light.png.b64",
        "icon-dark.png.b64",
        "social-supplement.svg",
        "social-supplement-light.svg",
        "social-supplement-dark.svg",
        "icon-light.png",
        "icon-dark.png",
    ):
        assert not (ASSETS / old_name).exists()
    assert not (ROOT / "scripts" / "assets.py").exists()


def test_project_name_is_generic():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "# Social Supplement MCP" in readme
    assert "Extensible collection of supplemental social-media MCP tools" in metadata
