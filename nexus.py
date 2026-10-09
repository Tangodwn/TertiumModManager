from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core import GAME_DOMAIN, ModManagerError
from version import __version__

API_ROOT = "https://api.nexusmods.com/v1"
USER_AGENT = f"TertiumModManager/{__version__} (+Darktide mod manager)"
MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024 * 1024


@dataclass
class NxmLink:
    domain: str
    mod_id: int
    file_id: int
    key: str | None = None
    expires: str | None = None
    user_id: str | None = None


def parse_nxm_url(url: str) -> NxmLink:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme.lower() != "nxm":
        raise ModManagerError("Not an nxm:// URL.")
    domain = parsed.netloc.lower()
    m = re.match(r"^/mods/(\d+)/files/(\d+)", parsed.path, flags=re.IGNORECASE)
    if not m:
        raise ModManagerError("The Nexus link does not contain a mod ID and file ID.")
    q = urllib.parse.parse_qs(parsed.query)
    return NxmLink(
        domain=domain,
        mod_id=int(m.group(1)),
        file_id=int(m.group(2)),
        key=(q.get("key") or [None])[0],
        expires=(q.get("expires") or [None])[0],
        user_id=(q.get("user_id") or [None])[0],
    )


def parse_nexus_mod_reference(value: str, domain: str = GAME_DOMAIN) -> int:
    """Parse a Nexus mod page URL or bare numeric mod ID without guessing."""
    text = str(value or "").strip()
    if not text:
        raise ModManagerError("Enter a Nexus mod page URL or numeric mod ID.")
    if text.isdigit():
        mod_id = int(text)
        if mod_id <= 0:
            raise ModManagerError("Nexus mod ID must be greater than zero.")
        return mod_id

    if text.lower().startswith("nxm://"):
        link = parse_nxm_url(text)
        if link.domain.lower() != domain.lower():
            raise ModManagerError(f"Expected a {domain} Nexus link.")
        return link.mod_id

    parsed = urllib.parse.urlparse(text if "://" in text else "https://" + text)
    host = (parsed.hostname or "").lower()
    if host not in {"nexusmods.com", "www.nexusmods.com"}:
        raise ModManagerError("That is not a Nexus Mods URL.")

    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    lower = [part.lower() for part in parts]
    if "mods" not in lower:
        raise ModManagerError("The Nexus URL does not contain a mod ID.")
    idx = lower.index("mods")
    if idx + 1 >= len(parts) or not parts[idx + 1].isdigit():
        raise ModManagerError("The Nexus URL does not contain a numeric mod ID.")
    if idx > 0:
        game_part = lower[idx - 1]
        if game_part not in {domain.lower(), "games"} and domain.lower() not in lower:
            raise ModManagerError(f"Expected a Nexus page for {domain}.")
    return int(parts[idx + 1])


def normalize_version(value: Any) -> str:
    """Conservatively normalize version text for exact installed-file matching."""
    text = str(value or "").strip().casefold()
    if text.startswith("v") and len(text) > 1 and text[1].isdigit():
        text = text[1:]
    return re.sub(r"\s+", "", text)


def matching_files_for_version(payload: dict[str, Any], installed_version: str) -> list[dict[str, Any]]:
    """Return Nexus files whose declared version exactly matches the installed mod version."""
    wanted = normalize_version(installed_version)
    if not wanted or wanted in {"local", "unknown"}:
        return []
    matches: list[dict[str, Any]] = []
    for item in payload.get("files", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        versions = {
            normalize_version(item.get("version")),
            normalize_version(item.get("mod_version")),
        }
        versions.discard("")
        if wanted in versions:
            matches.append(dict(item))
    return matches


class NexusClient:
    def __init__(self, api_key: str):
        self.api_key = api_key.strip()
        if not self.api_key:
            raise ModManagerError("A Nexus Mods API key is required.")

    def _request_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = API_ROOT + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        last_error: Exception | None = None
        for attempt in range(3):
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "apikey": self.api_key,
                    "User-Agent": USER_AGENT,
                    "Application-Name": "Tertium Mod Manager",
                    "Application-Version": __version__,
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                if exc.code == 403:
                    raise ModManagerError(
                        "Nexus requires browser authorization for this download. "
                        "Open the Nexus file page and click Mod Manager Download so Tertium receives an nxm:// link with a temporary authorization key."
                    ) from exc
                if exc.code in {429, 500, 502, 503, 504} and attempt < 2:
                    last_error = exc
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    try:
                        delay = min(max(float(retry_after), 1.0), 10.0) if retry_after else (1.5 * (attempt + 1))
                    except ValueError:
                        delay = 1.5 * (attempt + 1)
                    time.sleep(delay)
                    continue
                raise ModManagerError(f"Nexus API error {exc.code}: {body[:500]}") from exc
            except urllib.error.URLError as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise ModManagerError(f"Could not reach Nexus Mods: {exc.reason}") from exc
        raise ModManagerError(f"Could not reach Nexus Mods: {last_error}")

    def validate(self) -> dict[str, Any]:
        return self._request_json("/users/validate.json")

    def mod_info(self, mod_id: int, domain: str = GAME_DOMAIN) -> dict[str, Any]:
        return self._request_json(f"/games/{domain}/mods/{mod_id}.json")

    def mod_files(self, mod_id: int, domain: str = GAME_DOMAIN) -> dict[str, Any]:
        return self._request_json(f"/games/{domain}/mods/{mod_id}/files.json")

    def file_info(self, mod_id: int, file_id: int, domain: str = GAME_DOMAIN) -> dict[str, Any]:
        payload = self.mod_files(mod_id, domain)
        for item in payload.get("files", []):
            if int(item.get("file_id", -1)) == int(file_id):
                return item
        return {"file_id": file_id, "name": f"File {file_id}", "version": "", "file_name": ""}

    def latest_successor(self, mod_id: int, file_id: int, domain: str = GAME_DOMAIN) -> dict[str, Any] | None:
        payload = self.mod_files(mod_id, domain)
        current = int(file_id)
        by_old = {int(x.get("old_file_id")): int(x.get("new_file_id")) for x in payload.get("file_updates", []) if x.get("old_file_id") and x.get("new_file_id")}
        seen: set[int] = set()
        while current in by_old and current not in seen:
            seen.add(current)
            current = by_old[current]
        if current == int(file_id):
            return None
        for f in payload.get("files", []):
            if int(f.get("file_id", -1)) == current:
                return f
        return {"file_id": current, "name": f"File {current}", "version": "", "file_name": ""}

    def download_urls(self, link: NxmLink) -> list[dict[str, Any]]:
        params = {}
        if link.key and link.expires:
            params = {"key": link.key, "expires": link.expires}
        data = self._request_json(
            f"/games/{link.domain}/mods/{link.mod_id}/files/{link.file_id}/download_link.json",
            params=params,
        )
        if not isinstance(data, list) or not data:
            raise ModManagerError("Nexus returned no download mirrors.")
        return data

    def direct_download_urls(self, mod_id: int, file_id: int, domain: str = GAME_DOMAIN) -> list[dict[str, Any]]:
        """Request an authenticated direct download URL when Nexus grants that capability."""
        link = NxmLink(domain=domain, mod_id=mod_id, file_id=file_id)
        return self.download_urls(link)

    # Compatibility alias for older Tertium builds / third-party scripts.
    def premium_download_urls(self, mod_id: int, file_id: int, domain: str = GAME_DOMAIN) -> list[dict[str, Any]]:
        return self.direct_download_urls(mod_id, file_id, domain)


def download_file(
    url: str,
    dest: Path,
    progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    """Download atomically so a killed/failed download never masquerades as a valid cache file."""
    progress = progress or (lambda _done, _total: None)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            if part.exists():
                part.unlink()
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as response:
                total_s = response.headers.get("Content-Length")
                total = int(total_s) if total_s and total_s.isdigit() else None
                if total is not None and total > MAX_DOWNLOAD_BYTES:
                    raise ModManagerError(
                        f"Download is {total / 1024**3:.1f} GiB, above Tertium's {MAX_DOWNLOAD_BYTES / 1024**3:.0f} GiB safety limit."
                    )
                done = 0
                with open(part, "wb") as fh:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        done += len(chunk)
                        if done > MAX_DOWNLOAD_BYTES:
                            raise ModManagerError(
                                f"Download exceeded Tertium's {MAX_DOWNLOAD_BYTES / 1024**3:.0f} GiB safety limit."
                            )
                        fh.write(chunk)
                        progress(done, total)
                if total is not None and done != total:
                    raise ModManagerError(f"Download ended early ({done:,} of {total:,} bytes).")
            part.replace(dest)
            return dest
        except ModManagerError:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
                continue
            reason = getattr(exc, "reason", str(exc))
            raise ModManagerError(f"Download failed: {reason}") from exc
    raise ModManagerError(f"Download failed: {last_error}")


def choose_mirror(entries: list[dict[str, Any]]) -> str:
    for item in entries:
        uri = item.get("URI") or item.get("uri")
        if uri:
            return str(uri)
    raise ModManagerError("Nexus returned download mirrors without a usable URI.")


def browser_authorization_required(exc: BaseException) -> bool:
    """Return True when a Nexus download failure should fall back to nxm:// browser authorization."""
    text = str(exc).casefold()
    markers = (
        "requires browser authorization",
        "nexus returned 403",
        "temporary authorization key",
        "temporary key",
        "mod manager download",
    )
    return any(marker in text for marker in markers)
