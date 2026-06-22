"""Stage [1] -- Ingest (plan.md section 4).

Resolve each :class:`FootageSource` in a Footage Manifest to a local media file:
remote URLs are downloaded with ``yt-dlp``; local paths pass through. The result is a
:class:`MediaClip` the frame-sampling stage [2] can decode.

``yt-dlp`` is an optional (``media`` extra) dependency and is imported lazily so the core
package and the M0 tests do not require it. Container/fps normalisation is deferred to the
decoder in :mod:`pack_config_miner.pipeline.frames` -- PyAV decodes any container, and the
sampler controls the effective frame rate -- so ingest only has to land a readable local file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..contracts.manifest import Capture, FootageSource, Manifest


@dataclass(frozen=True)
class MediaClip:
    """A manifest source resolved to a local, decodable media file.

    Carries the bits stage [2] (frames) and later stages need: where the file is, which set it
    belongs to, and whether it is ``controlled`` (one-pack-per-shot) or ``uncontrolled``.
    """

    source_id: str
    set_code: str
    capture: Capture
    path: Path


def load_manifest(path: str | Path) -> Manifest:
    """Read and validate a Footage Manifest JSON file."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return Manifest.model_validate(raw)


def is_remote(uri: str) -> bool:
    """True if ``uri`` is an ``http(s)`` URL (download) rather than a local path (passthrough)."""
    return uri.startswith(("http://", "https://"))


def _import_yt_dlp():
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover - exercised only without the extra installed
        raise RuntimeError(
            "Ingesting remote footage needs yt-dlp. Install the media extra: "
            'pip install -e ".[media]"'
        ) from exc
    return yt_dlp


def _download_remote(uri: str, source_id: str, cache_dir: Path, *, overwrite: bool) -> Path:
    """Download ``uri`` into ``cache_dir`` as ``<source_id>.<ext>``; return the local path.

    A previously downloaded file is reused unless ``overwrite`` is set.
    """
    yt_dlp = _import_yt_dlp()
    cache_dir.mkdir(parents=True, exist_ok=True)
    ydl_opts = {
        "outtmpl": str(cache_dir / f"{source_id}.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(uri, download=False)
        dest = Path(ydl.prepare_filename(info))
        if dest.exists() and not overwrite:
            return dest
        ydl.download([uri])
    return dest


def ingest_source(
    source: FootageSource,
    cache_dir: str | Path = "data/media",
    *,
    overwrite: bool = False,
) -> MediaClip:
    """Resolve one :class:`FootageSource` to a local :class:`MediaClip`.

    Remote (``http(s)``) URIs are downloaded into ``cache_dir``; local paths pass through after
    confirming the file exists.
    """
    if is_remote(source.uri):
        path = _download_remote(source.uri, source.id, Path(cache_dir), overwrite=overwrite)
    else:
        path = Path(source.uri)
        if not path.exists():
            raise FileNotFoundError(f"footage source {source.id!r}: local file not found: {path}")
    return MediaClip(
        source_id=source.id,
        set_code=source.setCode,
        capture=source.capture,
        path=path,
    )


def ingest_manifest(
    manifest: Manifest,
    cache_dir: str | Path = "data/media",
    *,
    overwrite: bool = False,
) -> list[MediaClip]:
    """Resolve every source in a manifest, in order."""
    return [ingest_source(s, cache_dir, overwrite=overwrite) for s in manifest.sources]
