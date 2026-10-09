from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from version import __version__

STEAM_APP_ID = "1361210"
STEAM_NEWS_API = (
    "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
    "?appid=1361210&count=6&maxlength=0&format=json"
)
OFFICIAL_NEWS_URL = "https://store.steampowered.com/news/app/1361210"
CACHE_MAX_AGE = 6 * 60 * 60


def parse_steam_news_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return the newest usable Darktide Steam-news item without copying article content."""
    appnews = payload.get("appnews") if isinstance(payload, dict) else None
    items = appnews.get("newsitems") if isinstance(appnews, dict) else None
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        parsed = urlparse(url)
        if (not title or parsed.scheme != "https" or
                parsed.hostname not in {"store.steampowered.com", "steamcommunity.com", "fatsharkgames.com", "www.fatsharkgames.com"}):
            continue
        try:
            epoch = int(item.get("date") or 0)
        except (TypeError, ValueError):
            epoch = 0
        date_text = ""
        if epoch > 0:
            try:
                date_text = datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%b %d, %Y")
            except (OverflowError, OSError, ValueError):
                date_text = ""
        return {
            "title": title[:180],
            "url": url,
            "date": date_text,
            "date_epoch": epoch,
            "feed": str(item.get("feedlabel") or "Steam").strip()[:80] or "Steam",
        }
    return None


def _read_cache(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    item = raw.get("item")
    if not isinstance(item, dict):
        return None
    return {
        "item": item,
        "fetched_at": float(raw.get("fetched_at") or 0.0),
    }


def _write_cache(path: Path, item: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"fetched_at": time.time(), "item": item}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        pass


def cached_official_news(cache_path: Path) -> dict[str, Any] | None:
    cached = _read_cache(cache_path)
    return dict(cached["item"]) if cached else None


def fetch_official_news(cache_path: Path, timeout: float = 6.0, force: bool = False) -> dict[str, Any]:
    """Fetch newest official Steam news metadata; fall back to cache without bundling artwork/content."""
    cached = _read_cache(cache_path)
    if cached and not force and (time.time() - cached["fetched_at"]) < CACHE_MAX_AGE:
        item = dict(cached["item"])
        item["cached"] = True
        return item

    request = urllib.request.Request(
        STEAM_NEWS_API,
        headers={
            "User-Agent": f"TertiumModManager/{__version__} (+community Darktide mod manager)",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, float(timeout))) as response:
            data = response.read(2 * 1024 * 1024)
        payload = json.loads(data.decode("utf-8", errors="replace"))
        item = parse_steam_news_payload(payload)
        if item:
            _write_cache(cache_path, item)
            item = dict(item)
            item["cached"] = False
            return item
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError):
        pass

    if cached:
        item = dict(cached["item"])
        item["cached"] = True
        return item
    return {
        "title": "Official Darktide Updates",
        "date": "",
        "url": OFFICIAL_NEWS_URL,
        "feed": "Steam",
        "cached": True,
    }
