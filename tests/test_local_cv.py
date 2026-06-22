"""Tests for stage [3] Tier 2 (local CV art match).

Uses synthetic textured "cards" (random shapes -> ORB features) so the matcher can be exercised
deterministically without real art. Guarded with ``importorskip`` so the suite stays green without
the ``cv`` extra installed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")
pytest.importorskip("faiss")
np = pytest.importorskip("numpy")

from pack_config_miner.pipeline.frames import CandidateFrame  # noqa: E402
from pack_config_miner.pipeline.identify.base import Detection, Identifier  # noqa: E402
from pack_config_miner.pipeline.identify.local_cv import (  # noqa: E402
    LocalCVIdentifier,
    build_art_index,
    card_descriptors,
    detect_card_regions,
    is_monochrome,
    load_art_index,
    match_region,
    save_art_index,
)


def _make_card(seed: int, w: int = 320, h: int = 446):
    """A deterministic, feature-rich synthetic card image."""
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 60, (h, w, 3), dtype=np.uint8)
    for _ in range(45):
        p1 = (int(rng.integers(0, w)), int(rng.integers(0, h)))
        p2 = (int(rng.integers(0, w)), int(rng.integers(0, h)))
        color = tuple(int(c) for c in rng.integers(90, 256, 3))
        cv2.rectangle(img, p1, p2, color, thickness=int(rng.integers(1, 4)))
        cv2.circle(img, p1, int(rng.integers(4, 22)), color, -1)
    return img


def _warp(img, seed: int = 7):
    """A mild perspective warp + noise to mimic a card seen off-angle in footage."""
    h, w = img.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[9, 6], [w - 11, 4], [w - 7, h - 9], [5, h - 5]])
    out = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (w, h))
    rng = np.random.default_rng(seed)
    noise = rng.integers(-12, 12, out.shape).astype(np.int16)
    return np.clip(out.astype(np.int16) + noise, 0, 255).astype(np.uint8)


@pytest.fixture
def art_dir(tmp_path: Path) -> Path:
    """Two distinct synthetic cards written to disk with a fetch-art-style manifest."""
    root = tmp_path / "art"
    root.mkdir()
    files = {}
    for i, cid in enumerate(["TST-001", "TST-002"], start=1):
        fn = f"{cid}.png"
        cv2.imwrite(str(root / fn), _make_card(seed=i))
        files[cid] = fn
    (root / "manifest.json").write_text(json.dumps(files), encoding="utf-8")
    return root


def test_card_descriptors_none_on_blank() -> None:
    blank = np.zeros((446, 320, 3), dtype=np.uint8)
    assert card_descriptors(blank) is None


def test_build_art_index_counts(art_dir: Path) -> None:
    idx = build_art_index(art_dir, "TST")
    assert idx.card_ids == ["TST-001", "TST-002"]
    assert idx.index.ntotal == len(idx.labels) > 0
    assert set(idx.labels) == {"TST-001", "TST-002"}


def test_match_region_identifies_warped_card(art_dir: Path) -> None:
    idx = build_art_index(art_dir, "TST")
    match = match_region(_warp(_make_card(seed=1)), idx)
    assert match is not None
    card_id, conf = match
    assert card_id == "TST-001"  # the warped query matches its own art, not the other card
    assert 0.0 < conf <= 1.0


def test_match_region_rejects_unrelated(art_dir: Path) -> None:
    idx = build_art_index(art_dir, "TST")
    # A high vote threshold no unrelated image can clear -> no false positive.
    assert match_region(_make_card(seed=999), idx, min_votes=10_000) is None


def test_detect_card_regions_finds_a_quad() -> None:
    frame = np.full((600, 800, 3), 20, dtype=np.uint8)
    cv2.rectangle(frame, (250, 150), (550, 450), (240, 240, 240), -1)  # a bright card-like quad
    regions = detect_card_regions(frame)
    assert len(regions) >= 1
    warped, bbox = regions[0]
    assert warped.shape[0] > 0 and 0.0 <= bbox.x <= 1.0


def test_detect_card_regions_whole_frame_fallback() -> None:
    blank = np.zeros((100, 100, 3), dtype=np.uint8)
    regions = detect_card_regions(blank)
    assert len(regions) == 1  # falls back to the whole frame


def test_save_and_load_round_trip(art_dir: Path, tmp_path: Path) -> None:
    idx = build_art_index(art_dir, "TST")
    save_art_index(idx, tmp_path / "idx")
    loaded = load_art_index(tmp_path / "idx")
    assert loaded.card_ids == idx.card_ids
    assert loaded.labels == idx.labels
    assert match_region(_warp(_make_card(seed=2)), loaded)[0] == "TST-002"


def test_identifier_emits_embedding_detection(art_dir: Path, tmp_path: Path) -> None:
    idx = build_art_index(art_dir, "TST")
    img = tmp_path / "frame.png"
    cv2.imwrite(str(img), _warp(_make_card(seed=1)))

    identifier = LocalCVIdentifier(idx, detect=False)  # whole image = one card
    assert isinstance(identifier, Identifier)
    dets = identifier.identify(
        CandidateFrame(source_id="s1", ordinal=2, frame_index=0, timestamp=0.0, path=img)
    )
    assert len(dets) == 1
    d = dets[0]
    assert isinstance(d, Detection)
    assert d.cardId == "TST-001"
    assert d.idMethod == "embedding"
    assert d.sourceId == "s1" and d.frameOrdinal == 2
    assert d.godPack is False


def test_is_monochrome_distinguishes_grey_from_colour() -> None:
    colour = np.zeros((60, 60, 3), dtype=np.uint8)
    colour[:] = (200, 30, 30)  # a saturated blue-ish card
    assert not is_monochrome(colour)
    grey = np.full((60, 60, 3), 120, dtype=np.uint8)  # flat grey == monochrome
    assert is_monochrome(grey)


def test_god_pack_detection_on_monochrome_unmatched(art_dir: Path, tmp_path: Path) -> None:
    idx = build_art_index(art_dir, "TST")
    # A monochrome card not in the index (a god-pack card): grey, so no art match -> godPack.
    god = np.full((446, 320, 3), 100, dtype=np.uint8)
    cv2.rectangle(god, (40, 40), (220, 380), (160, 160, 160), -1)
    img = tmp_path / "god.png"
    cv2.imwrite(str(img), god)

    dets = LocalCVIdentifier(idx, detect=False).identify(
        CandidateFrame(source_id="s1", ordinal=5, frame_index=0, timestamp=0.0, path=img)
    )
    assert len(dets) == 1
    assert dets[0].godPack is True
    assert dets[0].cardId is None


def test_god_packs_disabled(art_dir: Path, tmp_path: Path) -> None:
    idx = build_art_index(art_dir, "TST")
    god = np.full((446, 320, 3), 100, dtype=np.uint8)
    img = tmp_path / "god.png"
    cv2.imwrite(str(img), god)
    dets = LocalCVIdentifier(idx, detect=False, god_packs=False).identify(
        CandidateFrame(source_id="s1", ordinal=0, frame_index=0, timestamp=0.0, path=img)
    )
    assert dets == []  # monochrome no-match is simply dropped when god-pack detection is off
