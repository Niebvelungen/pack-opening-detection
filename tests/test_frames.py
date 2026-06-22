"""Tests for stage [2] frame sampling.

The keyframe *selection* logic is pure and tested directly on synthetic signatures (no decoder
needed). The numpy/PyAV/Pillow paths are guarded with ``importorskip`` so the suite stays green
without the ``media`` extra installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pack_config_miner.pipeline.frames import (
    frame_signature,
    sample_keyframes,
    select_keyframe_indices,
    signature_distance,
)
from pack_config_miner.pipeline.ingest import MediaClip


def test_signature_distance_mean_abs() -> None:
    assert signature_distance([0, 0, 0], [0, 0, 0]) == 0.0
    assert signature_distance([0, 0], [10, 20]) == 15.0
    assert signature_distance([], []) == 0.0


def test_signature_distance_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="same length"):
        signature_distance([1, 2], [1, 2, 3])


def test_select_empty_and_single() -> None:
    assert select_keyframe_indices([]) == []
    assert select_keyframe_indices([(0, 0)]) == [0]


def test_select_keeps_first_and_scene_changes() -> None:
    # Three "scenes": frames 0-1 dark, 2-3 mid, 4-5 bright. Near-duplicates are dropped.
    dark, mid, bright = (0, 0), (50, 50), (200, 200)
    sigs = [dark, dark, mid, mid, bright, bright]
    # First is baseline; each new scene's first frame crosses the threshold.
    assert select_keyframe_indices(sigs, threshold=20.0) == [0, 2, 4]


def test_select_threshold_controls_density() -> None:
    sigs = [(0,), (10,), (20,), (30,), (40,)]
    # Low threshold keeps every step; high threshold keeps only big jumps from the last kept.
    assert select_keyframe_indices(sigs, threshold=5.0) == [0, 1, 2, 3, 4]
    assert select_keyframe_indices(sigs, threshold=25.0) == [0, 3]


def test_select_min_gap_rate_limits() -> None:
    sigs = [(0,), (100,), (200,), (300,)]
    # min_gap=2 forbids selecting a frame within 2 of the last kept (index 0 always kept).
    assert select_keyframe_indices(sigs, threshold=10.0, min_gap=2) == [0, 2]


def test_frame_signature_downscales_rgb() -> None:
    np = pytest.importorskip("numpy")
    # A solid mid-gray 32x32 RGB frame -> every cell of the fingerprint is ~128.
    frame = np.full((32, 32, 3), 128, dtype=np.uint8)
    sig = frame_signature(frame, size=4)
    assert len(sig) == 16
    assert all(v == 128 for v in sig)


def test_frame_signature_splits_bright_dark_halves() -> None:
    np = pytest.importorskip("numpy")
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    frame[:, 4:, :] = 255  # right half white
    sig = frame_signature(frame, size=2)  # 2x2 grid: left cells dark, right cells bright
    assert sig[0] == 0 and sig[2] == 0  # left column cells
    assert sig[1] == 255 and sig[3] == 255  # right column cells


def _write_synthetic_clip(path: Path, scenes: list[tuple[int, int, int]], *, per_scene: int = 5):
    """Encode a tiny clip: each colour in ``scenes`` is held for ``per_scene`` frames."""
    av = pytest.importorskip("av")
    np = pytest.importorskip("numpy")
    container = av.open(str(path), mode="w")
    stream = container.add_stream("mpeg4", rate=5)
    stream.width, stream.height = 64, 64
    stream.pix_fmt = "yuv420p"
    for colour in scenes:
        arr = np.empty((64, 64, 3), dtype=np.uint8)
        arr[:] = colour
        for _ in range(per_scene):
            frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def test_sample_keyframes_end_to_end(tmp_path: Path) -> None:
    pytest.importorskip("av")
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")

    scenes = [(0, 0, 0), (128, 128, 128), (255, 255, 255)]  # three distinct "reveal" scenes
    clip_path = tmp_path / "syn.mp4"
    _write_synthetic_clip(clip_path, scenes)

    clip = MediaClip(source_id="syn", set_code="CMF", capture="controlled", path=clip_path)
    out_dir = tmp_path / "frames"
    frames = sample_keyframes(clip, out_dir, threshold=30.0)

    # One keyframe per scene; near-duplicate frames within a scene are deduped.
    assert len(frames) == len(scenes)
    assert [f.ordinal for f in frames] == [0, 1, 2]
    for f in frames:
        assert f.path is not None and f.path.exists()
