from pathlib import Path
import tempfile

import self_update


def test_version_comparison():
    assert self_update.parse_version("v0.8.8") == (0, 8, 8)
    assert self_update.is_newer_version("0.8.8", "0.8.7")
    assert not self_update.is_newer_version("0.8.7", "0.8.7")
    assert not self_update.is_newer_version("0.8.6", "0.8.7")


def test_fetch_latest_release_reads_installer_digest(monkeypatch):
    payload = {
        "tag_name": "v0.8.8",
        "html_url": "https://github.com/Tangodwn/TertiumModManager/releases/tag/v0.8.8",
        "published_at": "2026-10-09T00:00:00Z",
        "assets": [
            {
                "name": "TertiumModManager-Setup-x64.exe",
                "browser_download_url": "https://example.invalid/TertiumModManager-Setup-x64.exe",
                "size": 123,
                "digest": "sha256:" + ("a" * 64),
            },
            {
                "name": "TertiumModManager-Setup-x64.exe.sha256",
                "browser_download_url": "https://example.invalid/TertiumModManager-Setup-x64.exe.sha256",
                "size": 97,
            },
        ],
    }
    monkeypatch.setattr(self_update, "_request_json", lambda *_a, **_k: payload)
    release = self_update.fetch_latest_release()
    assert release.version == "0.8.8"
    assert release.installer_size == 123
    assert release.installer_sha256 == "a" * 64
    assert release.checksum_url.endswith(".sha256")


def test_verify_installer_accepts_matching_release_digest():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "setup.exe"
        path.write_bytes(b"tertium update")
        digest = self_update.sha256_file(path)
        release = self_update.ReleaseInfo(
            version="9.9.9",
            tag="v9.9.9",
            page_url="",
            installer_url="",
            installer_sha256=digest,
        )
        assert self_update.verify_installer(path, release) == digest


def test_verify_installer_rejects_mismatch():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "setup.exe"
        path.write_bytes(b"bad update")
        release = self_update.ReleaseInfo(
            version="9.9.9",
            tag="v9.9.9",
            page_url="",
            installer_url="",
            installer_sha256="0" * 64,
        )
        try:
            self_update.verify_installer(path, release)
        except self_update.SelfUpdateError:
            pass
        else:
            raise AssertionError("checksum mismatch must fail")


def test_launcher_self_update_ui_contract():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app.py").read_text(encoding="utf-8")
    installer = (root / "installer" / "TertiumModManager.iss").read_text(encoding="utf-8")
    assert "UPDATE TERTIUM" in source
    assert "Check for Update" in source
    assert "def check_self_update" in source
    assert "def install_app_update" in source
    assert "schedule_windows_installer" in source
    assert "verify_installer" in source
    desktop_line = next(line for line in installer.splitlines() if 'Name: "desktopicon"' in line)
    assert "unchecked" not in desktop_line
