"""Tests for stage [4] resolve: detection -> cardId via exact id, fuzzy name, or unresolved."""

from __future__ import annotations

from pathlib import Path

import pytest

from pack_config_miner.contracts.catalog import CatalogIndex
from pack_config_miner.pipeline.identify.base import BBox, Detection
from pack_config_miner.pipeline.index import build_index, load_catalog
from pack_config_miner.pipeline.resolve import (
    ResolvedDetection,
    base_card_id,
    dedupe_consecutive,
    resolve_detection,
    resolve_detections,
)

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_CATALOG = FIXTURES / "cards_sample.json"


@pytest.fixture(scope="module")
def index() -> CatalogIndex:
    return build_index(load_catalog(SAMPLE_CATALOG))


def _det(**kw: object) -> Detection:
    base: dict = {"sourceId": "s1", "frameOrdinal": 0}
    base.update(kw)
    return Detection(**base)  # type: ignore[arg-type]


def test_exact_id_hit_is_ocr(index: CatalogIndex) -> None:
    det = _det(cardId="CMF-003", isFoil=True, confidence=0.9, bbox=BBox(0, 0, 1, 1))
    res = resolve_detection(det, index, "CMF")
    assert res.resolved
    assert res.cardId == "CMF-003"
    assert res.idMethod == "ocr"
    assert res.matchScore is None
    # Metadata attached from the index; original detection preserved for later stages.
    assert res.rarity == "SR"
    assert res.types == ["Ruler"]
    assert res.races == ["Human"]
    assert res.detection.isFoil is True


def test_exact_id_hit_honours_embedding_method(index: CatalogIndex) -> None:
    # A Tier 2 (local CV) detection stamps idMethod="embedding"; resolve must preserve it.
    det = _det(cardId="CMF-001", idMethod="embedding", confidence=0.8)
    res = resolve_detection(det, index, "CMF")
    assert res.cardId == "CMF-001"
    assert res.idMethod == "embedding"


def test_fuzzy_name_hit_when_id_absent(index: CatalogIndex) -> None:
    det = _det(name="Wonder Stoen")  # transposed typo of "Wonder Stone" (CMF-002)
    res = resolve_detection(det, index, "CMF")
    assert res.resolved
    assert res.cardId == "CMF-002"
    assert res.idMethod == "name"
    assert res.matchScore is not None and res.matchScore >= 85.0
    assert res.rarity == "C"


def test_misread_id_falls_through_to_name(index: CatalogIndex) -> None:
    # Printed id wasn't a real catalog id, but the name is legible -> name match rescues it.
    det = _det(cardId="CMF-999", name="Aesop, the Prince's Tutor")
    res = resolve_detection(det, index, "CMF")
    assert res.cardId == "CMF-001"
    assert res.idMethod == "name"


def test_unresolved_when_nothing_matches(index: CatalogIndex) -> None:
    det = _det(name="Completely Unrelated Title")
    res = resolve_detection(det, index, "CMF")
    assert not res.resolved
    assert res.cardId is None
    assert res.idMethod is None
    assert res.rarity is None
    assert res.types == [] and res.races == []


def test_fuzzy_match_constrained_to_source_set(index: CatalogIndex) -> None:
    # A valid CMF name, but the source is a different set -> no candidates -> unresolved.
    det = _det(name="Wonder Stone")
    res = resolve_detection(det, index, "TTT")
    assert not res.resolved


def test_score_cutoff_rejects_weak_matches(index: CatalogIndex) -> None:
    det = _det(name="Wonder Stoen")
    # The typo resolves at the default cutoff but not when we demand a perfect score.
    assert resolve_detection(det, index, "CMF", score_cutoff=100.0).cardId is None
    assert resolve_detection(det, index, "CMF").cardId == "CMF-002"


def test_resolve_detections_batch_and_unresolved_rate(index: CatalogIndex) -> None:
    dets = [
        _det(cardId="CMF-001"),  # exact
        _det(name="Litle Red, the Pure Stone"),  # fuzzy -> CMF-003
        _det(name="Nonexistent Card"),  # unresolved
        _det(cardId=None, name=None),  # unresolved (nothing to go on)
    ]
    results, stats = resolve_detections(dets, index, "CMF")
    assert [r.cardId for r in results] == ["CMF-001", "CMF-003", None, None]
    assert stats.total == 4
    assert stats.resolved == 2
    assert stats.unresolved == 2
    assert stats.unresolved_rate == 0.5


def test_resolve_stats_empty_is_zero_rate(index: CatalogIndex) -> None:
    _results, stats = resolve_detections([], index, "CMF")
    assert stats.total == 0
    assert stats.unresolved_rate == 0.0


def test_base_card_id_strips_face_and_art_suffixes() -> None:
    assert base_card_id("JRV-062J") == "JRV-062"  # J-Ruler back of a Ruler
    assert base_card_id("CMF-001^") == "CMF-001"  # alt art
    assert base_card_id("CMF-001") == "CMF-001"


def _rd(card_id: str | None, *, conf: float = 0.9, foil: bool = False) -> ResolvedDetection:
    det = Detection(sourceId="s1", frameOrdinal=0, cardId=card_id, isFoil=foil, confidence=conf)
    if card_id is None:
        return ResolvedDetection(detection=det)
    return ResolvedDetection(detection=det, cardId=card_id, rarity="R", idMethod="embedding")


def test_dedupe_collapses_lingering_and_ruler_faces() -> None:
    seq = [
        _rd("JRV-001"),
        _rd("JRV-001", conf=0.95),  # same card lingering across frames -> keep higher conf
        _rd("JRV-062"),  # Ruler front
        _rd("JRV-062J"),  # ...its J-Ruler back -> same physical card, collapse
        _rd("JRV-003"),
    ]
    out = dedupe_consecutive(seq)
    assert [r.cardId for r in out] == ["JRV-001", "JRV-062", "JRV-003"]
    assert out[0].detection.confidence == 0.95  # kept the higher-confidence representative


def test_dedupe_keeps_rare_and_its_foil_separate() -> None:
    # Same id but one is the rare and one is the foil -> distinct (foil is part of the key).
    out = dedupe_consecutive([_rd("JRV-020", foil=False), _rd("JRV-020", foil=True)])
    assert len(out) == 2


def test_dedupe_unresolved_breaks_runs() -> None:
    out = dedupe_consecutive([_rd("JRV-001"), _rd(None), _rd("JRV-001")])
    assert [r.cardId for r in out] == ["JRV-001", None, "JRV-001"]
