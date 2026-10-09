from pathlib import Path
import tempfile

import self_update


def test_version_comparison():
    assert self_update.parse_version("v0.8.14") == (0, 8, 14)
    assert self_update.is_newer_version("0.8.14", "0.8.13")
    assert not self_update.is_newer_version("0.8.13", "0.8.13")
    assert not self_update.is_newer_version("0.8.12", "0.8.13")


def test_fetch_latest_release_reads_package_digest(monkeypatch):
    payload = {
        "tag_name": "v0.8.14",
        "html_url": "https://github.com/Tangodwn/TertiumModManager/releases/tag/v0.8.14",
        "published_at": "2026-10-09T00:00:00Z",
        "assets": [
            {
                "name": "TertiumModManager-0.8.14-Portable-x64.zip",
                "browser_download_url": "https://example.invalid/TertiumModManager-0.8.14-Portable-x64.zip",
                "size": 123,
                "digest": "sha256:" + ("a" * 64),
            },
            {
                "name": "TertiumModManager-0.8.14-Portable-x64.zip.sha256",
                "browser_download_url": "https://example.invalid/TertiumModManager-0.8.14-Portable-x64.zip.sha256",
                "size": 97,
            },
        ],
    }
    monkeypatch.setattr(self_update, "_request_json", lambda *_a, **_k: payload)
    release = self_update.fetch_latest_release()
    assert release.version == "0.8.14"
    assert release.package_name == "TertiumModManager-0.8.14-Portable-x64.zip"
    assert release.package_size == 123
    assert release.package_sha256 == "a" * 64
    assert release.checksum_url.endswith(".sha256")


def test_verify_update_package_accepts_matching_release_digest():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "update.zip"
        path.write_bytes(b"tertium update")
        digest = self_update.sha256_file(path)
        release = self_update.ReleaseInfo(
            version="9.9.9",
            tag="v9.9.9",
            page_url="",
            package_url="",
            package_name="TertiumModManager-9.9.9-Portable-x64.zip",
            package_sha256=digest,
        )
        assert self_update.verify_update_package(path, release) == digest


def test_verify_update_package_rejects_mismatch():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "update.zip"
        path.write_bytes(b"bad update")
        release = self_update.ReleaseInfo(
            version="9.9.9",
            tag="v9.9.9",
            page_url="",
            package_url="",
            package_name="TertiumModManager-9.9.9-Portable-x64.zip",
            package_sha256="0" * 64,
        )
        try:
            self_update.verify_update_package(path, release)
        except self_update.SelfUpdateError:
            pass
        else:
            raise AssertionError("checksum mismatch must fail")


def test_public_release_channel_is_configured():
    assert self_update.RELEASE_REPO == "Tangodwn/TertiumModManager"
    assert "Tangodwn/TertiumModManager" in self_update.RELEASE_API


def test_launcher_self_update_ui_contract():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app.py").read_text(encoding="utf-8")
    workflow = (root / ".github" / "workflows" / "windows-release.yml").read_text(encoding="utf-8")
    assert "UPDATE TERTIUM" in source
    assert "Check for Update" in source
    assert "def check_self_update" in source
    assert "def install_app_update" in source
    assert "schedule_windows_package_update" in source
    assert "verify_update_package" in source
    assert "Portable-x64.zip" in workflow


def test_package_updater_does_not_execute_downloaded_installer():
    source = Path(__file__).resolve().parents[1].joinpath("self_update.py").read_text(encoding="utf-8")
    fn = source[source.index("def schedule_windows_package_update"):]
    assert "Expand-Archive" in fn
    assert "Copy-Item" in fn
    assert "cwd=str(cache)" in fn
    assert "last-update.log" in fn
    assert "Start-Process -FilePath $restart" in fn
    assert "Start-Process -FilePath $installer" not in fn
