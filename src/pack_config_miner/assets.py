"""Card-art assets: load an image cache (id -> URL) and download reference images.

Catalog art is the prerequisite for the Tier 2 CV fallback (embedding match against reference
images; plan.md section 8) and is also handy for building synthetic test fixtures for the
vertical slice. The art lives in a separate ``image_cache.json`` (a flat ``{cardId: url}`` map),
keyed by the same collector id as the catalog.

Downloads land under an art root as ``<sanitized-id>.<ext>`` plus a ``manifest.json`` mapping
each exact card id to its local filename (ids may contain spaces or ``^``/``*`` suffixes that are
not filesystem-safe, so the manifest is the source of truth for id -> file).
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse

# id -> image URL
ImageCache = dict[str, str]

_MANIFEST_NAME = "manifest.json"
_ILLEGAL = '<>:"/\\|?*'
_USER_AGENT = "pack-config-miner/0.0.1 (+art-fetch)"


def load_image_cache(source: str | Path) -> ImageCache:
    """Load an image cache from a local path or an ``http(s)`` URL into a ``{id: url}`` map."""
    s = str(source)
    if s.startswith(("http://", "https://")):
        req = urllib.request.Request(s, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8")
    else:
        text = Path(source).read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("image cache must be a JSON object of {cardId: url}")
    return {str(k): str(v) for k, v in data.items()}


def _safe_filename(card_id: str, url: str) -> str:
    """A filesystem-safe filename for a card id, preserving the URL's extension."""
    ext = Path(unquote(urlparse(url).path)).suffix or ".jpg"
    stem = "".join("_" if c in _ILLEGAL else c for c in card_id).strip()
    return f"{stem}{ext}"


def _read_manifest(art_root: Path) -> dict[str, str]:
    path = art_root / _MANIFEST_NAME
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _write_manifest(art_root: Path, manifest: dict[str, str]) -> None:
    (art_root / _MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8"
    )


def select_ids(
    cache: ImageCache,
    *,
    ids: list[str] | None = None,
    set_code: str | None = None,
    limit: int | None = None,
) -> list[str]:
    """Pick which card ids to fetch: explicit ``ids``, or all ids whose id starts with
    ``<set_code>-``, optionally capped at ``limit``. Order follows the cache's key order."""
    if ids is not None:
        chosen = [i for i in ids if i in cache]
    elif set_code is not None:
        prefix = f"{set_code}-"
        chosen = [i for i in cache if i.startswith(prefix)]
    else:
        chosen = list(cache)
    if limit is not None:
        chosen = chosen[:limit]
    return chosen


def fetch_art(
    cache: ImageCache,
    art_root: str | Path,
    *,
    ids: list[str] | None = None,
    set_code: str | None = None,
    limit: int | None = None,
    overwrite: bool = False,
    sleep: float = 0.05,
    timeout: float = 30.0,
) -> dict[str, str]:
    """Download selected card images into ``art_root``; update + return the id -> filename manifest.

    Existing files are skipped unless ``overwrite``. ``sleep`` seconds pass between network
    fetches to stay polite. Returns the full (merged) manifest.
    """
    root = Path(art_root)
    root.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest(root)

    chosen = select_ids(cache, ids=ids, set_code=set_code, limit=limit)
    for card_id in chosen:
        url = cache[card_id]
        filename = _safe_filename(card_id, url)
        dest = root / filename
        if dest.exists() and not overwrite:
            manifest[card_id] = filename
            continue
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            dest.write_bytes(resp.read())
        manifest[card_id] = filename
        if sleep:
            time.sleep(sleep)

    _write_manifest(root, manifest)
    return manifest
