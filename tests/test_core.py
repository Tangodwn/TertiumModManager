from pathlib import Path
import tempfile
import zipfile

from core import ModRecord, RegistryStore, detect_mod_roots, extract_archive, parse_steam_library_paths, toggle_mod_folder
from nexus import parse_nxm_url


def test_parse_nxm():
    x = parse_nxm_url("nxm://warhammer40kdarktide/mods/123/files/456?key=abc&expires=999&user_id=1")
    assert x.domain == "warhammer40kdarktide"
    assert x.mod_id == 123
    assert x.file_id == 456
    assert x.key == "abc"
    assert x.expires == "999"


def test_vdf_paths():
    text = '"libraryfolders" { "0" { "path" "C:\\\\Program Files (x86)\\\\Steam" } "1" { "path" "D:\\\\SteamLibrary" } }'
    paths = parse_steam_library_paths(text)
    assert len(paths) == 2
    assert "SteamLibrary" in str(paths[1])


def test_detect_mod_roots_and_toggle():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        mod = game / "mods" / "Example"
        mod.mkdir(parents=True)
        (mod / "Example.mod").write_text("return {}", encoding="utf-8")
        found = detect_mod_roots(game / "mods")
        assert any(p.name == "Example" for p in found)
        renamed = toggle_mod_folder(game, "Example", False)
        assert renamed == "_Example"
        assert (game / "mods" / "_Example").exists()
        renamed = toggle_mod_folder(game, "_Example", True)
        assert renamed == "Example"


def test_zip_slip_blocked():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        archive = root / "bad.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../evil.txt", "no")
        try:
            extract_archive(archive, root / "out")
        except Exception:
            pass
        else:
            raise AssertionError("unsafe ZIP entry was not rejected")


def test_loader_patch_state_and_build_id():
    from core import loader_patch_state, read_steam_build_id
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        steamapps = root / "steamapps"
        game = steamapps / "common" / "Warhammer 40,000 DARKTIDE"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        db = game / "bundle" / "bundle_database.data"
        db.write_bytes(b"vanilla database")
        (steamapps / "appmanifest_1361210.acf").write_text('"AppState" { "buildid" "12345678" }', encoding="utf-8")
        assert loader_patch_state(game) is False
        assert read_steam_build_id(game) == "12345678"
        db.write_bytes(b"prefix 9ba626afa44a3aa3.patch_999 suffix")
        assert loader_patch_state(game) is True




def test_dtkit_shebang_test_double_uses_python_on_windows():
    import sys
    from core import _dtkit_command
    with tempfile.TemporaryDirectory() as td:
        tool = Path(td) / "dtkit-patch"
        tool.write_text("#!/usr/bin/env python3\nprint('ok')\n", encoding="utf-8")
        cmd = _dtkit_command(tool, "--help", platform_name="nt")
        assert cmd[0] == sys.executable
        assert cmd[1] == str(tool)
        assert cmd[2:] == ["--help"]

def test_one_button_repair_with_fake_dtkit():
    import os
    from core import repair_after_game_update, loader_patch_state
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        (game / "tools").mkdir()
        (game / "mods" / "dmf").mkdir(parents=True)
        db = game / "bundle" / "bundle_database.data"
        db.write_bytes(b"vanilla database")
        tool = game / "tools" / "dtkit-patch"
        tool.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, sys\n"
            "if '--help' in sys.argv:\n"
            "    print('usage: dtkit-patch --patch BUNDLE')\n"
            "    raise SystemExit(0)\n"
            "bundle = pathlib.Path(sys.argv[-1])\n"
            "db = bundle / 'bundle_database.data'\n"
            "db.write_bytes(db.read_bytes() + b' 9ba626afa44a3aa3.patch_999')\n",
            encoding="utf-8",
        )
        tool.chmod(0o755)
        store = RegistryStore(root / "state")
        report = repair_after_game_update(game, store)
        assert report["is_patched"] is True
        assert report["dmf_present"] is True
        assert loader_patch_state(game) is True


def test_profiles_capture_and_apply():
    from core import ProfileStore, apply_mod_profile, capture_mod_profile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        for name in ("One", "Two"):
            mod = game / "mods" / name
            mod.mkdir(parents=True)
            (mod / f"{name}.mod").write_text("return {}", encoding="utf-8")
        states = capture_mod_profile(game)
        assert states == {"One": True, "Two": True}
        profiles = ProfileStore(root / "state")
        profiles.save("test", {"One": False, "Two": True})
        result = apply_mod_profile(game, profiles.all()["test"])
        assert result["changed"] == ["One"]
        assert (game / "mods" / "_One").exists()
        assert (game / "mods" / "Two").exists()


def test_diagnostics_bundle_excludes_api_key():
    from core import create_diagnostic_bundle
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        mod = game / "mods" / "Example"
        mod.mkdir(parents=True)
        (mod / "Example.mod").write_text("return {}", encoding="utf-8")
        store = RegistryStore(root / "state")
        out = root / "diag.zip"
        create_diagnostic_bundle(game, store, out, "test")
        assert out.exists()
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
            assert "diagnostics.json" in names
            text = zf.read("diagnostics.json").decode("utf-8")
            assert "api_key" not in text.lower()
            assert "Example" in text


def test_repair_backup_is_build_compatible_and_can_restore():
    from core import repair_after_game_update, restore_latest_loader_backup, loader_patch_state
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        steamapps = root / "steamapps"
        game = steamapps / "common" / "Warhammer 40,000 DARKTIDE"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        (game / "tools").mkdir()
        (game / "mods" / "dmf").mkdir(parents=True)
        (steamapps / "appmanifest_1361210.acf").write_text('"AppState" { "buildid" "777" }', encoding="utf-8")
        db = game / "bundle" / "bundle_database.data"
        db.write_bytes(b"vanilla current build")
        tool = game / "tools" / "dtkit-patch"
        tool.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, sys\n"
            "if '--help' in sys.argv:\n"
            "    print('usage: --patch')\n"
            "    raise SystemExit(0)\n"
            "db = pathlib.Path(sys.argv[-1]) / 'bundle_database.data'\n"
            "db.write_bytes(db.read_bytes() + b' 9ba626afa44a3aa3.patch_999')\n",
            encoding="utf-8",
        )
        tool.chmod(0o755)
        store = RegistryStore(root / "state")
        report = repair_after_game_update(game, store)
        assert report["is_patched"] is True
        restored = restore_latest_loader_backup(game, store)
        assert restored.exists()
        assert loader_patch_state(game) is False
        assert db.read_bytes() == b"vanilla current build"


def test_zip_absolute_path_blocked():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        archive = root / "absolute.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("C:/evil.txt", "no")
        try:
            extract_archive(archive, root / "out")
        except Exception:
            pass
        else:
            raise AssertionError("absolute archive entry was not rejected")


def test_dml_install_preserves_mod_load_order_and_user_mods():
    from core import DML_MOD_ID, install_archive
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        (game / "mods" / "UserMod").mkdir(parents=True)
        (game / "mods" / "UserMod" / "UserMod.mod").write_text("return {}", encoding="utf-8")
        load_order = game / "mods" / "mod_load_order.txt"
        load_order.write_text("UserMod\n", encoding="utf-8")
        archive = root / "dml.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("mods/mod_load_order.txt", "SHOULD NOT REPLACE\n")
            zf.writestr("mods/base/mod_manager.lua", "loader base")
            zf.writestr("tools/dtkit-patch.exe", "fake")
            zf.writestr("toggle_darktide_mods.bat", "fake")
        store = RegistryStore(root / "state")
        rec = ModRecord(mod_id=DML_MOD_ID, file_id=1, name="DML")
        install_archive(game, archive, rec, store)
        assert load_order.read_text(encoding="utf-8") == "UserMod\n"
        assert (game / "mods" / "UserMod" / "UserMod.mod").exists()
        assert (game / "mods" / "base" / "mod_manager.lua").read_text(encoding="utf-8") == "loader base"
        assert (game / "tools" / "dtkit-patch.exe").exists()


def test_dml_first_install_creates_load_order():
    from core import DML_MOD_ID, install_archive
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        archive = root / "dml.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("mods/mod_load_order.txt", "-- generated by DML\n")
            zf.writestr("mods/base/mod_manager.lua", "loader base")
            zf.writestr("tools/dtkit-patch.exe", "fake")
        store = RegistryStore(root / "state")
        rec = ModRecord(mod_id=DML_MOD_ID, file_id=1, name="DML")
        install_archive(game, archive, rec, store)
        assert (game / "mods" / "mod_load_order.txt").read_text(encoding="utf-8") == "-- generated by DML\n"


def test_unpatch_loader_only_toggles_when_confirmed_patched():
    from core import loader_patch_state, unpatch_loader
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        (game / "tools").mkdir()
        db = game / "bundle" / "bundle_database.data"
        marker = b"9ba626afa44a3aa3.patch_999"
        db.write_bytes(b"vanilla " + marker)
        tool = game / "tools" / "dtkit-patch"
        tool.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, sys\n"
            "if '--help' in sys.argv:\n"
            "    print('usage: --toggle')\n"
            "    raise SystemExit(0)\n"
            "db = pathlib.Path(sys.argv[-1]) / 'bundle_database.data'\n"
            "marker = b'9ba626afa44a3aa3.patch_999'\n"
            "data = db.read_bytes()\n"
            "db.write_bytes(data.replace(marker, b'') if marker in data else data + marker)\n",
            encoding="utf-8",
        )
        tool.chmod(0o755)
        code, _ = unpatch_loader(game)
        assert code == 0
        assert loader_patch_state(game) is False


def test_corrupt_json_is_preserved():
    from core import load_json
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        path = root / "mods.json"
        path.write_text("{definitely broken", encoding="utf-8")
        assert load_json(path, {"mods": []}) == {"mods": []}
        preserved = list(root.glob("mods.json.corrupt-*"))
        assert preserved
        assert preserved[0].read_text(encoding="utf-8") == "{definitely broken"


def test_darktide_running_check_does_not_spawn_tasklist():
    import inspect
    import core

    source = inspect.getsource(core.is_darktide_running)
    assert "tasklist" not in source.casefold()
    assert "_windows_process_image_names" in source


def test_windows_process_enumerator_is_native_no_subprocess():
    import inspect
    import core

    source = inspect.getsource(core._windows_process_image_names)
    assert "subprocess" not in source
    assert "CreateToolhelp32Snapshot" in source
    assert "Process32FirstW" in source
    assert "Process32NextW" in source


def test_removed_mod_is_quarantined_and_restored_with_registry():
    from core import quarantine_mod_folder, restore_latest_removed_mod
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        mod = game / "mods" / "Example"
        mod.mkdir(parents=True)
        (mod / "Example.mod").write_text("return {}", encoding="utf-8")
        store = RegistryStore(root / "state")
        rec = ModRecord(mod_id=123, file_id=456, name="Example", folders=["Example"])
        store.upsert(rec)
        backup = quarantine_mod_folder(game, "Example", store, rec)
        store.remove_mod(123)
        assert backup.exists()
        assert not mod.exists()
        result = restore_latest_removed_mod(game, store)
        assert result["logical_name"] == "Example"
        assert mod.exists()
        assert store.get(123) is not None


def test_install_records_archive_hash_and_verifies_cache():
    from core import install_archive, verify_cached_archive
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        store = RegistryStore(root / "state")
        archive = store.cache_archive_path(321, 654, "Example.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("Example/Example.mod", "return {}")
        rec = ModRecord(mod_id=321, file_id=654, name="Example", file_name="Example.zip")
        installed = install_archive(game, archive, rec, store)
        assert installed.archive_sha256
        assert installed.archive_size == archive.stat().st_size
        assert verify_cached_archive(installed, store) is True
        archive.write_bytes(b"tampered")
        assert verify_cached_archive(installed, store) is False


def test_profile_load_order_excludes_dmf():
    from core import apply_mod_profile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        for name in ("dmf", "Example"):
            mod = game / "mods" / name
            mod.mkdir(parents=True)
            (mod / f"{name}.mod").write_text("return {}", encoding="utf-8")
        apply_mod_profile(game, {"Example": True}, maintain_load_order=True)
        text = (game / "mods" / "mod_load_order.txt").read_text(encoding="utf-8")
        assert "Example" in text
        assert "dmf" not in text.lower()


def test_zip_symlink_blocked():
    import stat
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        archive = root / "symlink.zip"
        info = zipfile.ZipInfo("Example/link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(info, "../../outside")
        try:
            extract_archive(archive, root / "out")
        except Exception:
            pass
        else:
            raise AssertionError("symlink archive entry was not rejected")


def test_windows_ads_and_reserved_names_blocked():
    from core import _unsafe_archive_name
    assert _unsafe_archive_name("Example/file.txt:evil") is True
    assert _unsafe_archive_name("Example/CON.txt") is True
    assert _unsafe_archive_name("Example/good.lua") is False


def test_game_dir_writable_probe_cleans_up():
    from core import game_dir_writable
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        assert game_dir_writable(root) is True
        assert not list(root.glob('.tertium-write-test-*'))


def test_mod_update_preserves_disabled_state_and_can_rollback():
    from core import install_archive, rollback_mod_update, sync_registry_folders, toggle_mod_folder
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        store = RegistryStore(root / "state")

        v1 = root / "v1.zip"
        with zipfile.ZipFile(v1, "w") as zf:
            zf.writestr("Example/Example.mod", "return {}")
            zf.writestr("Example/version.txt", "v1")
        rec1 = ModRecord(mod_id=555, file_id=1, name="Example", version="1.0", file_name="v1.zip")
        install_archive(game, v1, rec1, store)
        toggle_mod_folder(game, "Example", False)
        sync_registry_folders(game, store)
        assert (game / "mods" / "_Example").exists()
        assert store.get(555).folders == ["_Example"]

        v2 = root / "v2.zip"
        with zipfile.ZipFile(v2, "w") as zf:
            zf.writestr("Example/Example.mod", "return {}")
            zf.writestr("Example/version.txt", "v2")
        rec2 = ModRecord(mod_id=555, file_id=2, name="Example", version="2.0", file_name="v2.zip")
        install_archive(game, v2, rec2, store)
        assert not (game / "mods" / "Example").exists()
        assert (game / "mods" / "_Example" / "version.txt").read_text(encoding="utf-8") == "v2"
        assert store.get(555).enabled is False

        result = rollback_mod_update(game, store, 555)
        assert result["version"] == "1.0"
        assert (game / "mods" / "_Example" / "version.txt").read_text(encoding="utf-8") == "v1"
        restored = store.get(555)
        assert restored.file_id == 1
        assert restored.enabled is False


def test_version_files_stay_in_sync():
    from version import __version__
    root = Path(__file__).resolve().parents[1]
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == __version__
    assert f'version = "{__version__}"' in (root / "pyproject.toml").read_text(encoding="utf-8")
    windows_info = (root / "windows_version_info.txt").read_text(encoding="utf-8")
    assert f"StringStruct('FileVersion', '{__version__}')" in windows_info
    assert f"StringStruct('ProductVersion', '{__version__}')" in windows_info
    numeric = tuple(int(x) for x in __version__.split(".")) + (0,)
    assert f"filevers={numeric}" in windows_info
    assert f"prodvers={numeric}" in windows_info
    installer = (root / "installer" / "TertiumModManager.iss").read_text(encoding="utf-8")
    assert "#ifndef MyAppVersion" in installer
    assert '#define MyAppVersion "' not in installer
    builder = (root / "scripts" / "build_windows_release.ps1").read_text(encoding="utf-8")
    assert '"/DMyAppVersion=$AppVersion"' in builder
    quick = (root / "QUICK_START_WINDOWS.txt").read_text(encoding="utf-8")
    assert __version__ in quick
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert f"Current version: **{__version__}" in readme
    assert (root / f"RELEASE_NOTES_{__version__}.md").exists()


def test_repair_reapplies_verified_cached_aml_after_dml_overwrite():
    from core import AML_MOD_ID, install_archive, repair_after_game_update
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        (game / "tools").mkdir()
        (game / "mods" / "base").mkdir(parents=True)
        (game / "mods" / "dmf").mkdir(parents=True)
        db = game / "bundle" / "bundle_database.data"
        db.write_bytes(b"vanilla")
        tool = game / "tools" / "dtkit-patch"
        tool.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, sys\n"
            "if '--help' in sys.argv:\n"
            "    print('usage: --patch')\n"
            "    raise SystemExit(0)\n"
            "db = pathlib.Path(sys.argv[-1]) / 'bundle_database.data'\n"
            "db.write_bytes(db.read_bytes() + b' 9ba626afa44a3aa3.patch_999')\n",
            encoding="utf-8",
        )
        tool.chmod(0o755)

        store = RegistryStore(root / "state")
        aml_archive = store.cache_archive_path(AML_MOD_ID, 99, "aml.zip")
        with zipfile.ZipFile(aml_archive, "w") as zf:
            zf.writestr("base/mod_manager.lua", "AML PATCH")
        aml_record = ModRecord(mod_id=AML_MOD_ID, file_id=99, name="AML", file_name="aml.zip")
        install_archive(game, aml_archive, aml_record, store)
        assert (game / "mods" / "base" / "mod_manager.lua").read_text(encoding="utf-8") == "AML PATCH"

        # Simulate DML/game work replacing the AML-patched base file and disabling loader patching.
        (game / "mods" / "base" / "mod_manager.lua").write_text("DML BASE", encoding="utf-8")
        db.write_bytes(b"fresh game database")

        def reapply():
            rec = store.get(AML_MOD_ID)
            cached = store.cache_archive_path(rec.mod_id, rec.file_id, rec.file_name)
            install_archive(game, cached, rec, store)

        report = repair_after_game_update(game, store, reapply_aml=reapply)
        assert report["is_patched"] is True
        assert report["aml_reapplied"] is True
        assert (game / "mods" / "base" / "mod_manager.lua").read_text(encoding="utf-8") == "AML PATCH"


def test_troubleshooting_safe_mode_restores_exact_states():
    from core import enter_troubleshooting_safe_mode, restore_latest_safe_mode
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        for folder in ("dmf", "Alpha", "_Bravo"):
            mod = game / "mods" / folder
            mod.mkdir(parents=True)
            logical = folder.lstrip("_")
            (mod / f"{logical}.mod").write_text("return {}", encoding="utf-8")
        store = RegistryStore(root / "state")

        result = enter_troubleshooting_safe_mode(game, store)
        assert "Alpha" in result["changed"]
        assert (game / "mods" / "_Alpha").exists()
        assert (game / "mods" / "_Bravo").exists()
        assert (game / "mods" / "dmf").exists()

        restored = restore_latest_safe_mode(game, store)
        assert "Alpha" in restored["changed"]
        assert (game / "mods" / "Alpha").exists()
        assert (game / "mods" / "_Bravo").exists()
        assert (game / "mods" / "dmf").exists()


def test_mod_structure_audit_detects_duplicates_and_orphans():
    from core import audit_mod_structure
    with tempfile.TemporaryDirectory() as td:
        game = Path(td) / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        for folder in ("Echo", "_Echo"):
            mod = game / "mods" / folder
            mod.mkdir(parents=True)
            (mod / "Echo.mod").write_text("return {}", encoding="utf-8")
        (game / "mods" / "Orphan").mkdir(parents=True)
        issues = audit_mod_structure(game)
        messages = "\n".join(item["message"] for item in issues)
        assert "Duplicate logical mod folder" in messages
        assert "no top-level .mod descriptor" in messages


def test_stale_partial_download_cleanup_only_removes_old_parts():
    import os
    import time as _time
    from core import cleanup_stale_download_parts
    with tempfile.TemporaryDirectory() as td:
        store = RegistryStore(Path(td) / "state")
        old = store.cache_dir / "old.zip.part"
        fresh = store.cache_dir / "fresh.zip.part"
        old.write_bytes(b"old")
        fresh.write_bytes(b"fresh")
        old_time = _time.time() - 24 * 3600
        os.utime(old, (old_time, old_time))
        result = cleanup_stale_download_parts(store, older_than_seconds=6 * 3600)
        assert result["removed"] == 1
        assert not old.exists()
        assert fresh.exists()


def test_setup_manifest_is_secret_free_and_comparable():
    from core import ProfileStore, export_setup_manifest, load_setup_manifest, compare_setup_manifest, toggle_mod_folder
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        mod = game / "mods" / "Example"
        mod.mkdir(parents=True)
        (mod / "Example.mod").write_text("return {}", encoding="utf-8")
        store = RegistryStore(root / "state")
        store.upsert(ModRecord(mod_id=77, file_id=88, name="Example", version="1.0", folders=["Example"]))
        profiles = ProfileStore(store.root)
        profiles.save("Default", {"Example": True})
        dest = root / "setup.json"
        export_setup_manifest(game, store, profiles, dest, "9.9.9")
        text = dest.read_text(encoding="utf-8")
        assert "api_key" not in text.lower()
        manifest = load_setup_manifest(dest)
        same = compare_setup_manifest(game, store, manifest)
        assert not same["missing_folders"]
        assert not same["extra_folders"]
        assert not same["state_differences"]
        assert not same["nexus_differences"]

        toggle_mod_folder(game, "Example", False)
        changed = compare_setup_manifest(game, store, manifest)
        assert changed["state_differences"][0]["name"] == "Example"


def test_manual_load_order_preserves_existing_order_and_comments():
    from core import write_mod_load_order
    with tempfile.TemporaryDirectory() as td:
        game = Path(td) / "game"
        (game / "mods").mkdir(parents=True)
        load = game / "mods" / "mod_load_order.txt"
        load.write_text("-- UI mods\nZulu\nAlpha\n\n-- testing\nDisabledOld\n", encoding="utf-8")
        write_mod_load_order(game, ["Alpha", "Zulu", "Bravo"])
        text = load.read_text(encoding="utf-8")
        assert text.index("Zulu") < text.index("Alpha")
        assert "-- UI mods" in text
        assert "-- testing" in text
        assert "DisabledOld" not in text
        assert text.rstrip().endswith("Bravo")


def test_dependency_audit_reports_missing_and_manual_order_violations():
    from core import audit_mod_dependencies
    with tempfile.TemporaryDirectory() as td:
        game = Path(td) / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        alpha = game / "mods" / "Alpha"
        beta = game / "mods" / "Beta"
        alpha.mkdir(parents=True)
        beta.mkdir(parents=True)
        (alpha / "Alpha.mod").write_text(
            'return { require = {"MissingThing"}, load_after = {"Beta"}, packages = {} }',
            encoding="utf-8",
        )
        (beta / "Beta.mod").write_text('return { packages = {} }', encoding="utf-8")
        (game / "mods" / "mod_load_order.txt").write_text("Alpha\nBeta\n", encoding="utf-8")
        issues = audit_mod_dependencies(game, aml_active=False)
        messages = "\n".join(item["message"] for item in issues)
        assert "Requires 'MissingThing'" in messages
        assert "load_after 'Beta'" in messages


def test_dependency_audit_skips_order_warning_when_aml_active():
    from core import audit_mod_dependencies
    with tempfile.TemporaryDirectory() as td:
        game = Path(td) / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        alpha = game / "mods" / "Alpha"
        beta = game / "mods" / "Beta"
        alpha.mkdir(parents=True)
        beta.mkdir(parents=True)
        (alpha / "Alpha.mod").write_text('return { load_after = {"Beta"}, packages = {} }', encoding="utf-8")
        (beta / "Beta.mod").write_text('return { packages = {} }', encoding="utf-8")
        (game / "mods" / "mod_load_order.txt").write_text("Alpha\nBeta\n", encoding="utf-8")
        issues = audit_mod_dependencies(game, aml_active=True)
        assert not any("load_after" in item["message"] for item in issues)


def test_zip_windows_case_collision_is_blocked():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        archive = root / "collision.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("Example/File.txt", "a")
            zf.writestr("example/file.txt", "b")
        try:
            extract_archive(archive, root / "out")
        except Exception as exc:
            assert "colliding duplicate" in str(exc).lower()
        else:
            raise AssertionError("case-insensitive Windows path collision was not rejected")


def test_zip_windows_trailing_dot_path_is_blocked():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        archive = root / "trailing.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("Example./Example.mod", "return {}")
        try:
            extract_archive(archive, root / "out")
        except Exception:
            pass
        else:
            raise AssertionError("Windows trailing-dot path was not rejected")


def test_scan_reads_version_from_mod_descriptor_when_info_json_missing():
    from core import scan_installed_mods
    with tempfile.TemporaryDirectory() as td:
        game = Path(td) / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        mod = game / "mods" / "Versioned"
        mod.mkdir(parents=True)
        (mod / "Versioned.mod").write_text('return { version = "2.4.1", packages = {} }', encoding="utf-8")
        scanned = scan_installed_mods(game)
        assert scanned[0]["version"] == "2.4.1"


def test_restore_latest_mod_state_prefers_newest_unrestored_snapshot():
    from core import create_mod_state_snapshot, restore_latest_mod_state, toggle_mod_folder
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        mod = game / "mods" / "Alpha"
        mod.mkdir(parents=True)
        (mod / "Alpha.mod").write_text("return {}", encoding="utf-8")
        store = RegistryStore(root / "state")

        create_mod_state_snapshot(game, store, "pre-profile-apply")
        toggle_mod_folder(game, "Alpha", False)
        newest = create_mod_state_snapshot(game, store, "pre-update-all")
        toggle_mod_folder(game, "Alpha", True)

        result = restore_latest_mod_state(game, store)
        assert Path(result["snapshot"]) == newest
        assert (game / "mods" / "_Alpha").exists()
        assert not (game / "mods" / "Alpha").exists()


def test_health_report_exposes_generic_mod_state_restore_availability():
    from core import create_mod_state_snapshot, health_report
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        (game / "mods").mkdir()
        store = RegistryStore(root / "state")
        create_mod_state_snapshot(game, store, "pre-profile-apply")
        report = health_report(game, store)
        assert report["mod_state_restore_available"] is True


def test_dependency_audit_flags_enabled_mod_missing_from_manual_load_order():
    from core import audit_mod_dependencies
    with tempfile.TemporaryDirectory() as td:
        game = Path(td) / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        mod = game / "mods" / "Alpha"
        mod.mkdir(parents=True)
        (mod / "Alpha.mod").write_text("return { packages = {} }", encoding="utf-8")
        (game / "mods" / "mod_load_order.txt").write_text("", encoding="utf-8")
        issues = audit_mod_dependencies(game, aml_active=False)
        assert any(item["severity"] == "error" and "missing from mod_load_order" in item["message"] for item in issues)


def test_dependency_audit_flags_stale_manual_load_order_entry():
    from core import audit_mod_dependencies
    with tempfile.TemporaryDirectory() as td:
        game = Path(td) / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        (game / "mods").mkdir()
        (game / "mods" / "mod_load_order.txt").write_text("MissingMod\n", encoding="utf-8")
        issues = audit_mod_dependencies(game, aml_active=False)
        assert any("missing or currently disabled" in item["message"] for item in issues)


def test_windows_native_packaging_files_are_present_and_tracked():
    root = Path(__file__).resolve().parents[1]
    assert (root / "TertiumModManager.spec").exists()
    assert (root / "installer" / "TertiumModManager.iss").exists()
    assert (root / "scripts" / "build_windows_release.ps1").exists()
    assert (root / "MAKE_WINDOWS_RELEASE.cmd").exists()
    assert (root / "MAKE_WINDOWS_INSTALLER.cmd").exists()
    icon = root / "assets" / "tertium.ico"
    assert icon.exists() and icon.stat().st_size > 1000
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert "*.spec" not in gitignore


def test_crash_parser_identifies_mod_directly_in_stack():
    from core import parse_darktide_crash_log
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "console-2026-10-08-00.56.58-08984781-a3ad-4a30-b3e2-c47e625b0c19.log"
        path.write_text(
            "[Session] 08984781-a3ad-4a30-b3e2-c47e625b0c19\n"
            "<<Script Error>>...player_husk_data_extension.lua:277: attempt to index local 'field' (a nil value)<</Script Error>>\n"
            "<<Lua Stack>>  [1] @scripts/extension_systems/unit_data/player_husk_data_extension.lua:277: in function __index\n"
            "  [2] ./../mods/NumericUI/scripts/mods/NumericUI/TeamPlayerPanel.lua:507: in function hook_chain<</Lua Stack>>\n",
            encoding="utf-8",
        )
        finding = parse_darktide_crash_log(path, [{"logical_name": "NumericUI"}])
        assert finding is not None
        assert finding["guid"] == "08984781-a3ad-4a30-b3e2-c47e625b0c19"
        assert finding["candidates"][0]["logical_name"] == "NumericUI"
        assert finding["candidates"][0]["confidence"] == "high"


def test_recent_crash_guard_does_not_resurface_older_crash_after_clean_session(monkeypatch):
    import core
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        (game / "mods").mkdir()
        crash = root / "console-old.log"
        crash.write_text(
            "<<Lua Error>>boom<</Lua Error>>\n"
            "<<Lua Stack>>./../mods/BadMod/scripts/mods/BadMod/main.lua:1<</Lua Stack>>",
            encoding="utf-8",
        )
        clean = root / "console-new.log"
        clean.write_text("normal clean session\n", encoding="utf-8")
        # The candidate provider is already sorted newest-first in production.
        monkeypatch.setattr(core, "_console_log_candidates", lambda _limit=30: [clean, crash])
        assert core.analyze_recent_crash(game) is None


def test_guardian_ui_command_methods_exist():
    from app import TertiumApp
    required = {
        "_apply_interface_mode",
        "_change_interface_mode",
        "_refresh_official_news_async",
        "_open_official_news",
        "_refresh_crash_guard",
        "disable_crash_suspect_and_retry",
        "launch_crash_anyway",
        "show_crash_guard_details",
        "dismiss_crash_notice",
        "adopt_existing_mods",
        "open_nexus_catalog",
    }
    assert all(hasattr(TertiumApp, name) for name in required)


def test_guardian_background_is_packaged():
    root = Path(__file__).resolve().parents[1]
    background = root / "assets" / "tertium_hive.png"
    assert background.exists() and background.stat().st_size > 100_000
    spec = (root / "TertiumModManager.spec").read_text(encoding="utf-8")
    assert "tertium_hive.png" in spec


def test_build_aware_quarantine_expires_after_mod_or_game_update():
    from core import CompatibilityStore, active_quarantines
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        steamapps = root / "steamapps"
        game = steamapps / "common" / "Warhammer 40,000 DARKTIDE"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        mod = game / "mods" / "Example"
        mod.mkdir(parents=True)
        descriptor = mod / "Example.mod"
        descriptor.write_text('return { version = "1.0", packages = {} }', encoding="utf-8")
        (steamapps / "appmanifest_1361210.acf").write_text('"AppState" { "buildid" "100" }', encoding="utf-8")
        compatibility = CompatibilityStore(root / "state")
        compatibility.set_quarantined("Example", "100", "1.0", True, "test")
        assert len(active_quarantines(game, compatibility)) == 1

        descriptor.write_text('return { version = "1.1", packages = {} }', encoding="utf-8")
        assert active_quarantines(game, compatibility) == []

        descriptor.write_text('return { version = "1.0", packages = {} }', encoding="utf-8")
        (steamapps / "appmanifest_1361210.acf").write_text('"AppState" { "buildid" "101" }', encoding="utf-8")
        assert active_quarantines(game, compatibility) == []


def test_guardian_always_starts_in_simple_mode_even_after_prior_advanced_session():
    from app import startup_interface_mode
    assert startup_interface_mode({}) == "simple"
    assert startup_interface_mode({"interface_mode": "advanced"}) == "simple"


def test_guardian_settings_exposes_explicit_advanced_mode_toggle():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app.py").read_text(encoding="utf-8")
    assert "ENTER ADVANCED MODE" in source
    assert "RETURN TO SIMPLE MODE" in source
    assert "def _toggle_interface_mode" in source
    # Advanced Mode is intentionally not written back as a startup preference.
    mode_block = source[source.index("def _change_interface_mode"):source.index("def _apply_interface_mode")]
    assert 'self.config["interface_mode"]' not in mode_block


def test_guardian_automation_reconciles_manual_mods_and_prunes_removed_local_records():
    from core import reconcile_installed_registry
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        mod = game / "mods" / "ManualMod"
        mod.mkdir(parents=True)
        (mod / "ManualMod.mod").write_text('return { version = "1.2.3" }', encoding="utf-8")
        store = RegistryStore(root / "state")

        first = reconcile_installed_registry(game, store)
        assert first["adopted_count"] == 1
        adopted = [r for r in store.all() if r.source == "local"]
        assert len(adopted) == 1
        assert adopted[0].folders == ["ManualMod"]
        assert adopted[0].version == "1.2.3"

        # Reconciliation is idempotent and does not create duplicate placeholders.
        second = reconcile_installed_registry(game, store)
        assert second["adopted_count"] == 0
        assert len([r for r in store.all() if r.source == "local"]) == 1

        # If the manually-managed folder really disappears, its local-only registry
        # placeholder is retired automatically instead of lingering forever.
        import shutil
        shutil.rmtree(mod)
        third = reconcile_installed_registry(game, store)
        assert third["pruned_count"] == 1
        assert store.all() == []


def test_nexus_install_promotes_matching_adopted_local_mod_and_preserves_disabled_state():
    from core import install_archive, reconcile_installed_registry
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        local = game / "mods" / "_NumericUI"
        local.mkdir(parents=True)
        (local / "NumericUI.mod").write_text('return { version = "1.0" }', encoding="utf-8")
        (local / "version.txt").write_text("local-v1", encoding="utf-8")
        store = RegistryStore(root / "state")
        reconcile_installed_registry(game, store)
        old = next(r for r in store.all() if r.source == "local")
        assert old.enabled is False

        archive = root / "numericui-v2.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("NumericUI/NumericUI.mod", 'return { version = "2.0" }')
            zf.writestr("NumericUI/version.txt", "nexus-v2")
        record = ModRecord(
            mod_id=999,
            file_id=200,
            name="Numeric UI",
            version="2.0",
            file_name=archive.name,
            source="nexus",
        )
        installed = install_archive(game, archive, record, store)

        assert installed.folders == ["_NumericUI"]
        assert installed.enabled is False
        assert (game / "mods" / "_NumericUI" / "version.txt").read_text(encoding="utf-8") == "nexus-v2"
        assert store.get(999) is not None
        assert all(r.mod_id != old.mod_id for r in store.all())
        assert len(store.all()) == 1


def test_promoted_nexus_mod_can_rollback_to_previous_local_install():
    from core import install_archive, reconcile_installed_registry, rollback_mod_update
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        local = game / "mods" / "Example"
        local.mkdir(parents=True)
        (local / "Example.mod").write_text('return { version = "local" }', encoding="utf-8")
        (local / "payload.txt").write_text("old-local", encoding="utf-8")
        store = RegistryStore(root / "state")
        reconcile_installed_registry(game, store)
        old = next(r for r in store.all() if r.source == "local")

        archive = root / "example-nexus.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("Example/Example.mod", 'return { version = "2.0" }')
            zf.writestr("Example/payload.txt", "new-nexus")
        install_archive(
            game,
            archive,
            ModRecord(mod_id=777, file_id=2, name="Example Mod", version="2.0", file_name=archive.name, source="nexus"),
            store,
        )
        assert store.get(777) is not None
        assert (game / "mods" / "Example" / "payload.txt").read_text(encoding="utf-8") == "new-nexus"

        result = rollback_mod_update(game, store, 777)
        assert result["version"] == "local"
        assert (game / "mods" / "Example" / "payload.txt").read_text(encoding="utf-8") == "old-local"
        assert store.get(777) is None
        restored = store.get(old.mod_id)
        assert restored is not None and restored.source == "local"


def test_modded_launch_preflight_blocks_missing_required_dependency():
    from core import modded_launch_preflight
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        game = root / "game"
        (game / "bundle").mkdir(parents=True)
        (game / "binaries").mkdir()
        mod = game / "mods" / "NeedsHelper"
        mod.mkdir(parents=True)
        (mod / "NeedsHelper.mod").write_text(
            'return { version = "1.0", require = { "MissingHelper" } }',
            encoding="utf-8",
        )
        # Manual mode load-order itself is valid so the dependency is the blocker under test.
        (game / "mods" / "mod_load_order.txt").write_text("NeedsHelper\n", encoding="utf-8")
        store = RegistryStore(root / "state")
        report = modded_launch_preflight(game, store)
        assert report["ok"] is False
        assert any("MissingHelper" in str(item.get("message")) for item in report["blockers"])
        assert report["reconcile"]["adopted_count"] == 1


def test_guardian_automation_is_wired_into_refresh_and_modded_launch():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app.py").read_text(encoding="utf-8")
    assert "reconcile_installed_registry(self.game_dir, self.store)" in source
    assert "modded_launch_preflight(self.game_dir, self.store)" in source
    assert "local-only mod(s)" in source


def test_streamlined_installer_builder_omits_portable_in_installer_only_mode():
    root = Path(__file__).resolve().parents[1]
    cmd = (root / "MAKE_WINDOWS_INSTALLER.cmd").read_text(encoding="utf-8")
    builder = (root / "scripts" / "build_windows_release.ps1").read_text(encoding="utf-8")
    assert "-InstallerOnly" in cmd
    assert "[switch]$InstallerOnly" in builder
    assert "Installer-only mode: portable ZIP omitted." in builder
    assert "TertiumModManager-Setup-x64.exe" in cmd


def test_github_windows_ci_and_release_workflows_are_present():
    root = Path(__file__).resolve().parents[1]
    ci = (root / ".github" / "workflows" / "windows-ci.yml").read_text(encoding="utf-8")
    release = (root / ".github" / "workflows" / "windows-release.yml").read_text(encoding="utf-8")
    assert "windows-latest" in ci
    assert 'python-version: ["3.11", "3.12", "3.13"]' in ci
    assert 'tags: ["v*"]' in release
    assert "Verify release tag matches VERSION" in release
    assert "-InstallerOnly -SkipTests" in release
    assert "TertiumModManager-Setup-x64.exe.sha256" in release
    # Normal release assets deliberately exclude the portable ZIP.
    publish = release[release.index("Publish GitHub release assets"):]
    assert "Portable-x64.zip" not in publish


def test_guardian_recovery_and_nexus_fallback_source_contract():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app.py").read_text(encoding="utf-8")
    assert 'text="Launch Anyway"' in source
    assert 'text="Advanced Details"' in source
    assert 'def repair_and_launch(self, skip_crash_guard: bool = False)' in source
    retry = source[source.index("def disable_crash_suspect_and_retry"):source.index("def launch_crash_anyway")]
    assert "askyesno" not in retry
    assert "skip_crash_guard=True" in retry

    update = source[source.index("def update_all"):source.index("def _collect_guided_updates_worker")]
    assert "nexus_is_premium" not in update
    assert "_update_all_worker" in update

    worker = source[source.index("def _update_all_worker"):source.index("def save_profile")]
    assert "browser_authorization_required" in worker
    assert 'self.queue.put(("guided_updates", remaining))' in worker
    assert "Run Update All again" not in worker


def test_link_local_record_to_nexus_preserves_install_state_and_removes_placeholder():
    from core import ModRecord, RegistryStore, link_local_record_to_nexus
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = RegistryStore(root)
        local = ModRecord(
            mod_id=-123,
            file_id=0,
            name="NumericUI",
            version="26.02.08-1",
            folders=["_NumericUI"],
            enabled=False,
            installed_at=123.0,
            source="local",
        )
        store.upsert(local)
        linked = link_local_record_to_nexus(
            store,
            -123,
            42,
            {"name": "Numeric UI"},
            {"file_id": 99, "version": "26.02.08-1", "file_name": "NumericUI.zip", "category_id": 3},
        )
        assert linked.mod_id == 42
        assert linked.file_id == 99
        assert linked.folders == ["_NumericUI"]
        assert linked.enabled is False
        assert linked.installed_at == 123.0
        assert linked.source == "nexus"
        assert store.get(-123) is None
        assert store.get(42).name == "Numeric UI"
        backups = list(store.backup_dir.glob("registry-link-*.json"))
        assert len(backups) == 1


def test_link_local_record_to_nexus_refuses_duplicate_nexus_target():
    from core import ModManagerError, ModRecord, RegistryStore, link_local_record_to_nexus
    with tempfile.TemporaryDirectory() as td:
        store = RegistryStore(Path(td))
        store.upsert(ModRecord(mod_id=-1, file_id=0, name="Local", folders=["Local"], source="local"))
        store.upsert(ModRecord(mod_id=77, file_id=10, name="Already Linked", folders=["Other"], source="nexus"))
        try:
            link_local_record_to_nexus(
                store,
                -1,
                77,
                {"name": "Wrong Target"},
                {"file_id": 11, "version": "1.0"},
            )
        except ModManagerError:
            pass
        else:
            raise AssertionError("duplicate Nexus target should be rejected")


def test_existing_mod_linker_ui_contract():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app.py").read_text(encoding="utf-8")
    assert "Link Existing to Nexus" in source
    assert "def link_selected_existing_mod" in source
    assert "def _prepare_existing_mod_link_from_nxm" in source
    assert "pending_existing_link_local_id" in source
    assert "LINK ONLY" in source
    assert "link_local_record_to_nexus" in source


def test_link_local_record_to_nexus_allows_unknown_file_baseline():
    from core import ModRecord, RegistryStore, link_local_record_to_nexus
    with tempfile.TemporaryDirectory() as td:
        store = RegistryStore(Path(td))
        store.upsert(ModRecord(
            mod_id=-50,
            file_id=0,
            name="animation_events",
            version="",
            folders=["animation_events"],
            enabled=True,
            installed_at=50.0,
            source="local",
        ))
        linked = link_local_record_to_nexus(
            store,
            -50,
            500,
            {"name": "Animation Events"},
            {},
        )
        assert linked.mod_id == 500
        assert linked.file_id == 0
        assert linked.name == "Animation Events"
        assert linked.folders == ["animation_events"]
        assert linked.enabled is True
        assert store.get(-50) is None
        assert store.get(500).source == "nexus"


def test_auto_nexus_reconciliation_source_contract():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app.py").read_text(encoding="utf-8")
    assert "Auto-Link Existing" in source
    assert "def _start_auto_nexus_reconcile" in source
    assert "def _auto_nexus_reconcile_worker" in source
    assert "unique_catalog_match" in source
    assert "game_mod_catalog" in (root / "nexus.py").read_text(encoding="utf-8")
    assert "nexus-catalog-" in source
    assert "24 * 60 * 60" in source
    # Manual linking remains a fallback, not the normal simple-mode workflow.
    simple_block = source[source.index("if simple:"):source.index("else:", source.index("if simple:"))]
    assert "self.manual_link_button.pack_forget()" in simple_block
