"""Tests for stage [1] ingest -- manifest loading and local/remote source resolution.

No network: remote download is not exercised here (that needs yt-dlp + a live URL); these cover
manifest parsing, the remote/local split, and local passthrough including the missing-file path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pack_config_miner.contracts.manifest import FootageSource, Manifest
from pack_config_miner.pipeline.ingest import (
    MediaClip,
    ingest_manifest,
    ingest_source,
    is_remote,
    load_manifest,
)


def test_is_remote_distinguishes_url_from_path() -> None:
    assert is_remote("https://youtu.be/abc")
    assert is_remote("http://example.com/clip.mp4")
    assert not is_remote(r"C:\videos\clip.mp4")
    assert not is_remote("data/media/clip.mp4")


def test_load_manifest_round_trips(tmp_path: Path) -> None:
    src = tmp_path / "manifest.json"
    src.write_text(
        '{"sources": [{"id": "s1", "setCode": "CMF", "capture": "controlled", "uri": "clip.mp4"}]}',
        encoding="utf-8",
    )
    manifest = load_manifest(src)
    assert isinstance(manifest, Manifest)
    assert manifest.sources[0].id == "s1"
    assert manifest.sources[0].capture == "controlled"


def test_ingest_local_source_passthrough(tmp_path: Path) -> None:
    clip_file = tmp_path / "clip.mp4"
    clip_file.write_bytes(b"not really a video")
    source = FootageSource(id="local1", setCode="CMF", capture="controlled", uri=str(clip_file))
    clip = ingest_source(source, tmp_path / "cache")
    assert clip == MediaClip(
        source_id="local1", set_code="CMF", capture="controlled", path=clip_file
    )


def test_ingest_missing_local_file_raises(tmp_path: Path) -> None:
    source = FootageSource(
        id="gone", setCode="CMF", capture="controlled", uri=str(tmp_path / "nope.mp4")
    )
    with pytest.raises(FileNotFoundError):
        ingest_source(source, tmp_path / "cache")


def test_ingest_manifest_resolves_all_sources(tmp_path: Path) -> None:
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    manifest = Manifest(
        sources=[
            FootageSource(id="a", setCode="CMF", capture="controlled", uri=str(a)),
            FootageSource(id="b", setCode="TTT", capture="uncontrolled", uri=str(b)),
        ]
    )
    clips = ingest_manifest(manifest, tmp_path / "cache")
    assert [c.source_id for c in clips] == ["a", "b"]
    assert [c.set_code for c in clips] == ["CMF", "TTT"]
