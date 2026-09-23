from pathlib import Path


def test_installer_excludes_ai_owned_interpreters():
    script = Path(__file__).parents[1] / "setup.ps1"
    content = script.read_text(encoding="utf-8").lower()
    assert "(hermes|ollama|local-ai|llama|inference)" in content
    assert "hermes\\hermes-agent" not in content


def test_installer_uses_dedicated_venv_sibling():
    content = (Path(__file__).parents[1] / "setup.ps1").read_text(encoding="utf-8")
    assert ".codex-local-ops.venv" in content
