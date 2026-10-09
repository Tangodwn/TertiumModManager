from pathlib import Path
import tempfile

import nexus


class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class FakeResponse:
    def __init__(self, chunks, total):
        self.chunks = list(chunks)
        self.headers = FakeHeaders({"Content-Length": str(total)})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _size=-1):
        return self.chunks.pop(0) if self.chunks else b""


def test_download_file_is_atomic(monkeypatch):
    payload = b"abc" * 100
    monkeypatch.setattr(nexus.urllib.request, "urlopen", lambda *a, **k: FakeResponse([payload], len(payload)))
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "mod.zip"
        result = nexus.download_file("https://example.invalid/mod.zip", dest)
        assert result == dest
        assert dest.read_bytes() == payload
        assert not (Path(td) / "mod.zip.part").exists()


def test_download_file_removes_partial_on_failure(monkeypatch):
    class BrokenResponse(FakeResponse):
        def read(self, _size=-1):
            if self.chunks:
                return self.chunks.pop(0)
            raise OSError("connection lost")

    monkeypatch.setattr(nexus.urllib.request, "urlopen", lambda *a, **k: BrokenResponse([b"partial"], 100))
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "mod.zip"
        try:
            nexus.download_file("https://example.invalid/mod.zip", dest)
        except Exception:
            pass
        else:
            raise AssertionError("failed download should raise")
        assert not dest.exists()
        assert not (Path(td) / "mod.zip.part").exists()


def test_browser_authorization_required_detects_nexus_permission_fallback():
    assert nexus.browser_authorization_required(
        RuntimeError("Nexus requires browser authorization for this download.")
    )
    assert nexus.browser_authorization_required(
        RuntimeError("Click Mod Manager Download so the nxm:// link includes a temporary authorization key.")
    )
    assert not nexus.browser_authorization_required(RuntimeError("network cable unplugged"))


def test_parse_nexus_mod_reference_accepts_id_and_darktide_urls():
    assert nexus.parse_nexus_mod_reference("123") == 123
    assert nexus.parse_nexus_mod_reference("https://www.nexusmods.com/warhammer40kdarktide/mods/456") == 456
    assert nexus.parse_nexus_mod_reference("https://www.nexusmods.com/games/warhammer40kdarktide/mods/789") == 789


def test_parse_nexus_mod_reference_rejects_non_nexus_url():
    try:
        nexus.parse_nexus_mod_reference("https://example.com/warhammer40kdarktide/mods/456")
    except Exception:
        pass
    else:
        raise AssertionError("non-Nexus URL should be rejected")


def test_matching_files_for_version_is_conservative_and_ignores_leading_v():
    payload = {
        "files": [
            {"file_id": 1, "version": "v1.2.3", "name": "main"},
            {"file_id": 2, "version": "1.2.4", "name": "new"},
        ]
    }
    matches = nexus.matching_files_for_version(payload, "1.2.3")
    assert [x["file_id"] for x in matches] == [1]
    assert nexus.matching_files_for_version(payload, "local") == []
