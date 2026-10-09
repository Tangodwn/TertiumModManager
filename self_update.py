from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from version import __version__

RELEASE_REPO = "Tangodwn/TertiumModManager"
RELEASE_API = f"https://api.github.com/repos/{RELEASE_REPO}/releases/latest"
USER_AGENT = f"TertiumModManager/{__version__} (+self updater)"
PACKAGE_SUFFIX = "-Portable-x64.zip"


class SelfUpdateError(RuntimeError):
    pass


@dataclass
class ReleaseInfo:
    version: str
    tag: str
    page_url: str
    package_url: str
    package_name: str
    package_size: int = 0
    package_sha256: str = ""
    checksum_url: str = ""
    published_at: str = ""


def parse_version(value: str) -> tuple[int, ...]:
    text = str(value or "").strip()
    if text.lower().startswith("v"):
        text = text[1:]
    m = re.match(r"^(\d+(?:\.\d+)*)", text)
    if not m:
        raise ValueError(f"Invalid version: {value!r}")
    return tuple(int(part) for part in m.group(1).split("."))


def is_newer_version(remote: str, current: str = __version__) -> bool:
    try:
        left = parse_version(remote)
        right = parse_version(current)
    except ValueError:
        return False
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def _request_json(url: str, timeout: float = 15.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403, 404}:
            raise SelfUpdateError(
                "Tertium's release feed is not publicly reachable. "
                "Automatic self-updates require the public GitHub release channel."
            ) from exc
        raise SelfUpdateError(f"GitHub release check failed with HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise SelfUpdateError(f"Could not reach GitHub releases: {exc.reason}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SelfUpdateError(f"Could not read GitHub release metadata: {exc}") from exc


def fetch_latest_release(timeout: float = 15.0) -> ReleaseInfo:
    data = _request_json(RELEASE_API, timeout=timeout)
    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        raise SelfUpdateError("GitHub returned a release without a version tag.")
    version = tag[1:] if tag.lower().startswith("v") else tag
    expected_name = f"TertiumModManager-{version}-Portable-x64.zip"

    package = None
    checksum = None
    for asset in data.get("assets", []):
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        if name == expected_name:
            package = asset
        elif name == expected_name + ".sha256":
            checksum = asset
    if not package:
        raise SelfUpdateError(
            f"Latest release does not contain the in-app update package {expected_name}."
        )

    digest = str(package.get("digest") or "").strip()
    sha = digest.split(":", 1)[1].strip().lower() if digest.lower().startswith("sha256:") else ""
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        sha = ""

    return ReleaseInfo(
        version=version,
        tag=tag,
        page_url=str(data.get("html_url") or f"https://github.com/{RELEASE_REPO}/releases/tag/{tag}"),
        package_url=str(package.get("browser_download_url") or ""),
        package_name=expected_name,
        package_size=int(package.get("size") or 0),
        package_sha256=sha,
        checksum_url=str((checksum or {}).get("browser_download_url") or ""),
        published_at=str(data.get("published_at") or ""),
    )


def download_update_package(
    release: ReleaseInfo,
    destination: Path,
    progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    if not release.package_url:
        raise SelfUpdateError("Release does not contain an in-app update package URL.")
    progress = progress or (lambda _done, _total: None)
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    try:
        part.unlink(missing_ok=True)
        req = urllib.request.Request(release.package_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as response:
            total_s = response.headers.get("Content-Length")
            total = int(total_s) if total_s and total_s.isdigit() else (release.package_size or None)
            done = 0
            with part.open("wb") as fh:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    progress(done, total)
        if total and done != total:
            raise SelfUpdateError(f"Update download ended early ({done:,} of {total:,} bytes).")
        part.replace(destination)
        return destination
    except SelfUpdateError:
        part.unlink(missing_ok=True)
        raise
    except (OSError, urllib.error.URLError) as exc:
        part.unlink(missing_ok=True)
        reason = getattr(exc, "reason", str(exc))
        raise SelfUpdateError(f"Update download failed: {reason}") from exc


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def _fetch_expected_checksum(release: ReleaseInfo, timeout: float = 15.0) -> str:
    expected = release.package_sha256.strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", expected):
        return expected
    if not release.checksum_url:
        raise SelfUpdateError("Release does not provide a SHA-256 digest or checksum file.")
    req = urllib.request.Request(release.checksum_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            text = response.read(4096).decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError) as exc:
        reason = getattr(exc, "reason", str(exc))
        raise SelfUpdateError(f"Could not fetch update checksum: {reason}") from exc
    match = re.search(r"\b([0-9a-fA-F]{64})\b", text)
    if not match:
        raise SelfUpdateError("Release checksum file does not contain a valid SHA-256 hash.")
    return match.group(1).lower()


def verify_update_package(path: Path, release: ReleaseInfo) -> str:
    actual = sha256_file(path)
    expected = _fetch_expected_checksum(release)
    if actual != expected:
        raise SelfUpdateError(
            f"Downloaded update failed SHA-256 verification. Expected {expected}, got {actual}."
        )
    return actual


def update_cache_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(tempfile.gettempdir())
    path = base / "TertiumModManager" / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def installed_executable() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "Programs" / "TertiumModManager" / "TertiumModManager.exe"
    return Path(sys.executable).resolve()


def schedule_windows_package_update(package: Path, current_pid: int | None = None) -> Path:
    """Apply a verified ZIP after Tertium exits, then restart the app.

    The handoff runs from the update-cache directory, never from the installation
    directory. It updates files in place instead of renaming the live install
    directory, which avoids the Windows access-denied failure seen when the helper
    inherits the app folder as its working directory.
    """
    if os.name != "nt":
        raise SelfUpdateError("Automatic package updates are only supported on Windows.")
    if not package.exists():
        raise SelfUpdateError("Downloaded update package is missing.")

    pid = int(current_pid or os.getpid())
    current_exe = installed_executable()
    install_dir = current_exe.parent
    restart = install_dir / "TertiumModManager.exe"
    cache = update_cache_dir()
    stamp = f"{int(time.time())}-{pid}"
    script = cache / f"apply-update-{stamp}.ps1"
    stage = cache / f"stage-{stamp}"
    backup = cache / f"backup-{stamp}"
    log = cache / "last-update.log"

    def q(value: Path) -> str:
        return str(value).replace("'", "''")

    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"$pidToWait = {pid}\n"
        f"$package = '{q(package)}'\n"
        f"$install = '{q(install_dir)}'\n"
        f"$restart = '{q(restart)}'\n"
        f"$stage = '{q(stage)}'\n"
        f"$backup = '{q(backup)}'\n"
        f"$log = '{q(log)}'\n"
        f"$cache = '{q(cache)}'\n"
        "Set-Location -LiteralPath $cache\n"
        "Set-Content -LiteralPath $log -Value ('Tertium update started ' + (Get-Date -Format o))\n"
        "try {\n"
        "  while (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue) { Start-Sleep -Milliseconds 250 }\n"
        "  Add-Content -LiteralPath $log -Value 'Main process exited.'\n"
        "  if (Test-Path $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }\n"
        "  New-Item -ItemType Directory -Path $stage | Out-Null\n"
        "  Expand-Archive -LiteralPath $package -DestinationPath $stage -Force\n"
        "  $candidate = Join-Path $stage 'TertiumModManager.exe'\n"
        "  if (-not (Test-Path $candidate)) { throw 'Update package is missing TertiumModManager.exe.' }\n"
        "  Add-Content -LiteralPath $log -Value 'Package expanded and validated.'\n"
        "  if (Test-Path $backup) { Remove-Item -LiteralPath $backup -Recurse -Force }\n"
        "  New-Item -ItemType Directory -Path $backup | Out-Null\n"
        "  if (Test-Path $install) { Copy-Item -Path (Join-Path $install '*') -Destination $backup -Recurse -Force -ErrorAction Stop }\n"
        "  if (-not (Test-Path $install)) { New-Item -ItemType Directory -Path $install | Out-Null }\n"
        "  Copy-Item -Path (Join-Path $stage '*') -Destination $install -Recurse -Force -ErrorAction Stop\n"
        "  if (-not (Test-Path $restart)) { throw 'Updated executable is missing after replacement.' }\n"
        "  Add-Content -LiteralPath $log -Value 'Application files replaced.'\n"
        "  Start-Process -FilePath $restart -WorkingDirectory $install\n"
        "  Add-Content -LiteralPath $log -Value ('Restart requested ' + (Get-Date -Format o))\n"
        "  Start-Sleep -Seconds 2\n"
        "  Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue\n"
        "  Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue\n"
        "  Remove-Item -LiteralPath $package -Force -ErrorAction SilentlyContinue\n"
        "} catch {\n"
        "  Add-Content -LiteralPath $log -Value ('FAILED: ' + $_.Exception.Message)\n"
        "  if (Test-Path $backup) {\n"
        "    try { Copy-Item -Path (Join-Path $backup '*') -Destination $install -Recurse -Force -ErrorAction Stop } catch { Add-Content -LiteralPath $log -Value ('ROLLBACK FAILED: ' + $_.Exception.Message) }\n"
        "  }\n"
        "  exit 1\n"
        "}\n",
        encoding="utf-8",
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(script),
        ],
        creationflags=creationflags,
        close_fds=True,
        cwd=str(cache),
    )
    return script
