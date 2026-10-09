from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable, Iterable

GAME_DOMAIN = "warhammer40kdarktide"
STEAM_APP_ID = "1361210"
GAME_FOLDER_NAME = "Warhammer 40,000 DARKTIDE"
DML_MOD_ID = 19
DMF_MOD_ID = 8
AML_MOD_ID = 246
CORE_MOD_IDS = {DML_MOD_ID, DMF_MOD_ID, AML_MOD_ID}
MAX_ARCHIVE_FILES = 10000
MAX_ARCHIVE_UNPACKED_BYTES = 4 * 1024 * 1024 * 1024


class ModManagerError(RuntimeError):
    pass


@dataclass
class ModRecord:
    mod_id: int
    file_id: int
    name: str
    version: str = ""
    file_name: str = ""
    category_id: int | None = None
    folders: list[str] | None = None
    enabled: bool = True
    installed_at: float = 0.0
    source: str = "nexus"
    archive_sha256: str = ""
    archive_size: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["folders"] = list(self.folders or [])
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModRecord":
        return cls(
            mod_id=int(data["mod_id"]),
            file_id=int(data["file_id"]),
            name=str(data.get("name") or f"Nexus Mod {data['mod_id']}"),
            version=str(data.get("version") or ""),
            file_name=str(data.get("file_name") or ""),
            category_id=data.get("category_id"),
            folders=list(data.get("folders") or []),
            enabled=bool(data.get("enabled", True)),
            installed_at=float(data.get("installed_at") or 0.0),
            source=str(data.get("source") or "nexus"),
            archive_sha256=str(data.get("archive_sha256") or ""),
            archive_size=int(data.get("archive_size") or 0),
        )


def app_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    root = base / "TertiumModManager"
    root.mkdir(parents=True, exist_ok=True)
    return root


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        # Preserve malformed state instead of silently discarding the only copy.
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            corrupt = path.with_name(f"{path.name}.corrupt-{stamp}")
            shutil.copy2(path, corrupt)
        except OSError:
            pass
        return default
    except OSError:
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


class RegistryStore:
    def __init__(self, root: Path | None = None):
        self.root = root or app_data_dir()
        self.path = self.root / "mods.json"
        self.cache_dir = self.root / "cache"
        self.backup_dir = self.root / "backups"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def all(self) -> list[ModRecord]:
        raw = load_json(self.path, {"mods": []})
        return [ModRecord.from_dict(x) for x in raw.get("mods", [])]

    def save_all(self, records: Iterable[ModRecord]) -> None:
        save_json(self.path, {"mods": [r.to_dict() for r in records]})

    def upsert(self, record: ModRecord) -> None:
        records = self.all()
        for i, old in enumerate(records):
            if old.mod_id == record.mod_id and (old.file_id == record.file_id or old.name == record.name):
                records[i] = record
                break
        else:
            # One record per mod page is intentional for this Darktide-focused manager.
            records = [r for r in records if r.mod_id != record.mod_id]
            records.append(record)
        self.save_all(records)

    def remove_mod(self, mod_id: int) -> None:
        self.save_all(r for r in self.all() if r.mod_id != mod_id)

    def get(self, mod_id: int) -> ModRecord | None:
        return next((r for r in self.all() if r.mod_id == mod_id), None)

    def cache_archive_path(self, mod_id: int, file_id: int, file_name: str = "") -> Path:
        suffix = Path(file_name).suffix or ".archive"
        return self.cache_dir / f"{mod_id}-{file_id}{suffix}"


def link_local_record_to_nexus(
    store: RegistryStore,
    local_mod_id: int,
    nexus_mod_id: int,
    mod_info: dict[str, Any],
    file_info: dict[str, Any],
) -> ModRecord:
    """Promote one adopted local-only record to Nexus tracking without touching game files."""
    local = store.get(local_mod_id)
    if local is None:
        raise ModManagerError("The selected local mod is no longer in Tertium's registry.")
    if local.source != "local":
        raise ModManagerError(f"{local.name} is already Nexus-linked.")
    if local.mod_id in CORE_MOD_IDS:
        raise ModManagerError("Core framework records cannot be linked through the normal existing-mod workflow.")
    if nexus_mod_id <= 0:
        raise ModManagerError("Nexus mod ID must be greater than zero.")

    existing = store.get(nexus_mod_id)
    if existing is not None and existing.mod_id != local.mod_id:
        raise ModManagerError(
            f"Nexus mod {nexus_mod_id} is already linked to {existing.name}. "
            "Tertium will not merge two installed records automatically."
        )

    try:
        file_id = int(file_info.get("file_id") or 0)
    except (TypeError, ValueError):
        file_id = 0
    if file_id < 0:
        raise ModManagerError("Nexus file ID cannot be negative.")

    records_before = store.all()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = store.backup_dir / f"registry-link-{stamp}-{abs(local.mod_id)}.json"
    save_json(backup, {"mods": [record.to_dict() for record in records_before]})

    linked = ModRecord(
        mod_id=int(nexus_mod_id),
        file_id=file_id,
        name=str(mod_info.get("name") or local.name),
        version=str(file_info.get("version") or file_info.get("mod_version") or local.version or ""),
        file_name=str(file_info.get("file_name") or ""),
        category_id=file_info.get("category_id"),
        folders=list(local.folders or []),
        enabled=bool(local.enabled),
        installed_at=float(local.installed_at or time.time()),
        source="nexus",
        archive_sha256="",
        archive_size=0,
    )
    kept = [
        record
        for record in records_before
        if record.mod_id not in {local.mod_id, int(nexus_mod_id)}
    ]
    kept.append(linked)
    store.save_all(kept)
    return linked




class CompatibilityStore:
    """Build-aware crash/quarantine memory kept separate from the mod registry."""

    def __init__(self, root: Path | None = None):
        self.root = root or app_data_dir()
        self.path = self.root / "compatibility.json"

    def all(self) -> list[dict[str, Any]]:
        raw = load_json(self.path, {"records": []})
        rows = raw.get("records", []) if isinstance(raw, dict) else []
        return [dict(row) for row in rows if isinstance(row, dict)]

    def save_all(self, rows: Iterable[dict[str, Any]]) -> None:
        save_json(self.path, {"records": list(rows)})

    @staticmethod
    def _key(logical_name: str, build_id: str, mod_version: str) -> tuple[str, str, str]:
        return (logical_name.strip().lower(), str(build_id or ""), str(mod_version or ""))

    def get(self, logical_name: str, build_id: str, mod_version: str) -> dict[str, Any] | None:
        key = self._key(logical_name, build_id, mod_version)
        for row in self.all():
            if self._key(str(row.get("logical_name") or ""), str(row.get("build_id") or ""), str(row.get("mod_version") or "")) == key:
                return row
        return None

    def record_crash(self, logical_name: str, build_id: str, mod_version: str, signature: str, error: str, log_name: str) -> dict[str, Any]:
        rows = self.all()
        key = self._key(logical_name, build_id, mod_version)
        row = None
        for item in rows:
            if self._key(str(item.get("logical_name") or ""), str(item.get("build_id") or ""), str(item.get("mod_version") or "")) == key:
                row = item
                break
        now = time.time()
        if row is None:
            row = {
                "logical_name": logical_name,
                "build_id": str(build_id or ""),
                "mod_version": str(mod_version or ""),
                "evidence_count": 0,
                "signatures": [],
                "quarantined": False,
                "created_at": now,
            }
            rows.append(row)
        signatures = list(row.get("signatures") or [])
        if signature and signature not in signatures:
            signatures.append(signature)
            row["evidence_count"] = int(row.get("evidence_count") or 0) + 1
        row["signatures"] = signatures[-12:]
        row["last_seen"] = now
        row["last_error"] = str(error or "")[:1000]
        row["last_log"] = str(log_name or "")
        self.save_all(rows)
        return dict(row)

    def set_quarantined(self, logical_name: str, build_id: str, mod_version: str, value: bool, reason: str = "") -> dict[str, Any]:
        rows = self.all()
        key = self._key(logical_name, build_id, mod_version)
        row = None
        for item in rows:
            if self._key(str(item.get("logical_name") or ""), str(item.get("build_id") or ""), str(item.get("mod_version") or "")) == key:
                row = item
                break
        if row is None:
            row = {
                "logical_name": logical_name,
                "build_id": str(build_id or ""),
                "mod_version": str(mod_version or ""),
                "evidence_count": 0,
                "signatures": [],
                "created_at": time.time(),
            }
            rows.append(row)
        row["quarantined"] = bool(value)
        row["quarantine_reason"] = str(reason or "")[:1000]
        row["quarantine_changed_at"] = time.time()
        self.save_all(rows)
        return dict(row)

    def clear_quarantine(self, logical_name: str, build_id: str | None = None, mod_version: str | None = None) -> int:
        """Clear quarantine flags for a mod after an explicit user override/removal.

        When build/version are omitted, every stored quarantine for that logical
        mod name is cleared. Crash evidence is retained for diagnostics.
        """
        wanted = logical_name.strip().lower()
        rows = self.all()
        changed = 0
        for row in rows:
            if str(row.get("logical_name") or "").strip().lower() != wanted:
                continue
            if build_id is not None and str(row.get("build_id") or "") != str(build_id or ""):
                continue
            if mod_version is not None and str(row.get("mod_version") or "") != str(mod_version or ""):
                continue
            if row.get("quarantined"):
                row["quarantined"] = False
                row["quarantine_reason"] = "Cleared by explicit user action."
                row["quarantine_changed_at"] = time.time()
                changed += 1
        if changed:
            self.save_all(rows)
        return changed


    def crash_signatures(self, logical_name: str, build_id: str | None = None, mod_version: str | None = None) -> set[str]:
        """Return recorded crash signatures for a mod, optionally scoped to build/version."""
        wanted = logical_name.strip().lower()
        signatures: set[str] = set()
        for row in self.all():
            if str(row.get("logical_name") or "").strip().lower() != wanted:
                continue
            if build_id is not None and str(row.get("build_id") or "") != str(build_id or ""):
                continue
            if mod_version is not None and str(row.get("mod_version") or "") != str(mod_version or ""):
                continue
            signatures.update(str(x) for x in (row.get("signatures") or []) if str(x))
        return signatures

    def learned_candidates_for_error(
        self,
        error: str,
        build_id: str,
        installed_versions: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Map a repeated script error back to mods previously associated with it.

        This is only used when the current stack contains no direct mod path.
        Matching is restricted to the current Darktide build and currently
        installed mod version so stale history does not poison future sessions.
        """
        normalized_error = " ".join(str(error or "").split()).casefold()
        if not normalized_error:
            return []
        matches: list[dict[str, Any]] = []
        for row in self.all():
            logical = str(row.get("logical_name") or "").strip()
            if not logical:
                continue
            if str(row.get("build_id") or "") != str(build_id or ""):
                continue
            current_version = installed_versions.get(logical.casefold())
            if current_version is None:
                continue
            if str(row.get("mod_version") or "") != str(current_version or ""):
                continue
            prior_error = " ".join(str(row.get("last_error") or "").split()).casefold()
            if prior_error != normalized_error:
                continue
            if int(row.get("evidence_count") or 0) <= 0:
                continue
            matches.append({
                "logical_name": logical,
                "score": 95,
                "confidence": "high",
                "source": "learned-crash-signature",
            })
        # Unique learned matches are strong evidence. Multiple matches are kept
        # conservative so Tertium does not auto-disable the wrong mod.
        if len(matches) > 1:
            for item in matches:
                item["score"] = 55
                item["confidence"] = "medium"
        return matches


class ProfileStore:
    def __init__(self, root: Path | None = None):
        self.root = root or app_data_dir()
        self.path = self.root / "profiles.json"

    def all(self) -> dict[str, dict[str, bool]]:
        raw = load_json(self.path, {"profiles": {}})
        profiles = raw.get("profiles", {}) if isinstance(raw, dict) else {}
        clean: dict[str, dict[str, bool]] = {}
        for name, states in profiles.items():
            if isinstance(name, str) and isinstance(states, dict):
                clean[name] = {str(k): bool(v) for k, v in states.items()}
        return clean

    def save(self, name: str, states: dict[str, bool]) -> None:
        name = name.strip()
        if not name:
            raise ModManagerError("Profile name cannot be blank.")
        profiles = self.all()
        profiles[name] = dict(sorted(states.items(), key=lambda kv: kv[0].lower()))
        save_json(self.path, {"profiles": profiles})

    def delete(self, name: str) -> None:
        profiles = self.all()
        profiles.pop(name, None)
        save_json(self.path, {"profiles": profiles})


def _candidate_steam_roots() -> list[Path]:
    roots: list[Path] = []
    if os.name == "nt":
        try:
            import winreg  # type: ignore

            for hive, key_name in [
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
            ]:
                try:
                    with winreg.OpenKey(hive, key_name) as key:
                        for value_name in ("SteamPath", "InstallPath"):
                            try:
                                value, _ = winreg.QueryValueEx(key, value_name)
                                roots.append(Path(value))
                            except OSError:
                                pass
                except OSError:
                    pass
        except ImportError:
            pass

        pf86 = os.environ.get("PROGRAMFILES(X86)")
        pf = os.environ.get("PROGRAMFILES")
        if pf86:
            roots.append(Path(pf86) / "Steam")
        if pf:
            roots.append(Path(pf) / "Steam")
    return roots


def parse_steam_library_paths(vdf_text: str) -> list[Path]:
    # Handles both old and current libraryfolders.vdf formats by extracting every "path" value.
    found = re.findall(r'"path"\s+"([^"]+)"', vdf_text, flags=re.IGNORECASE)
    paths: list[Path] = []
    for value in found:
        value = value.replace("\\\\", "\\")
        paths.append(Path(value))
    return paths


def detect_darktide_install() -> Path | None:
    checked: set[str] = set()
    for steam_root in _candidate_steam_roots():
        key = str(steam_root).lower()
        if key in checked:
            continue
        checked.add(key)
        libraries = [steam_root]
        vdf = steam_root / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            try:
                libraries.extend(parse_steam_library_paths(vdf.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                pass

        for lib in libraries:
            steamapps = lib / "steamapps"
            manifest = steamapps / f"appmanifest_{STEAM_APP_ID}.acf"
            game = steamapps / "common" / GAME_FOLDER_NAME
            if manifest.exists() and game.exists():
                return game.resolve()
            if game.exists():
                return game.resolve()
    return None


def validate_game_dir(game_dir: Path) -> tuple[bool, str]:
    if not game_dir.exists():
        return False, "Folder does not exist."
    likely = [game_dir / "bundle", game_dir / "binaries"]
    if not all(p.exists() for p in likely):
        return False, "That folder does not look like the Darktide installation root (bundle/ and binaries/ are missing)."
    return True, "Darktide folder looks valid."


def game_dir_writable(game_dir: Path) -> bool:
    """Test actual write access instead of relying on os.access semantics on Windows."""
    if not game_dir.exists():
        return False
    probe = game_dir / f".tertium-write-test-{os.getpid()}"
    try:
        with probe.open("xb") as fh:
            fh.write(b"ok")
        return True
    except OSError:
        return False
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass


def ensure_game_dir_writable(game_dir: Path) -> None:
    if not game_dir_writable(game_dir):
        raise ModManagerError(
            "Tertium cannot write to the Darktide folder. Check the folder permissions or run Tertium "
            "with sufficient Windows permissions before modifying mods."
        )




def _hidden_subprocess_kwargs() -> dict:
    """Return Windows subprocess options that prevent helper console windows.

    Tertium is packaged as a GUI application. Console helpers such as tasklist,
    dtkit-patch and 7-Zip must never create a visible/focus-stealing console
    window while the manager is open. On non-Windows platforms this is a no-op.
    """
    if os.name != "nt":
        return {}
    kwargs: dict = {}
    creation_flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if creation_flag:
        kwargs["creationflags"] = creation_flag
    startup_cls = getattr(subprocess, "STARTUPINFO", None)
    if startup_cls is not None:
        startupinfo = startup_cls()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo
    return kwargs

def _windows_process_image_names() -> set[str]:
    """Enumerate running Windows process image names without spawning a console helper.

    Guardian originally polled ``tasklist.exe`` every few seconds. Even with hidden
    startup flags, a windowed/frozen application can briefly surface or focus a
    console process on some Windows systems. Using Toolhelp32 directly avoids any
    child process, terminal flash, or focus-stealing side effect.
    """
    if os.name != "nt":
        return set()

    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002
    MAX_PATH = 260

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * MAX_PATH),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    create_snapshot.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_first.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    process_next.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    snapshot = create_snapshot(TH32CS_SNAPPROCESS, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot in (None, 0, invalid_handle):
        return set()

    names: set[str] = set()
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not process_first(snapshot, ctypes.byref(entry)):
            return names
        while True:
            if entry.szExeFile:
                names.add(str(entry.szExeFile).casefold())
            if not process_next(snapshot, ctypes.byref(entry)):
                break
        return names
    finally:
        close_handle(snapshot)


def is_darktide_running() -> bool:
    """Best-effort native check used to avoid modifying live game files on Windows."""
    if os.name != "nt":
        return False
    try:
        return "darktide.exe" in _windows_process_image_names()
    except Exception:
        # Failure to query processes should not brick the manager; file operations
        # still have their own normal OS locking semantics.
        return False


def ensure_darktide_not_running() -> None:
    if is_darktide_running():
        raise ModManagerError(
            "Darktide is currently running. Close the game before installing, updating, "
            "removing, toggling, or repairing mods."
        )


def mods_dir(game_dir: Path) -> Path:
    path = game_dir / "mods"
    path.mkdir(parents=True, exist_ok=True)
    return path


def has_dml(game_dir: Path) -> bool:
    return (game_dir / "tools" / "dtkit-patch.exe").exists() or (game_dir / "tools" / "dtkit-patch").exists()


def has_dmf(game_dir: Path) -> bool:
    return (mods_dir(game_dir) / "dmf").exists()


def has_aml(game_dir: Path) -> bool:
    log = mods_dir(game_dir) / "auto_mod_loader_log.txt"
    # The patch itself cannot be reliably fingerprinted without shipping a vendor hash.
    # Treat an installed tracking record or AML's runtime log as positive evidence.
    return log.exists()


def detect_mod_roots(extracted_root: Path) -> list[Path]:
    candidates: list[Path] = []
    for path in extracted_root.rglob("*.mod"):
        parent = path.parent
        # A normal Darktide mod has <root>/<root>.mod, while Lua files live deeper.
        if parent == extracted_root:
            candidates.append(parent)
        elif parent.parent == extracted_root or (parent / path.name).exists():
            candidates.append(parent)
    # More reliable: any directory with a .mod file directly inside it.
    direct = []
    for d in [extracted_root, *[p for p in extracted_root.rglob("*") if p.is_dir()]]:
        try:
            if any(f.is_file() and f.suffix.lower() == ".mod" for f in d.iterdir()):
                direct.append(d)
        except OSError:
            continue
    candidates.extend(direct)

    unique: list[Path] = []
    seen: set[Path] = set()
    for c in candidates:
        rc = c.resolve()
        if rc not in seen:
            seen.add(rc)
            unique.append(c)
    return unique


def _unsafe_archive_name(name: str) -> bool:
    normalized = name.replace("\\", "/")
    if not normalized or "\x00" in normalized:
        return True
    if normalized.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:/", normalized):
        return True
    parts = [part for part in normalized.split("/") if part]
    if ".." in parts:
        return True
    # Darktide is a Windows target. Block NTFS alternate-data-stream syntax and
    # reserved DOS device names even when testing/extracting on another OS.
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    for part in parts:
        if ":" in part:
            return True
        if part != part.rstrip(" ."):
            return True
        stem = part.rstrip(" .").split(".", 1)[0].lower()
        if stem in reserved:
            return True
    return False


def _safe_zip_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    members = zf.infolist()
    if len(members) > MAX_ARCHIVE_FILES:
        raise ModManagerError(f"Archive contains too many files ({len(members):,}); refusing to extract it.")
    total_size = sum(max(0, int(m.file_size)) for m in members)
    if total_size > MAX_ARCHIVE_UNPACKED_BYTES:
        raise ModManagerError(
            f"Archive expands to {total_size / 1024**3:.1f} GiB, above the {MAX_ARCHIVE_UNPACKED_BYTES / 1024**3:.0f} GiB safety limit."
        )
    dest_resolved = dest.resolve()
    normalized_seen: set[str] = set()
    for member in members:
        normalized_name = member.filename.replace("\\", "/").rstrip("/")
        if normalized_name:
            collision_key = "/".join(part.rstrip(" .").casefold() for part in normalized_name.split("/"))
            if collision_key in normalized_seen:
                raise ModManagerError(f"Archive contains Windows-colliding duplicate paths: {member.filename}")
            normalized_seen.add(collision_key)
        if _unsafe_archive_name(member.filename):
            raise ModManagerError(f"Unsafe archive path blocked: {member.filename}")
        unix_mode = (member.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(unix_mode):
            raise ModManagerError(f"Symbolic links are not allowed in mod archives: {member.filename}")
        target = (dest / member.filename).resolve()
        if target != dest_resolved and dest_resolved not in target.parents:
            raise ModManagerError(f"Unsafe archive path blocked: {member.filename}")
    zf.extractall(dest)


def find_7zip() -> Path | None:
    names = ["7z.exe", "7za.exe", "7z"]
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    if os.name == "nt":
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.environ.get(env)
            if base:
                p = Path(base) / "7-Zip" / "7z.exe"
                if p.exists():
                    return p
    return None


def _validate_7zip_archive(seven: Path, archive: Path) -> None:
    result = subprocess.run(
        [str(seven), "l", "-slt", str(archive)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        timeout=30,
        **_hidden_subprocess_kwargs(),
    )
    if result.returncode != 0:
        raise ModManagerError(f"7-Zip could not inspect the archive:\n{result.stdout[-2000:]}")
    file_count = 0
    unpacked = 0
    archive_names = {str(archive), archive.name}
    normalized_seen: set[str] = set()
    for line in result.stdout.splitlines():
        if line.startswith("Path = "):
            name = line[7:].strip()
            if name and name not in archive_names:
                file_count += 1
                normalized_name = name.replace("\\", "/").rstrip("/")
                collision_key = "/".join(part.rstrip(" .").casefold() for part in normalized_name.split("/"))
                if collision_key in normalized_seen:
                    raise ModManagerError(f"Archive contains Windows-colliding duplicate paths: {name}")
                normalized_seen.add(collision_key)
                if _unsafe_archive_name(name):
                    raise ModManagerError(f"Unsafe archive path blocked: {name}")
        elif line.startswith(("Symbolic Link = ", "Hard Link = ")):
            value = line.split("=", 1)[1].strip() if "=" in line else ""
            if value:
                raise ModManagerError("Symbolic/hard links are not allowed in mod archives.")
        elif line.startswith("Size = "):
            value = line[7:].strip()
            if value.isdigit():
                unpacked += int(value)
    if file_count > MAX_ARCHIVE_FILES:
        raise ModManagerError(f"Archive contains too many entries ({file_count:,}); refusing to extract it.")
    if unpacked > MAX_ARCHIVE_UNPACKED_BYTES:
        raise ModManagerError(
            f"Archive expands above the {MAX_ARCHIVE_UNPACKED_BYTES / 1024**3:.0f} GiB safety limit."
        )


def extract_archive(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            _safe_zip_extract(zf, dest)
        return
    seven = find_7zip()
    if not seven:
        raise ModManagerError("This archive is not ZIP. Install 7-Zip so .7z/.rar archives can be extracted.")
    _validate_7zip_archive(seven, archive)
    result = subprocess.run(
        [str(seven), "x", str(archive), f"-o{dest}", "-y"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        **_hidden_subprocess_kwargs(),
    )
    if result.returncode != 0:
        raise ModManagerError(f"7-Zip extraction failed:\n{result.stdout[-2000:]}")


def _strip_single_wrapper(root: Path) -> Path:
    current = root
    for _ in range(3):
        entries = [p for p in current.iterdir() if p.name not in {"__MACOSX"}]
        if len(entries) == 1 and entries[0].is_dir():
            current = entries[0]
        else:
            break
    return current


def _backup_paths(paths: list[Path], backup_root: Path) -> None:
    for src in paths:
        if not src.exists():
            continue
        rel_name = src.name
        target = backup_root / rel_name
        if src.is_dir():
            shutil.copytree(src, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)


def _copy_overlay(src_root: Path, dst_root: Path, preserve: set[Path] | None = None) -> list[str]:
    preserve = preserve or set()
    installed: list[str] = []
    for src in src_root.iterdir():
        dst = dst_root / src.name
        if any(dst.resolve() == p.resolve() for p in preserve if p.exists()):
            continue
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        installed.append(src.name)
    return installed


def _overlay_files(src_root: Path) -> list[Path]:
    return [p for p in src_root.rglob("*") if p.is_file()]


def _install_file_overlay(
    src_root: Path,
    dst_root: Path,
    backup_root: Path,
    preserve_relative: set[Path] | None = None,
) -> list[Path]:
    """Apply a granular file overlay and return touched destination files.

    Existing destination files are backed up with their relative paths. Files that
    did not exist before are removed by _rollback_file_overlay if a later copy fails.
    """
    preserve_relative = preserve_relative or set()
    touched: list[Path] = []
    try:
        for src in _overlay_files(src_root):
            rel = src.relative_to(src_root)
            if rel in preserve_relative:
                continue
            dst = dst_root / rel
            if dst.exists():
                backup = backup_root / rel
                backup.parent.mkdir(parents=True, exist_ok=True)
                if dst.is_dir():
                    raise ModManagerError(f"Cannot overwrite directory with file: {dst}")
                shutil.copy2(dst, backup)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            touched.append(dst)
        return touched
    except Exception:
        _rollback_file_overlay(touched, dst_root, backup_root)
        raise


def _rollback_file_overlay(touched: list[Path], dst_root: Path, backup_root: Path) -> None:
    for dst in reversed(touched):
        try:
            rel = dst.relative_to(dst_root)
        except ValueError:
            continue
        backup = backup_root / rel
        try:
            if backup.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, dst)
            elif dst.exists() and dst.is_file():
                dst.unlink()
        except OSError:
            pass


def _resolve_existing_mod_path(target: Path, tracked_folder: str) -> Path:
    logical = tracked_folder.lstrip("_")
    candidates = [target / tracked_folder, target / logical, target / ("_" + logical)]
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return candidate
    return target / tracked_folder


def _record_logical_folders(record: ModRecord) -> set[str]:
    return {
        str(folder).lstrip("_").casefold()
        for folder in (record.folders or [])
        if str(folder).lstrip("_") and str(folder).lstrip("_").casefold() not in {"base", "dmf"}
    }


def _find_adopted_local_record(store: RegistryStore, logical_names: set[str]) -> ModRecord | None:
    """Find a local-only placeholder that clearly owns one of the incoming mod folders.

    Tertium never guesses a Nexus ID from a folder name. Instead, once Nexus itself
    supplies a real mod/file ID through an NXM install, folder overlap gives us a
    deterministic way to promote the existing local placeholder to Nexus tracking.
    """
    wanted = {str(name).casefold() for name in logical_names if str(name).strip()}
    if not wanted:
        return None
    matches: list[ModRecord] = []
    for candidate in store.all():
        if candidate.source != "local" or candidate.mod_id in CORE_MOD_IDS:
            continue
        if _record_logical_folders(candidate) & wanted:
            matches.append(candidate)
    # Ambiguous ownership is intentionally left alone rather than guessed.
    return matches[0] if len(matches) == 1 else None


def install_archive(
    game_dir: Path,
    archive: Path,
    record: ModRecord,
    store: RegistryStore,
    log: Callable[[str], None] | None = None,
) -> ModRecord:
    log = log or (lambda _msg: None)
    ensure_darktide_not_running()
    ensure_game_dir_writable(game_dir)
    ok, reason = validate_game_dir(game_dir)
    if not ok:
        raise ModManagerError(reason)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_root = store.backup_dir / f"{record.mod_id}-{stamp}"
    old = store.get(record.mod_id)
    promoted_local_id: int | None = None

    with tempfile.TemporaryDirectory(prefix="tertium-mod-") as td:
        extracted = Path(td)
        extract_archive(archive, extracted)
        src = _strip_single_wrapper(extracted)

        if record.mod_id == DML_MOD_ID:
            log("Installing Darktide Mod Loader into the game root…")
            load_order_rel = Path("mods") / "mod_load_order.txt"
            preserve_relative = {load_order_rel} if (game_dir / load_order_rel).exists() else set()
            _install_file_overlay(src, game_dir, backup_root, preserve_relative=preserve_relative)
            record.folders = []
        elif record.mod_id == AML_MOD_ID:
            log("Applying Auto Mod Loading and Ordering patch…")
            target = mods_dir(game_dir)
            # Locate the archive's base folder even if wrapped.
            base_candidates = [p for p in extracted.rglob("base") if p.is_dir() and (p / "mod_manager.lua").exists()]
            if not base_candidates:
                raise ModManagerError("AML archive did not contain base/mod_manager.lua as expected.")
            base = base_candidates[0]
            current = target / "base"
            if current.exists():
                _backup_paths([current], backup_root)
            try:
                shutil.copytree(base, current, dirs_exist_ok=True)
            except Exception:
                if current.exists():
                    shutil.rmtree(current, ignore_errors=True)
                saved = backup_root / "base"
                if saved.exists():
                    shutil.copytree(saved, current, dirs_exist_ok=True)
                raise
            record.folders = ["base"]
        else:
            roots = detect_mod_roots(extracted)
            if not roots:
                raise ModManagerError(
                    "No Darktide mod folder was detected. Expected a folder containing a .mod file. "
                    "For unusual archives, extract them manually into the Darktide mods folder and use Refresh."
                )
            target = mods_dir(game_dir)
            installed_folders: list[str] = []

            # A manually installed mod may already have been auto-adopted as local-only.
            # Once a genuine Nexus NXM install arrives, promote that placeholder instead
            # of creating a duplicate registry row or re-enabling a disabled folder.
            if old is None and record.source == "nexus":
                incoming_logicals = {root.name.lstrip("_").casefold() for root in roots}
                adopted_old = _find_adopted_local_record(store, incoming_logicals)
                if adopted_old is not None:
                    old = adopted_old
                    promoted_local_id = adopted_old.mod_id
                    log(f"Linked existing local mod '{adopted_old.name}' to Nexus mod {record.mod_id}.")

            # Back up every folder that may be replaced, then roll back the whole mod install on failure.
            # Resolve both enabled and underscore-disabled names so manually toggled mods are not duplicated
            # or unexpectedly re-enabled when an update arrives.
            old_paths: list[Path] = []
            prior_enabled_by_logical: dict[str, bool] = {}
            if old:
                for tracked in old.folders or []:
                    if tracked.lstrip("_").lower() in {"base", "dmf"}:
                        continue
                    actual = _resolve_existing_mod_path(target, tracked)
                    old_paths.append(actual)
                    if actual.exists():
                        prior_enabled_by_logical[actual.name.lstrip("_")] = not actual.name.startswith("_")
                    else:
                        prior_enabled_by_logical[tracked.lstrip("_")] = not tracked.startswith("_")
            planned: list[tuple[Path, Path]] = []
            replacement_paths: list[Path] = []
            for root in roots:
                name = root.name.lstrip("_")
                enabled_before = prior_enabled_by_logical.get(name, True)
                dst = target / (name if enabled_before else "_" + name)
                planned.append((root, dst))
                replacement_paths.append(dst)
            backup_candidates: list[Path] = []
            seen_backup: set[str] = set()
            for path in [*old_paths, *replacement_paths]:
                key = str(path.resolve()) if path.exists() else str(path.absolute())
                if path.exists() and key not in seen_backup:
                    seen_backup.add(key)
                    backup_candidates.append(path)
            _backup_paths(backup_candidates, backup_root)
            touched_names = {p.name for p in [*old_paths, *replacement_paths]}
            try:
                for path in old_paths:
                    if path.exists():
                        shutil.rmtree(path) if path.is_dir() else path.unlink()
                for root, dst in planned:
                    if dst.exists():
                        shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
                    shutil.copytree(root, dst)
                    installed_folders.append(dst.name)
            except Exception:
                for name in touched_names:
                    dst = target / name
                    if dst.exists():
                        shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
                    saved = backup_root / name
                    if saved.exists():
                        if saved.is_dir():
                            shutil.copytree(saved, dst, dirs_exist_ok=True)
                        else:
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(saved, dst)
                raise
            record.folders = installed_folders

    try:
        record.archive_sha256 = _sha256_file(archive)
        record.archive_size = int(archive.stat().st_size)
    except OSError:
        record.archive_sha256 = ""
        record.archive_size = 0
    record.installed_at = time.time()
    record.enabled = all(not f.startswith("_") for f in (record.folders or [])) if record.folders else True
    if promoted_local_id is not None and promoted_local_id != record.mod_id:
        store.remove_mod(promoted_local_id)
    store.upsert(record)
    if old and record.mod_id not in CORE_MOD_IDS and backup_root.exists():
        backed = [p.name for p in backup_root.iterdir() if p.name != "install.json"]
        if backed:
            save_json(backup_root / "install.json", {
                "created_at": time.time(),
                "mod_id": record.mod_id,
                "backed_up_entries": sorted(backed),
                "old_record": old.to_dict(),
                "new_record": record.to_dict(),
            })
    return record


def scan_installed_mods(game_dir: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    mdir = mods_dir(game_dir)
    for child in sorted(mdir.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name in {"base"}:
            continue
        folder = child.name
        enabled = not folder.startswith("_")
        logical = folder[1:] if folder.startswith("_") else folder
        mod_file = child / f"{logical}.mod"
        any_mod = next(child.glob("*.mod"), None)
        if mod_file.exists() or any_mod:
            version = ""
            info = child / "info.json"
            if info.exists():
                try:
                    version = str(json.loads(info.read_text(encoding="utf-8", errors="ignore")).get("version", ""))
                except Exception:
                    pass
            if not version and any_mod:
                try:
                    if any_mod.stat().st_size <= 2 * 1024 * 1024:
                        mod_text = any_mod.read_text(encoding="utf-8", errors="replace")
                        match = re.search(r"\bversion\s*=\s*[\"']([^\"']+)[\"']", mod_text, flags=re.IGNORECASE)
                        if match:
                            version = match.group(1).strip()
                except OSError:
                    pass
            result.append({"folder": folder, "logical_name": logical, "enabled": enabled, "version": version})
    return result


def sync_registry_folders(game_dir: Path, store: RegistryStore) -> None:
    """Synchronize tracked folder names with enabled/disabled underscore state on disk."""
    scanned = {item["logical_name"]: item for item in scan_installed_mods(game_dir)}
    records = store.all()
    changed = False
    for rec in records:
        if rec.mod_id == DML_MOD_ID:
            continue
        new_folders: list[str] = []
        for tracked in rec.folders or []:
            logical = tracked.lstrip("_")
            if logical == "base":
                new_folders.append(tracked)
                continue
            item = scanned.get(logical)
            new_folders.append(item["folder"] if item else tracked)
        if new_folders != list(rec.folders or []):
            rec.folders = new_folders
            changed = True
        if rec.folders and rec.mod_id not in {AML_MOD_ID, DML_MOD_ID}:
            enabled = all(not f.startswith("_") for f in rec.folders)
            if rec.enabled != enabled:
                rec.enabled = enabled
                changed = True
    if changed:
        store.save_all(records)


def capture_mod_profile(game_dir: Path) -> dict[str, bool]:
    return {
        item["logical_name"]: bool(item["enabled"])
        for item in scan_installed_mods(game_dir)
        if item["logical_name"].lower() != "dmf"
    }


def apply_mod_profile(game_dir: Path, states: dict[str, bool], maintain_load_order: bool = False) -> dict[str, Any]:
    scanned = {item["logical_name"]: item for item in scan_installed_mods(game_dir)}
    changed: list[str] = []
    missing: list[str] = []
    for logical, desired in states.items():
        if logical.lower() == "dmf":
            continue
        item = scanned.get(logical)
        if not item:
            missing.append(logical)
            continue
        if bool(item["enabled"]) != bool(desired):
            toggle_mod_folder(game_dir, item["folder"], bool(desired))
            changed.append(logical)
    if maintain_load_order:
        enabled = [
            m["logical_name"] for m in scan_installed_mods(game_dir)
            if m["enabled"] and m["logical_name"].lower() != "dmf"
        ]
        write_mod_load_order(game_dir, enabled)
    return {"changed": changed, "missing": missing}




def audit_mod_structure(game_dir: Path) -> list[dict[str, str]]:
    """Return non-destructive structural warnings for the Darktide mods folder."""
    mdir = mods_dir(game_dir)
    if not mdir.exists():
        return [{"severity": "error", "folder": "mods", "message": "Darktide mods folder is missing."}]

    issues: list[dict[str, str]] = []
    logical_seen: dict[str, str] = {}
    for child in sorted(mdir.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name == "base":
            continue
        logical = child.name.lstrip("_")
        key = logical.casefold()
        previous = logical_seen.get(key)
        if previous and previous != child.name:
            issues.append({
                "severity": "error",
                "folder": child.name,
                "message": f"Duplicate logical mod folder also exists as '{previous}'.",
            })
        else:
            logical_seen[key] = child.name

        try:
            mod_files = sorted([f.name for f in child.iterdir() if f.is_file() and f.suffix.lower() == ".mod"])
        except OSError as exc:
            issues.append({"severity": "error", "folder": child.name, "message": f"Could not inspect folder: {exc}"})
            continue
        if not mod_files:
            issues.append({
                "severity": "warning",
                "folder": child.name,
                "message": "Folder has no top-level .mod descriptor and will not be recognized as a normal Darktide mod.",
            })
            continue
        if len(mod_files) > 1:
            issues.append({
                "severity": "warning",
                "folder": child.name,
                "message": "Folder contains multiple top-level .mod descriptors: " + ", ".join(mod_files),
            })
        expected = f"{logical}.mod"
        if expected.casefold() not in {name.casefold() for name in mod_files}:
            issues.append({
                "severity": "warning",
                "folder": child.name,
                "message": f"Expected descriptor '{expected}' is missing (found: {', '.join(mod_files)}).",
            })
    return issues


def cleanup_stale_download_parts(store: RegistryStore, older_than_seconds: int = 6 * 3600) -> dict[str, int]:
    """Remove abandoned cache .part files left by an interrupted previous process."""
    removed = 0
    freed = 0
    cutoff = time.time() - max(0, int(older_than_seconds))
    for path in store.cache_dir.glob("*.part"):
        try:
            st = path.stat()
            if st.st_mtime > cutoff:
                continue
            size = int(st.st_size)
            path.unlink()
            removed += 1
            freed += size
        except OSError:
            continue
    return {"removed": removed, "freed": freed}


def create_mod_state_snapshot(
    game_dir: Path,
    store: RegistryStore,
    reason: str,
    *,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Capture a lightweight reversible snapshot of enabled states and tracking metadata."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_reason = re.sub(r"[^A-Za-z0-9_.-]+", "-", reason.strip()).strip("-") or "state"
    root = store.backup_dir / f"state-{stamp}-{safe_reason}"
    root.mkdir(parents=True, exist_ok=False)
    load_order = mods_dir(game_dir) / "mod_load_order.txt"
    payload: dict[str, Any] = {
        "schema": 1,
        "created_at": time.time(),
        "reason": reason,
        "steam_build_id": read_steam_build_id(game_dir),
        "states": capture_mod_profile(game_dir),
        "tracked_mods": [r.to_dict() for r in store.all()],
        "load_order": load_order.read_text(encoding="utf-8", errors="replace") if load_order.exists() else "",
        "restored_at": None,
    }
    if extra:
        payload["extra"] = extra
    save_json(root / "snapshot.json", payload)
    return root


def latest_state_snapshot(store: RegistryStore, reason_prefix: str | None = None, unrestored_only: bool = False) -> Path | None:
    candidates: list[tuple[float, Path]] = []
    for root in store.backup_dir.glob("state-*"):
        payload = load_json(root / "snapshot.json", {})
        if not isinstance(payload, dict) or not isinstance(payload.get("states"), dict):
            continue
        reason = str(payload.get("reason") or "")
        if reason_prefix and not reason.lower().startswith(reason_prefix.lower()):
            continue
        if unrestored_only and payload.get("restored_at"):
            continue
        try:
            created = float(payload.get("created_at") or root.stat().st_mtime)
        except OSError:
            created = 0.0
        candidates.append((created, root))
    return max(candidates, default=(0.0, None), key=lambda x: x[0])[1]


def restore_mod_state_snapshot(
    game_dir: Path,
    store: RegistryStore,
    snapshot_root: Path,
    *,
    maintain_load_order: bool = False,
) -> dict[str, Any]:
    ensure_darktide_not_running()
    ensure_game_dir_writable(game_dir)
    payload = load_json(snapshot_root / "snapshot.json", {})
    states = payload.get("states") if isinstance(payload, dict) else None
    if not isinstance(states, dict):
        raise ModManagerError("The selected state snapshot is invalid or incomplete.")
    clean_states = {str(k): bool(v) for k, v in states.items()}
    result = apply_mod_profile(game_dir, clean_states, maintain_load_order=maintain_load_order)
    sync_registry_folders(game_dir, store)
    payload["restored_at"] = time.time()
    save_json(snapshot_root / "snapshot.json", payload)
    return {**result, "snapshot": str(snapshot_root), "reason": str(payload.get("reason") or "")}


def enter_troubleshooting_safe_mode(game_dir: Path, store: RegistryStore, maintain_load_order: bool = False) -> dict[str, Any]:
    """Disable all normal mods while preserving a one-click restore snapshot."""
    ensure_darktide_not_running()
    ensure_game_dir_writable(game_dir)
    snapshot = create_mod_state_snapshot(game_dir, store, "troubleshooting-safe-mode")
    states = capture_mod_profile(game_dir)
    disabled_states = {name: False for name in states}
    try:
        result = apply_mod_profile(game_dir, disabled_states, maintain_load_order=maintain_load_order)
        sync_registry_folders(game_dir, store)
        return {**result, "snapshot": str(snapshot)}
    except Exception:
        try:
            restore_mod_state_snapshot(game_dir, store, snapshot, maintain_load_order=maintain_load_order)
        except Exception:
            pass
        raise


def restore_latest_safe_mode(game_dir: Path, store: RegistryStore, maintain_load_order: bool = False) -> dict[str, Any]:
    snapshot = latest_state_snapshot(store, "troubleshooting-safe-mode", unrestored_only=True)
    if not snapshot:
        raise ModManagerError("No unrestored troubleshooting snapshot is available.")
    return restore_mod_state_snapshot(game_dir, store, snapshot, maintain_load_order=maintain_load_order)


def restore_latest_mod_state(game_dir: Path, store: RegistryStore, maintain_load_order: bool = False) -> dict[str, Any]:
    snapshot = latest_state_snapshot(store, unrestored_only=True)
    if not snapshot:
        raise ModManagerError("No unrestored Tertium mod-state snapshot is available.")
    return restore_mod_state_snapshot(game_dir, store, snapshot, maintain_load_order=maintain_load_order)


def export_setup_manifest(
    game_dir: Path,
    store: RegistryStore,
    profiles: ProfileStore,
    destination: Path,
    app_version: str,
) -> Path:
    """Export a shareable, secret-free record of the user's Darktide mod setup."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "kind": "tertium-darktide-setup",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "tertium_version": app_version,
        "game_domain": GAME_DOMAIN,
        "steam_build_id": read_steam_build_id(game_dir),
        "installed": scan_installed_mods(game_dir),
        "tracked": [r.to_dict() for r in store.all()],
        "profiles": profiles.all(),
    }
    save_json(destination, payload)
    return destination


def load_setup_manifest(path: Path) -> dict[str, Any]:
    payload = load_json(path, {})
    if not isinstance(payload, dict) or payload.get("kind") != "tertium-darktide-setup":
        raise ModManagerError("This is not a Tertium Darktide setup manifest.")
    if int(payload.get("schema") or 0) != 1:
        raise ModManagerError(f"Unsupported setup manifest schema: {payload.get('schema')!r}")
    if str(payload.get("game_domain") or GAME_DOMAIN) != GAME_DOMAIN:
        raise ModManagerError("This setup manifest is for a different Nexus game domain.")
    return payload


def compare_setup_manifest(game_dir: Path, store: RegistryStore, manifest: dict[str, Any]) -> dict[str, Any]:
    """Compare a manifest to the current install without modifying anything."""
    current_scanned = {m["logical_name"]: m for m in scan_installed_mods(game_dir)}
    manifest_installed = {str(m.get("logical_name")): m for m in manifest.get("installed", []) if isinstance(m, dict) and m.get("logical_name")}
    current_tracked = {r.mod_id: r for r in store.all()}
    manifest_tracked: dict[int, dict[str, Any]] = {}
    for item in manifest.get("tracked", []):
        if not isinstance(item, dict):
            continue
        try:
            manifest_tracked[int(item.get("mod_id"))] = item
        except (TypeError, ValueError):
            continue

    missing_folders = sorted(name for name in manifest_installed if name not in current_scanned)
    extra_folders = sorted(name for name in current_scanned if name not in manifest_installed)
    state_differences = []
    for name in sorted(set(current_scanned) & set(manifest_installed)):
        desired = bool(manifest_installed[name].get("enabled", True))
        current = bool(current_scanned[name].get("enabled", True))
        if desired != current:
            state_differences.append({"name": name, "current": current, "manifest": desired})

    nexus_differences = []
    missing_nexus = []
    for mod_id, desired in manifest_tracked.items():
        if str(desired.get("source") or "nexus") != "nexus":
            continue
        current = current_tracked.get(mod_id)
        if not current:
            missing_nexus.append({"mod_id": mod_id, "name": str(desired.get("name") or f"Nexus Mod {mod_id}"), "file_id": desired.get("file_id")})
            continue
        desired_file = int(desired.get("file_id") or 0)
        if desired_file and int(current.file_id) != desired_file:
            nexus_differences.append({
                "mod_id": mod_id,
                "name": current.name,
                "current_file_id": current.file_id,
                "manifest_file_id": desired_file,
                "current_version": current.version,
                "manifest_version": str(desired.get("version") or ""),
            })
    return {
        "missing_folders": missing_folders,
        "extra_folders": extra_folders,
        "state_differences": state_differences,
        "missing_nexus": missing_nexus,
        "nexus_differences": nexus_differences,
    }


def toggle_mod_folder(game_dir: Path, folder_name: str, enabled: bool) -> str:
    ensure_darktide_not_running()
    ensure_game_dir_writable(game_dir)
    mdir = mods_dir(game_dir)
    src = mdir / folder_name
    if not src.exists():
        # Allow callers to pass the logical name.
        alt = mdir / (folder_name.lstrip("_") if folder_name.startswith("_") else "_" + folder_name)
        if alt.exists():
            src = alt
        else:
            raise ModManagerError(f"Mod folder not found: {folder_name}")

    logical = src.name.lstrip("_")
    dst_name = logical if enabled else "_" + logical
    dst = mdir / dst_name
    if src == dst:
        return dst_name
    if dst.exists():
        raise ModManagerError(f"Cannot rename; destination already exists: {dst.name}")
    src.rename(dst)
    return dst_name


def quarantine_mod_folder(
    game_dir: Path,
    folder_name: str,
    store: RegistryStore,
    record: ModRecord | None = None,
) -> Path:
    """Move a user mod into Tertium backups instead of deleting it permanently."""
    ensure_darktide_not_running()
    ensure_game_dir_writable(game_dir)
    mdir = mods_dir(game_dir)
    src = mdir / folder_name
    if not src.exists():
        raise ModManagerError(f"Mod folder not found: {folder_name}")
    logical = src.name.lstrip("_")
    if logical.lower() in {"dmf", "base"}:
        raise ModManagerError(f"Core mod folder is protected: {logical}")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = store.backup_dir / f"removed-{stamp}-{logical}"
    target = backup / "mods" / src.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(target))
    save_json(backup / "removed.json", {
        "created_at": time.time(),
        "folder": src.name,
        "logical_name": logical,
        "record": record.to_dict() if record else None,
    })
    return backup


def latest_removed_mod(store: RegistryStore) -> Path | None:
    candidates: list[tuple[float, Path]] = []
    for path in store.backup_dir.glob("removed-*"):
        meta = load_json(path / "removed.json", {})
        if isinstance(meta, dict) and meta.get("folder"):
            try:
                created = float(meta.get("created_at") or path.stat().st_mtime)
            except OSError:
                created = 0.0
            candidates.append((created, path))
    return max(candidates, default=(0.0, None), key=lambda x: x[0])[1]


def restore_latest_removed_mod(game_dir: Path, store: RegistryStore) -> dict[str, Any]:
    ensure_darktide_not_running()
    ensure_game_dir_writable(game_dir)
    backup = latest_removed_mod(store)
    if not backup:
        raise ModManagerError("No removed-mod backup is available.")
    meta = load_json(backup / "removed.json", {})
    folder = str(meta.get("folder") or "")
    if not folder:
        raise ModManagerError("Removed-mod backup metadata is invalid.")
    src = backup / "mods" / folder
    dst = mods_dir(game_dir) / folder
    if not src.exists():
        raise ModManagerError("The removed-mod backup no longer contains the mod folder.")
    if dst.exists():
        raise ModManagerError(f"Cannot restore {folder}; that folder already exists in Darktide/mods.")
    shutil.move(str(src), str(dst))
    rec_data = meta.get("record")
    if isinstance(rec_data, dict):
        try:
            store.upsert(ModRecord.from_dict(rec_data))
        except Exception:
            pass
    try:
        (backup / "removed.json").unlink(missing_ok=True)
        shutil.rmtree(backup, ignore_errors=True)
    except OSError:
        pass
    return {"folder": folder, "logical_name": str(meta.get("logical_name") or folder.lstrip("_"))}


def latest_mod_install_backup(store: RegistryStore, mod_id: int) -> Path | None:
    candidates: list[tuple[float, Path]] = []
    for path in store.backup_dir.glob(f"{int(mod_id)}-*"):
        meta = load_json(path / "install.json", {})
        if not isinstance(meta, dict) or int(meta.get("mod_id") or -1) != int(mod_id):
            continue
        if meta.get("rolled_back_at"):
            continue
        try:
            created = float(meta.get("created_at") or path.stat().st_mtime)
        except OSError:
            created = 0.0
        candidates.append((created, path))
    return max(candidates, default=(0.0, None), key=lambda x: x[0])[1]


def rollback_mod_update(game_dir: Path, store: RegistryStore, mod_id: int) -> dict[str, Any]:
    """Restore the most recent pre-update backup for a normal (non-core) tracked mod."""
    ensure_darktide_not_running()
    ensure_game_dir_writable(game_dir)
    if int(mod_id) in CORE_MOD_IDS:
        raise ModManagerError("Core DML/DMF/AML rollback uses dedicated recovery paths and is not available here.")
    current = store.get(int(mod_id))
    if not current:
        raise ModManagerError("This mod is not currently tracked by Tertium.")
    backup = latest_mod_install_backup(store, int(mod_id))
    if not backup:
        raise ModManagerError("No previous Tertium-managed version backup was found for this mod.")
    meta = load_json(backup / "install.json", {})
    old_data = meta.get("old_record") if isinstance(meta, dict) else None
    if not isinstance(old_data, dict):
        raise ModManagerError("The mod backup is missing its previous-version metadata.")
    old_record = ModRecord.from_dict(old_data)
    entries = [str(x) for x in meta.get("backed_up_entries", []) if isinstance(x, str)]
    if not entries:
        raise ModManagerError("The mod backup contains no restorable folders.")
    if any(Path(name).name != name or name in {".", "..", "base", "dmf"} for name in entries):
        raise ModManagerError("The mod backup contains an unsafe or protected folder name.")

    target = mods_dir(game_dir)
    current_paths = [_resolve_existing_mod_path(target, f) for f in (current.folders or [])]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safety = store.backup_dir / f"pre-mod-rollback-{mod_id}-{stamp}"
    existing_current = [p for p in current_paths if p.exists()]
    _backup_paths(existing_current, safety)
    save_json(safety / "rollback-safety.json", {
        "created_at": time.time(),
        "record": current.to_dict(),
        "source_backup": backup.name,
    })

    restored_names: list[str] = []
    try:
        for path in current_paths:
            if path.exists():
                shutil.rmtree(path) if path.is_dir() else path.unlink()
        for name in entries:
            src = backup / name
            dst = target / name
            if not src.exists():
                raise ModManagerError(f"Backup is missing expected folder: {name}")
            if dst.exists():
                shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            restored_names.append(name)
        old_record.folders = restored_names
        old_record.enabled = all(not name.startswith("_") for name in restored_names)
        if old_record.mod_id != current.mod_id:
            store.remove_mod(current.mod_id)
        store.upsert(old_record)
        meta["rolled_back_at"] = time.time()
        save_json(backup / "install.json", meta)
        return {
            "name": old_record.name,
            "version": old_record.version,
            "folders": restored_names,
            "backup": str(backup),
        }
    except Exception:
        for name in restored_names:
            dst = target / name
            if dst.exists():
                shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
        for saved in safety.iterdir() if safety.exists() else []:
            if saved.name == "rollback-safety.json":
                continue
            dst = target / saved.name
            if dst.exists():
                shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
            if saved.is_dir():
                shutil.copytree(saved, dst)
            else:
                shutil.copy2(saved, dst)
        store.upsert(current)
        raise


def _read_mod_rule_list(text: str, key: str) -> list[str]:
    """Best-effort parser for AML's simple string-list rule tables in .mod files."""
    match = re.search(rf"\b{re.escape(key)}\s*=\s*\{{(.*?)\}}", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    body = match.group(1)
    # Strip Lua line comments so commented-out rules are not treated as active.
    body = re.sub(r"--[^\n]*", "", body)
    values = re.findall(r"[\"']([^\"']+)[\"']", body)
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))


def mod_declared_rules(mod_folder: Path) -> dict[str, list[str]]:
    logical = mod_folder.name.lstrip("_")
    expected = mod_folder / f"{logical}.mod"
    mod_file = expected if expected.exists() else next(mod_folder.glob("*.mod"), None)
    if not mod_file or not mod_file.exists():
        return {"require": [], "load_after": [], "load_before": []}
    try:
        if mod_file.stat().st_size > 2 * 1024 * 1024:
            return {"require": [], "load_after": [], "load_before": []}
        text = mod_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"require": [], "load_after": [], "load_before": []}
    return {
        "require": _read_mod_rule_list(text, "require"),
        "load_after": _read_mod_rule_list(text, "load_after"),
        "load_before": _read_mod_rule_list(text, "load_before"),
    }


def current_mod_load_order(game_dir: Path) -> list[str]:
    path = mods_dir(game_dir) / "mod_load_order.txt"
    if not path.exists():
        return []
    result: list[str] = []
    seen: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        key = stripped.casefold()
        if key not in seen:
            seen.add(key)
            result.append(stripped)
    return result


def audit_mod_dependencies(game_dir: Path, aml_active: bool = False) -> list[dict[str, str]]:
    """Check declared AML rules without executing mod Lua or modifying the load order."""
    scanned = scan_installed_mods(game_dir)
    enabled = {m["logical_name"].casefold(): m for m in scanned if m["enabled"]}
    available = set(enabled)
    if has_dmf(game_dir):
        available.add("dmf")
    available.add("base")

    order = current_mod_load_order(game_dir)
    order_index = {name.casefold(): i for i, name in enumerate(order)}
    issues: list[dict[str, str]] = []
    if not aml_active:
        for item in scanned:
            key = item["logical_name"].casefold()
            if item["enabled"] and key not in {"dmf", "base"} and key not in order_index:
                issues.append({
                    "severity": "error",
                    "mod": item["logical_name"],
                    "message": "Enabled mod is missing from mod_load_order.txt and may not load in manual mode.",
                })
        enabled_keys = set(enabled) | {"dmf", "base"}
        for listed in order:
            if listed.casefold() not in enabled_keys:
                issues.append({
                    "severity": "warning",
                    "mod": listed,
                    "message": "mod_load_order.txt contains an entry that is missing or currently disabled.",
                })
    for item in scanned:
        if not item["enabled"]:
            continue
        name = item["logical_name"]
        folder = mods_dir(game_dir) / item["folder"]
        rules = mod_declared_rules(folder)
        for dep in rules["require"]:
            dep_key = dep.casefold()
            if dep_key not in available:
                issues.append({
                    "severity": "error",
                    "mod": name,
                    "message": f"Requires '{dep}', but that mod is missing or disabled.",
                })
            elif not aml_active and dep_key not in {"dmf", "base"}:
                if name.casefold() in order_index and dep_key in order_index and order_index[dep_key] > order_index[name.casefold()]:
                    issues.append({
                        "severity": "warning",
                        "mod": name,
                        "message": f"Requires '{dep}' to load first, but the manual load order places it later.",
                    })
        if aml_active:
            continue
        for dep in rules["load_after"]:
            dep_key = dep.casefold()
            if dep_key in order_index and name.casefold() in order_index and order_index[dep_key] > order_index[name.casefold()]:
                issues.append({
                    "severity": "warning",
                    "mod": name,
                    "message": f"Declares load_after '{dep}', but the manual load order places '{dep}' later.",
                })
        for dep in rules["load_before"]:
            dep_key = dep.casefold()
            if dep_key in order_index and name.casefold() in order_index and order_index[dep_key] < order_index[name.casefold()]:
                issues.append({
                    "severity": "warning",
                    "mod": name,
                    "message": f"Declares load_before '{dep}', but the manual load order places '{dep}' first.",
                })
    return issues


def write_mod_load_order(game_dir: Path, enabled_logical_names: list[str]) -> None:
    """Update manual load order while preserving existing custom order/comments where possible."""
    path = mods_dir(game_dir) / "mod_load_order.txt"
    enabled_by_key = {name.casefold(): name for name in enabled_logical_names if name and name.casefold() not in {"dmf", "base"}}
    written: set[str] = set()
    output: list[str] = []
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                output.append(line)
                continue
            key = stripped.casefold()
            if key in enabled_by_key and key not in written:
                output.append(enabled_by_key[key])
                written.add(key)
            # Disabled/removed active entries are intentionally omitted.
    missing = [name for name in enabled_logical_names if name.casefold() in enabled_by_key and name.casefold() not in written]
    if missing:
        if output and output[-1].strip():
            output.append("")
        output.extend(missing)
    while output and not output[-1].strip():
        output.pop()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(output) + ("\n" if output else ""), encoding="utf-8")


def steam_manifest_path(game_dir: Path) -> Path | None:
    """Return the Steam appmanifest path for a normal Steam install, if present."""
    try:
        # <steam library>/steamapps/common/Warhammer 40,000 DARKTIDE
        steamapps = game_dir.resolve().parent.parent
    except OSError:
        steamapps = game_dir.parent.parent
    candidate = steamapps / f"appmanifest_{STEAM_APP_ID}.acf"
    return candidate if candidate.exists() else None


def read_steam_build_id(game_dir: Path) -> str:
    manifest = steam_manifest_path(game_dir)
    if not manifest:
        return ""
    try:
        text = manifest.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    match = re.search(r'"buildid"\s+"([^"]+)"', text, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def bundle_database_path(game_dir: Path) -> Path:
    return game_dir / "bundle" / "bundle_database.data"


def loader_patch_state(game_dir: Path) -> bool | None:
    """Best-effort DML patch detection.

    dtkit-patch adds the DML patch bundle name to bundle_database.data.  Returning
    None means the database could not be inspected; callers should avoid making
    a destructive toggle decision from an unknown state.
    """
    db = bundle_database_path(game_dir)
    if not db.exists():
        return None
    needle = b"9ba626afa44a3aa3.patch_999"
    try:
        # Stream the file so this stays cheap even if the database grows.
        overlap = len(needle) - 1
        tail = b""
        with db.open("rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                blob = tail + chunk
                if needle in blob:
                    return True
                tail = blob[-overlap:] if overlap else b""
        return False
    except OSError:
        return None


def game_update_signature(game_dir: Path) -> dict[str, Any]:
    """Small fingerprint used to show whether Steam/Darktide changed since repair."""
    db = bundle_database_path(game_dir)
    sig: dict[str, Any] = {"build_id": read_steam_build_id(game_dir)}
    try:
        st = db.stat()
        sig.update({"bundle_database_size": st.st_size, "bundle_database_mtime_ns": st.st_mtime_ns})
    except OSError:
        sig.update({"bundle_database_size": 0, "bundle_database_mtime_ns": 0})
    return sig


def _dtkit_executable(game_dir: Path) -> Path:
    candidates = [game_dir / "tools" / "dtkit-patch.exe", game_dir / "tools" / "dtkit-patch"]
    exe = next((p for p in candidates if p.exists()), None)
    if not exe:
        raise ModManagerError("Darktide Mod Loader is not installed; dtkit-patch was not found.")
    return exe


def _dtkit_command(exe: Path, *args: str, platform_name: str | None = None) -> list[str]:
    """Build a portable dtkit-patch invocation.

    Official Windows DML releases use ``dtkit-patch.exe``.  The no-extension
    form exists for other platforms and is also useful in tests.  Windows
    cannot execute a shebang script directly, so when that no-extension file
    is clearly a Python script we invoke it through the current interpreter.
    This does not alter normal Windows behavior for the real ``.exe``.
    """
    rendered = [str(exe), *(str(arg) for arg in args)]
    platform_name = platform_name or os.name
    if platform_name != "nt" or exe.suffix.lower() == ".exe":
        return rendered
    try:
        header = exe.read_bytes()[:256].lower()
    except OSError:
        return rendered
    if header.startswith(b"#!") and b"python" in header.splitlines()[0]:
        return [sys.executable, *rendered]
    return rendered


def _dtkit_help(exe: Path, game_dir: Path) -> str:
    try:
        result = subprocess.run(
            _dtkit_command(exe, "--help"),
            cwd=game_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=10,
            **_hidden_subprocess_kwargs(),
        )
        return result.stdout or ""
    except Exception:
        return ""


def _dtkit_supports_patch(exe: Path, game_dir: Path) -> bool:
    return "--patch" in _dtkit_help(exe, game_dir)


def patch_loader(game_dir: Path) -> tuple[int, str]:
    """Ensure DML is enabled without accidentally toggling an already-patched game."""
    ensure_darktide_not_running()
    ensure_game_dir_writable(game_dir)
    exe = _dtkit_executable(game_dir)
    state = loader_patch_state(game_dir)
    if state is True:
        return 0, "Darktide Mod Loader patch is already enabled."

    supports_patch = _dtkit_supports_patch(exe, game_dir)
    if supports_patch:
        args = _dtkit_command(exe, "--patch", str(game_dir / "bundle"))
    else:
        # Official DML currently ships a --toggle wrapper. Only use toggle when
        # inspection says the database is definitely unpatched; unknown state is
        # not safe to toggle automatically.
        if state is None:
            raise ModManagerError(
                "Could not determine whether Darktide is already patched, and this dtkit-patch build "
                "does not advertise --patch. Run the official toggle_darktide_mods.bat once, then retry."
            )
        args = _dtkit_command(exe, "--toggle", str(game_dir / "bundle"))

    result = subprocess.run(
        args,
        cwd=game_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        **_hidden_subprocess_kwargs(),
    )
    return result.returncode, result.stdout


def unpatch_loader(game_dir: Path) -> tuple[int, str]:
    """Ensure DML is disabled without blindly toggling an unknown state."""
    ensure_darktide_not_running()
    ensure_game_dir_writable(game_dir)
    exe = _dtkit_executable(game_dir)
    state = loader_patch_state(game_dir)
    if state is False:
        return 0, "Darktide Mod Loader patch is already disabled."
    if state is None:
        raise ModManagerError("Could not determine loader patch state; refusing to toggle it automatically.")
    help_text = _dtkit_help(exe, game_dir)
    if "--unpatch" in help_text:
        args = _dtkit_command(exe, "--unpatch", str(game_dir / "bundle"))
    else:
        args = _dtkit_command(exe, "--toggle", str(game_dir / "bundle"))
    result = subprocess.run(
        args,
        cwd=game_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        **_hidden_subprocess_kwargs(),
    )
    if result.returncode == 0:
        state_after = loader_patch_state(game_dir)
        if state_after is not False:
            raise ModManagerError("dtkit-patch returned success, but Tertium could not verify that DML was disabled.")
    return result.returncode, result.stdout


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_cached_archive(record: ModRecord, store: RegistryStore) -> bool | None:
    """Verify a tracked cached archive when an install-time hash is available."""
    cached = store.cache_archive_path(record.mod_id, record.file_id, record.file_name)
    if not cached.exists():
        return None
    if not record.archive_sha256:
        return None
    try:
        return _sha256_file(cached) == record.archive_sha256
    except OSError:
        return None


def cached_aml_patch_state(game_dir: Path, store: RegistryStore) -> bool | None:
    """Compare the installed AML-patched mod_manager.lua with the tracked cached AML archive.

    True means the current file matches the cached AML patch, False means it differs or
    is missing, and None means there is not enough information to verify it safely.
    """
    aml = store.get(AML_MOD_ID)
    if not aml:
        return None
    cached = store.cache_archive_path(aml.mod_id, aml.file_id, aml.file_name)
    current = mods_dir(game_dir) / "base" / "mod_manager.lua"
    if not cached.exists():
        return None
    cache_state = verify_cached_archive(aml, store)
    if cache_state is False:
        return None
    if not current.exists():
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="tertium-aml-check-") as td:
            root = Path(td)
            extract_archive(cached, root)
            candidates = [p for p in root.rglob("mod_manager.lua") if p.parent.name == "base"]
            if not candidates:
                return None
            return _sha256_file(current) == _sha256_file(candidates[0])
    except Exception:
        return None


def repair_after_game_update(
    game_dir: Path,
    store: RegistryStore,
    reapply_aml: Callable[[], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """One-button post-update repair with rollback of bundle_database.data on failure."""
    log = log or (lambda _msg: None)
    ensure_darktide_not_running()
    ensure_game_dir_writable(game_dir)
    ok, reason = validate_game_dir(game_dir)
    if not ok:
        raise ModManagerError(reason)
    if not has_dml(game_dir):
        raise ModManagerError("Darktide Mod Loader is missing. Install/update DML first.")

    before_sig = game_update_signature(game_dir)
    before_state = loader_patch_state(game_dir)
    db = bundle_database_path(game_dir)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = store.backup_dir / f"darktide-update-repair-{stamp}"
    backup_db = backup_dir / "bundle_database.data"

    if db.exists() and before_state is not True:
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(db, backup_db)
        save_json(backup_dir / "repair.json", {
            "created_at": time.time(),
            "signature": before_sig,
            "patched_before": before_state is True,
        })
        log(f"Backed up {db.name} before patching.")

    try:
        if before_state is True:
            output = "Darktide Mod Loader patch is already enabled."
            code = 0
        else:
            log("Re-enabling Darktide Mod Loader…")
            code, output = patch_loader(game_dir)
            if output.strip():
                log(output.strip())
            if code != 0:
                raise ModManagerError(f"dtkit-patch failed with exit code {code}.")

        after_state = loader_patch_state(game_dir)
        if after_state is not True:
            raise ModManagerError("dtkit-patch completed, but Tertium could not verify the DML patch afterward.")

        aml_reapplied = False
        if reapply_aml is not None and store.get(AML_MOD_ID):
            aml_state = cached_aml_patch_state(game_dir, store)
            if aml_state is False:
                log("AML patch differs from the tracked cached copy; re-applying it…")
                reapply_aml()
                aml_reapplied = True
            elif aml_state is True:
                log("AML patch verified; no re-apply needed.")
            else:
                log("AML is tracked but could not be fingerprint-verified; leaving the installed file unchanged.")

        if not has_dmf(game_dir):
            log("Warning: Darktide Mod Framework folder was not found.")

        return {
            "build_id": before_sig.get("build_id", ""),
            "was_patched": before_state is True,
            "is_patched": after_state is True,
            "aml_reapplied": aml_reapplied,
            "dmf_present": has_dmf(game_dir),
            "backup": str(backup_db) if backup_db.exists() else "",
            "output": output.strip(),
            "signature": game_update_signature(game_dir),
        }
    except Exception:
        if backup_db.exists():
            try:
                shutil.copy2(backup_db, db)
                log("Patch failed; restored the pre-repair bundle database backup.")
            except OSError as restore_exc:
                log(f"WARNING: automatic rollback also failed: {restore_exc}")
        raise

def latest_compatible_repair_backup(game_dir: Path, store: RegistryStore) -> Path | None:
    current_build = read_steam_build_id(game_dir)
    if not current_build:
        return None
    candidates = sorted(
        store.backup_dir.glob("darktide-update-repair-*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for folder in candidates:
        db = folder / "bundle_database.data"
        meta = load_json(folder / "repair.json", {})
        build = str((meta.get("signature") or {}).get("build_id") or "") if isinstance(meta, dict) else ""
        if not db.exists():
            continue
        if current_build and build and build != current_build:
            continue
        if current_build and not build:
            continue
        return db
    return None


def restore_latest_loader_backup(
    game_dir: Path,
    store: RegistryStore,
    log: Callable[[str], None] | None = None,
) -> Path:
    log = log or (lambda _msg: None)
    ensure_darktide_not_running()
    ensure_game_dir_writable(game_dir)
    backup = latest_compatible_repair_backup(game_dir, store)
    if not backup:
        raise ModManagerError("No compatible post-update repair backup was found for the current Darktide build.")
    db = bundle_database_path(game_dir)
    if not db.exists():
        raise ModManagerError("Darktide bundle_database.data is missing.")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safety = store.backup_dir / f"pre-loader-rollback-{stamp}"
    safety.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db, safety / db.name)
    shutil.copy2(backup, db)
    log(f"Restored loader backup from {backup.parent.name}; mods should now be disabled for this build.")
    return backup


def backup_inventory(store: RegistryStore) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not store.backup_dir.exists():
        return items
    for folder in store.backup_dir.iterdir():
        if not folder.is_dir():
            continue
        size = 0
        try:
            for f in folder.rglob("*"):
                if f.is_file():
                    size += f.stat().st_size
            mtime = folder.stat().st_mtime
        except OSError:
            continue
        items.append({"path": folder, "size": size, "mtime": mtime})
    return sorted(items, key=lambda x: x["mtime"], reverse=True)


def prune_backups(store: RegistryStore, keep: int = 20) -> dict[str, int]:
    keep = max(1, int(keep))
    items = backup_inventory(store)
    removed = 0
    freed = 0
    for item in items[keep:]:
        try:
            shutil.rmtree(item["path"])
            removed += 1
            freed += int(item["size"])
        except OSError:
            pass
    return {"removed": removed, "freed": freed, "remaining": max(0, len(items) - removed)}


def _redact_path(value: str) -> str:
    home = str(Path.home())
    if home and value.lower().startswith(home.lower()):
        return "%USERPROFILE%" + value[len(home):]
    return value


def _redact_text(value: str) -> str:
    home = str(Path.home())
    if not home:
        return value
    return re.sub(re.escape(home), "%USERPROFILE%", value, flags=re.IGNORECASE)


def _tail_bytes(path: Path, limit: int = 5 * 1024 * 1024) -> bytes:
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - limit))
        return fh.read(limit)


def darktide_console_logs_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Fatshark" / "Darktide" / "console_logs"


def aml_log_summary(game_dir: Path, max_lines: int = 400) -> dict[str, Any]:
    path = mods_dir(game_dir) / "auto_mod_loader_log.txt"
    if not path.exists():
        return {"present": False, "warnings": [], "errors": []}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
    except OSError:
        return {"present": True, "warnings": [], "errors": []}
    warnings: list[str] = []
    errors: list[str] = []
    for line in lines:
        lower = line.lower()
        clean = line.strip()
        if not clean:
            continue
        if any(token in lower for token in ("error", "failed", "missing required", "missing dependency")):
            errors.append(clean)
        elif "warn" in lower:
            warnings.append(clean)
    return {"present": True, "warnings": warnings[-10:], "errors": errors[-10:]}


def _console_log_candidates(limit: int = 30) -> list[Path]:
    console_dir = darktide_console_logs_dir()
    if not console_dir or not console_dir.exists():
        return []
    rows: list[Path] = []
    for path in console_dir.glob("console-*.log"):
        try:
            path.stat()
        except OSError:
            continue
        rows.append(path)
    return sorted(rows, key=lambda p: p.stat().st_mtime, reverse=True)[:max(0, limit)]


def parse_darktide_crash_log(path: Path, installed_mods: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """Extract a conservative mod-suspect signal from a Darktide console crash log."""
    try:
        text = _tail_bytes(path, 8 * 1024 * 1024).decode("utf-8", errors="replace")
        mtime = float(path.stat().st_mtime)
    except OSError:
        return None
    error_matches = list(re.finditer(r"<<(?:Script|Lua) Error>>(.*?)<</(?:Script|Lua) Error>>", text, flags=re.IGNORECASE | re.DOTALL))
    if not error_matches:
        return None
    error_match = error_matches[-1]
    error = " ".join(error_match.group(1).strip().split())[:2000]
    after = text[error_match.end():]
    stack_marker = re.search(r"<<Lua Stack>>(.*?)(?:<</Lua Stack>>|<<[^>]+>>|\Z)", after, flags=re.IGNORECASE | re.DOTALL)
    stack_text = stack_marker.group(1) if stack_marker else after[:12000]

    installed_by_lower = {
        str(item.get("logical_name") or "").lower(): str(item.get("logical_name") or "")
        for item in (installed_mods or [])
    }
    raw_mods: list[str] = []
    for match in re.finditer(r"(?:^|[\\/])mods[\\/]([^\\/\s:]+)[\\/]", stack_text, flags=re.IGNORECASE | re.MULTILINE):
        folder = match.group(1).lstrip("_")
        if folder.lower() in {"dmf", "base"}:
            continue
        canonical = installed_by_lower.get(folder.lower(), folder)
        if canonical.lower() not in {x.lower() for x in raw_mods}:
            raw_mods.append(canonical)

    candidates: list[dict[str, Any]] = []
    for idx, logical in enumerate(raw_mods):
        score = max(60, 100 - idx * 15)
        candidates.append({"logical_name": logical, "score": score, "confidence": "high" if idx == 0 else "medium"})

    # If the stack omitted a mod path, nearby DMF mod log lines are weak evidence only.
    if not candidates:
        nearby = text[max(0, error_match.start() - 12000):error_match.start()]
        logged: list[str] = []
        for match in re.finditer(r"\[MOD\]\[([^\]]+)\]", nearby, flags=re.IGNORECASE):
            name = match.group(1).strip().lstrip("_")
            if name.lower() in {"dmf", "base"}:
                continue
            canonical = installed_by_lower.get(name.lower(), name)
            if canonical.lower() not in {x.lower() for x in logged}:
                logged.append(canonical)
        for idx, logical in enumerate(reversed(logged[-4:])):
            candidates.append({"logical_name": logical, "score": max(20, 45 - idx * 5), "confidence": "low"})

    guid = ""
    guid_pattern = r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    name_match = re.search(guid_pattern, path.name, flags=re.IGNORECASE)
    if name_match:
        guid = name_match.group(1)
    else:
        session_match = re.search(r"(?:\[Session\]|<<Session>>)[^0-9a-f]*" + guid_pattern, text, flags=re.IGNORECASE)
        if session_match:
            guid = session_match.group(1)
    signature_source = error + "|" + "|".join(x["logical_name"] for x in candidates[:3])
    signature = hashlib.sha256(signature_source.encode("utf-8", errors="replace")).hexdigest()[:24]
    return {
        "log_path": str(path),
        "log_name": path.name,
        "mtime": mtime,
        "guid": guid,
        "error": error,
        "signature": signature,
        "candidates": candidates,
    }


def analyze_recent_crash(game_dir: Path, max_logs: int = 12) -> dict[str, Any] | None:
    """Analyze only the newest Darktide session for a crash signal.

    Older crash logs must not keep poisoning the dashboard after a later successful
    play session. Diagnostics still preserve several historical logs; the launcher
    guard intentionally answers only "did the most recent session crash?".
    """
    installed = scan_installed_mods(game_dir)
    candidates = _console_log_candidates(max_logs)
    if not candidates:
        return None
    return parse_darktide_crash_log(candidates[0], installed)


def _installed_version_map(game_dir: Path) -> dict[str, str]:
    return {str(item["logical_name"]).lower(): str(item.get("version") or "") for item in scan_installed_mods(game_dir)}


def active_quarantines(game_dir: Path, compatibility: CompatibilityStore) -> list[dict[str, Any]]:
    build_id = read_steam_build_id(game_dir)
    versions = _installed_version_map(game_dir)
    active: list[dict[str, Any]] = []
    for row in compatibility.all():
        if not row.get("quarantined"):
            continue
        logical = str(row.get("logical_name") or "")
        if str(row.get("build_id") or "") != str(build_id or ""):
            continue
        if str(row.get("mod_version") or "") != versions.get(logical.lower(), ""):
            continue
        active.append(dict(row))
    return active


def quarantine_crash_candidate(
    game_dir: Path,
    store: RegistryStore,
    compatibility: CompatibilityStore,
    logical_name: str,
    finding: dict[str, Any] | None = None,
    maintain_load_order: bool = False,
) -> dict[str, Any]:
    ensure_darktide_not_running()
    scanned = {str(item["logical_name"]).lower(): item for item in scan_installed_mods(game_dir)}
    item = scanned.get(logical_name.lower())
    if not item:
        raise ModManagerError(f"Mod '{logical_name}' is not installed.")
    if logical_name.lower() == "dmf":
        raise ModManagerError("DMF is a protected core dependency and cannot be crash-quarantined here.")
    build_id = read_steam_build_id(game_dir)
    version = str(item.get("version") or "")
    create_mod_state_snapshot(game_dir, store, "pre-crash-quarantine")
    changed = None
    if item.get("enabled"):
        changed = toggle_mod_folder(game_dir, str(item["folder"]), False)
    reason = str((finding or {}).get("error") or "Crash stack implicated this mod.")
    compatibility.set_quarantined(logical_name, build_id, version, True, reason)
    if finding:
        compatibility.record_crash(
            logical_name,
            build_id,
            version,
            str(finding.get("signature") or ""),
            reason,
            str(finding.get("log_name") or ""),
        )
    if maintain_load_order:
        enabled = [
            m["logical_name"] for m in scan_installed_mods(game_dir)
            if m["enabled"] and str(m["logical_name"]).lower() != "dmf"
        ]
        write_mod_load_order(game_dir, enabled)
    sync_registry_folders(game_dir, store)
    return {"logical_name": logical_name, "folder": changed or item["folder"], "build_id": build_id, "mod_version": version}


def enforce_active_quarantines(
    game_dir: Path,
    store: RegistryStore,
    compatibility: CompatibilityStore,
    maintain_load_order: bool = False,
) -> list[str]:
    ensure_darktide_not_running()
    active = active_quarantines(game_dir, compatibility)
    if not active:
        return []
    scanned = {str(item["logical_name"]).lower(): item for item in scan_installed_mods(game_dir)}
    changed: list[str] = []
    for row in active:
        logical = str(row.get("logical_name") or "")
        item = scanned.get(logical.lower())
        if item and item.get("enabled"):
            toggle_mod_folder(game_dir, str(item["folder"]), False)
            changed.append(logical)
    if changed and maintain_load_order:
        enabled = [
            m["logical_name"] for m in scan_installed_mods(game_dir)
            if m["enabled"] and str(m["logical_name"]).lower() != "dmf"
        ]
        write_mod_load_order(game_dir, enabled)
    if changed:
        sync_registry_folders(game_dir, store)
    return changed


def adopt_untracked_installed_mods(game_dir: Path, store: RegistryStore) -> dict[str, Any]:
    """Adopt manually installed folders into local Tertium tracking without guessing a Nexus ID."""
    tracked_folders = {
        folder.lstrip("_").lower()
        for rec in store.all()
        for folder in (rec.folders or [])
        if folder.lower() != "base"
    }
    adopted: list[str] = []
    records = store.all()
    used_ids = {rec.mod_id for rec in records}
    for item in scan_installed_mods(game_dir):
        logical = str(item["logical_name"])
        if logical.lower() == "dmf" or logical.lower() in tracked_folders:
            continue
        digest = hashlib.sha256(logical.lower().encode("utf-8")).hexdigest()
        synthetic = -int(digest[:8], 16)
        while synthetic in used_ids:
            synthetic -= 1
        used_ids.add(synthetic)
        records.append(ModRecord(
            mod_id=synthetic,
            file_id=0,
            name=logical,
            version=str(item.get("version") or ""),
            file_name="",
            folders=[str(item["folder"])],
            enabled=bool(item.get("enabled")),
            installed_at=time.time(),
            source="local",
        ))
        adopted.append(logical)
    if adopted:
        store.save_all(records)
    return {"adopted": adopted, "count": len(adopted)}


def reconcile_installed_registry(game_dir: Path, store: RegistryStore) -> dict[str, Any]:
    """Keep Tertium's local registry aligned with the actual mods folder.

    This is intentionally conservative: unknown installed folders are adopted as
    local-only records, but Tertium never invents Nexus IDs. Local-only records
    whose folders were actually removed are pruned so the registry does not grow
    stale. Nexus-linked records are retained even if temporarily missing because
    they may still be recoverable from cache/backups or a profile operation.
    """
    ok, reason = validate_game_dir(game_dir)
    if not ok:
        raise ModManagerError(reason)

    sync_registry_folders(game_dir, store)
    adopted_result = adopt_untracked_installed_mods(game_dir, store)
    scanned = scan_installed_mods(game_dir)
    present = {str(item["logical_name"]).casefold() for item in scanned}
    records = store.all()
    kept: list[ModRecord] = []
    pruned: list[str] = []
    for rec in records:
        if rec.mod_id in CORE_MOD_IDS or rec.source != "local":
            kept.append(rec)
            continue
        logicals = _record_logical_folders(rec)
        if logicals and logicals.isdisjoint(present):
            pruned.append(rec.name)
            continue
        kept.append(rec)
    if pruned:
        store.save_all(kept)
    # A final sync captures underscore enable/disable state on newly adopted rows.
    sync_registry_folders(game_dir, store)
    return {
        "adopted": list(adopted_result.get("adopted") or []),
        "adopted_count": int(adopted_result.get("count") or 0),
        "pruned": pruned,
        "pruned_count": len(pruned),
    }


def modded_launch_preflight(game_dir: Path, store: RegistryStore) -> dict[str, Any]:
    """Prepare and validate the local mod state before a modded launch.

    Tertium is authoritative for the enabled/disabled state. Every PLAY MODDED
    launch rewrites mod_load_order.txt from the enabled folders while preserving
    the user's existing relative order and comments where possible. This keeps a
    valid manual fallback even when AML is absent, stale, or cannot be verified.
    The preflight never contacts Nexus.
    """
    reconcile = reconcile_installed_registry(game_dir, store)
    enabled_logical_names = [
        item["logical_name"]
        for item in scan_installed_mods(game_dir)
        if item["enabled"] and item["logical_name"].casefold() not in {"dmf", "base"}
    ]
    write_mod_load_order(game_dir, enabled_logical_names)
    sync_registry_folders(game_dir, store)
    aml_active = bool(store.get(AML_MOD_ID) and cached_aml_patch_state(game_dir, store) is True)
    structure = audit_mod_structure(game_dir)
    dependencies = audit_mod_dependencies(game_dir, aml_active=aml_active)
    blockers = [
        dict(issue, source="structure")
        for issue in structure
        if str(issue.get("severity") or "").lower() == "error"
    ]
    blockers.extend(
        dict(issue, source="dependency")
        for issue in dependencies
        if str(issue.get("severity") or "").lower() == "error"
    )
    warnings = [
        dict(issue, source="structure")
        for issue in structure
        if str(issue.get("severity") or "").lower() != "error"
    ]
    warnings.extend(
        dict(issue, source="dependency")
        for issue in dependencies
        if str(issue.get("severity") or "").lower() != "error"
    )
    return {
        "ok": not blockers,
        "reconcile": reconcile,
        "blockers": blockers,
        "warnings": warnings,
        "aml_active": aml_active,
        "load_order_managed_by_tertium": True,
        "load_order_entries": enabled_logical_names,
    }


def health_report(game_dir: Path, store: RegistryStore) -> dict[str, Any]:
    ok, reason = validate_game_dir(game_dir)
    if not ok:
        return {"game_valid": False, "reason": reason}
    scanned = scan_installed_mods(game_dir)
    aml_state = cached_aml_patch_state(game_dir, store) if store.get(AML_MOD_ID) else None
    log_health = aml_log_summary(game_dir)
    try:
        free_bytes = int(shutil.disk_usage(game_dir).free)
    except OSError:
        free_bytes = 0
    running = is_darktide_running()
    writable = None if running else game_dir_writable(game_dir)
    cache_corrupt: list[str] = []
    cache_missing: list[str] = []
    for rec in store.all():
        state = verify_cached_archive(rec, store)
        if state is False:
            cache_corrupt.append(rec.name)
        elif state is None and rec.archive_sha256:
            cache_missing.append(rec.name)
    structure_issues = audit_mod_structure(game_dir)
    dependency_issues = audit_mod_dependencies(game_dir, aml_active=(aml_state is True))
    stale_parts = 0
    cutoff = time.time() - (6 * 3600)
    for part in store.cache_dir.glob("*.part"):
        try:
            if part.stat().st_mtime <= cutoff:
                stale_parts += 1
        except OSError:
            pass
    safe_mode_snapshot = latest_state_snapshot(store, "troubleshooting-safe-mode", unrestored_only=True)
    mod_state_snapshot = latest_state_snapshot(store, unrestored_only=True)
    compatibility = CompatibilityStore(store.root)
    quarantines = active_quarantines(game_dir, compatibility)
    recent_crash = analyze_recent_crash(game_dir)
    tracked_folders = {
        f.lstrip("_").lower()
        for rec in store.all()
        for f in (rec.folders or [])
        if f.lower() != "base"
    }
    untracked = [
        m["logical_name"] for m in scanned
        if str(m["logical_name"]).lower() != "dmf" and str(m["logical_name"]).lower() not in tracked_folders
    ]
    return {
        "game_valid": True,
        "darktide_running": running,
        "game_writable": writable,
        "free_bytes": free_bytes,
        "build_id": read_steam_build_id(game_dir),
        "dml_present": has_dml(game_dir),
        "dml_patch_state": loader_patch_state(game_dir),
        "dmf_present": has_dmf(game_dir),
        "aml_tracked": store.get(AML_MOD_ID) is not None,
        "aml_patch_state": aml_state,
        "installed_mods": len(scanned),
        "enabled_mods": sum(1 for m in scanned if m["enabled"]),
        "disabled_mods": sum(1 for m in scanned if not m["enabled"]),
        "aml_log_errors": log_health["errors"],
        "aml_log_warnings": log_health["warnings"],
        "cache_corrupt": cache_corrupt,
        "cache_missing": cache_missing,
        "structure_issues": structure_issues,
        "dependency_issues": dependency_issues,
        "stale_partial_downloads": stale_parts,
        "safe_mode_restore_available": safe_mode_snapshot is not None,
        "mod_state_restore_available": mod_state_snapshot is not None,
        "active_quarantines": quarantines,
        "recent_crash": recent_crash,
        "untracked_mods": untracked,
    }


def create_diagnostic_bundle(
    game_dir: Path,
    store: RegistryStore,
    destination: Path,
    app_version: str,
    include_latest_logs: int = 6,
    include_crash_logs: int = 3,
) -> Path:
    ok, reason = validate_game_dir(game_dir)
    if not ok:
        raise ModManagerError(reason)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tracked = [r.to_dict() for r in store.all()]
    scanned = scan_installed_mods(game_dir)
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "app_version": app_version,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "game_dir": _redact_path(str(game_dir)),
        "steam_build_id": read_steam_build_id(game_dir),
        "loader_patch_state": loader_patch_state(game_dir),
        "dml_present": has_dml(game_dir),
        "dmf_present": has_dmf(game_dir),
        "aml_tracked": store.get(AML_MOD_ID) is not None,
        "installed_mods": scanned,
        "tracked_mods": tracked,
        "structure_issues": audit_mod_structure(game_dir),
        "dependency_issues": audit_mod_dependencies(
            game_dir,
            aml_active=(cached_aml_patch_state(game_dir, store) is True if store.get(AML_MOD_ID) else False),
        ),
        "recent_crash": analyze_recent_crash(game_dir),
        "active_quarantines": active_quarantines(game_dir, CompatibilityStore(store.root)),
    }
    lines = [
        f"Tertium Mod Manager {app_version}",
        f"Generated: {payload['generated_utc']}",
        f"OS: {payload['platform']}",
        f"Darktide: {payload['game_dir']}",
        f"Steam build: {payload['steam_build_id'] or 'unknown'}",
        f"DML: {'present' if payload['dml_present'] else 'missing'} / patch={payload['loader_patch_state']}",
        f"DMF: {'present' if payload['dmf_present'] else 'missing'}",
        f"AML tracked: {payload['aml_tracked']}",
        "",
        "Installed mods:",
    ]
    for mod in scanned:
        state = "ON " if mod["enabled"] else "OFF"
        lines.append(f"- {state} {mod['logical_name']} {mod.get('version') or ''}".rstrip())

    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("diagnostics.txt", "\n".join(lines) + "\n")
        zf.writestr("diagnostics.json", json.dumps(payload, indent=2, sort_keys=True))
        load_order = mods_dir(game_dir) / "mod_load_order.txt"
        if load_order.exists():
            zf.writestr("darktide/mod_load_order.txt", load_order.read_text(encoding="utf-8", errors="replace"))
        aml_log = mods_dir(game_dir) / "auto_mod_loader_log.txt"
        if aml_log.exists():
            text = _tail_bytes(aml_log, 2 * 1024 * 1024).decode("utf-8", errors="replace")
            zf.writestr("darktide/auto_mod_loader_log.txt", _redact_text(text))
        console_dir = darktide_console_logs_dir()
        if console_dir and console_dir.exists():
            all_logs = _console_log_candidates(max(30, include_latest_logs + include_crash_logs + 8))
            selected = list(all_logs[:max(0, include_latest_logs)])
            crash_added = 0
            for candidate in all_logs:
                if crash_added >= max(0, include_crash_logs):
                    break
                if candidate in selected:
                    continue
                if parse_darktide_crash_log(candidate, scanned):
                    selected.append(candidate)
                    crash_added += 1
            selected = sorted(selected, key=lambda p: p.stat().st_mtime, reverse=True)
            for log_path in selected:
                text = _tail_bytes(log_path).decode("utf-8", errors="replace")
                zf.writestr(f"darktide/console_logs/{log_path.name}", _redact_text(text))
        manager_log = store.root / "tertium.log"
        if manager_log.exists():
            text = _tail_bytes(manager_log, 2 * 1024 * 1024).decode("utf-8", errors="replace")
            zf.writestr("tertium/tertium.log", _redact_text(text))
        crash_log = store.root / "tertium-crash.log"
        if crash_log.exists():
            text = _tail_bytes(crash_log, 2 * 1024 * 1024).decode("utf-8", errors="replace")
            zf.writestr("tertium/tertium-crash.log", _redact_text(text))
    return destination


def open_game_launcher(game_dir: Path) -> None:
    launcher = game_dir / "launcher" / "Launcher.exe"
    if launcher.exists():
        subprocess.Popen([str(launcher)], cwd=launcher.parent)
        return
    if os.name == "nt":
        os.startfile("steam://rungameid/1361210")  # type: ignore[attr-defined]
    else:
        raise ModManagerError("Darktide launcher could not be located.")
