from pathlib import Path

def test_release_workflow_has_valid_github_expressions():
    root = Path(__file__).resolve().parents[1]
    release = (root / ".github" / "workflows" / "windows-release.yml").read_text(encoding="utf-8")
    assert "\\${{" not in release
    assert "TertiumModManager-Windows-${{ steps.release.outputs.tag }}" in release
    assert "RELEASE_REQUEST" in release
    assert 'branches: ["main"]' in release
