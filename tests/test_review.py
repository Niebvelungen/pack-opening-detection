"""Tests for the review queue (M6): unresolved, low-confidence, mismatches, under-sampled slots."""

from __future__ import annotations

import json

from pack_config_miner.contracts.observation import IdentifiedCard, PackObservation
from pack_config_miner.contracts.pack_config import ConfidenceReport, SlotConfidence
from pack_config_miner.pipeline.review import build_review_queue, review_queue_to_json


def _card(card_id: str, confidence: float) -> IdentifiedCard:
    return IdentifiedCard(
        cardId=card_id,
        rarity="C",
        assignedSlot="C",
        idMethod="ocr",
        confidence=confidence,
    )


def test_review_queue_collects_all_kinds() -> None:
    obs = [
        PackObservation(
            sourceId="s1",
            setCode="GLD",
            packIndex=0,
            cards=[_card("GLD-001", 0.99), _card("GLD-002", 0.30)],  # second is low-confidence
            unresolved=1,
        )
    ]
    report = ConfidenceReport(
        setCode="GLD",
        packsObserved=1,
        slots={"FOIL": SlotConfidence(samples=8, status="needs_more_samples")},
    )
    items = build_review_queue(obs, ["pack 0: slot C has 0 cards, expected 1"], report)
    kinds = [i.kind for i in items]
    assert "unresolved" in kinds
    assert "low_confidence" in kinds
    assert "template_mismatch" in kinds
    assert "under_sampled" in kinds


def test_review_queue_clean_run_is_empty() -> None:
    obs = [
        PackObservation(
            sourceId="s1", setCode="GLD", packIndex=0, cards=[_card("GLD-001", 0.99)], unresolved=0
        )
    ]
    report = ConfidenceReport(
        setCode="GLD", packsObserved=1, slots={"BS": SlotConfidence(samples=200, status="ok")}
    )
    assert build_review_queue(obs, [], report) == []


def test_review_queue_json_round_trips() -> None:
    obs = [PackObservation(sourceId="s1", setCode="GLD", packIndex=2, unresolved=3)]
    report = ConfidenceReport(setCode="GLD", packsObserved=1)
    items = build_review_queue(obs, [], report)
    parsed = json.loads(review_queue_to_json(items))
    assert parsed[0]["kind"] == "unresolved"
    assert parsed[0]["packIndex"] == 2
