"""Golden end-to-end regression (M5): catalog + canned detections + template -> exact config.json.

This is the regression anchor for the output contract. Detections are generated deterministically
for 8 controlled packs; the resolved -> grouped -> attributed -> aggregated config must match the
committed golden byte-for-byte (compared as parsed JSON), and round-trip through the contract.
"""

from __future__ import annotations

import json
from pathlib import Path

from pack_config_miner.contracts.pack_config import PackConfig
from pack_config_miner.contracts.template import PackTemplate
from pack_config_miner.pipeline.identify.base import Detection
from pack_config_miner.pipeline.index import build_index, load_catalog
from pack_config_miner.pipeline.run import build_outputs, write_outputs

GOLDEN = Path(__file__).parent / "fixtures" / "golden"

# (R-SR-MR card, FOIL card) per pack. R-SR-MR: MR x1, SR x2, R x5. FOIL: R-Ruler x1, R x3, SR/MR x2.
PACKS = [
    ("GLD-023", "GLD-021"),
    ("GLD-022", "GLD-020"),
    ("GLD-022", "GLD-020"),
    ("GLD-020", "GLD-020"),
    ("GLD-020", "GLD-022"),
    ("GLD-020", "GLD-022"),
    ("GLD-020", "GLD-023"),
    ("GLD-020", "GLD-023"),
]
_COMMONS = ["GLD-001", "GLD-002", "GLD-003", "GLD-001", "GLD-002", "GLD-003"]


def _detections() -> list[Detection]:
    dets: list[Detection] = []
    for frame, (rsrmr, foil) in enumerate(PACKS):
        for cid in [*_COMMONS, "GLD-010", rsrmr]:  # 6 commons + magic stone + R/SR/MR (non-foil)
            dets.append(
                Detection(
                    sourceId="gld-box",
                    frameOrdinal=frame,
                    cardId=cid,
                    isFoil=False,
                    confidence=0.95,
                )
            )
        dets.append(
            Detection(
                sourceId="gld-box", frameOrdinal=frame, cardId=foil, isFoil=True, confidence=0.95
            )
        )
    return dets


def _outputs():  # type: ignore[no-untyped-def]
    index = build_index(load_catalog(GOLDEN / "catalog.json"))
    template = PackTemplate.model_validate_json((GOLDEN / "template.json").read_text("utf-8"))
    return build_outputs(_detections(), index, template, capture="controlled")


def test_golden_config_matches() -> None:
    config_dict = _outputs().config.to_config_dict()
    expected = json.loads((GOLDEN / "config.json").read_text("utf-8"))
    assert config_dict == expected


def test_golden_config_round_trips() -> None:
    config_dict = _outputs().config.to_config_dict()
    assert PackConfig.from_config_dict(config_dict).to_config_dict() == config_dict


def test_every_lottery_slot_sums_to_100() -> None:
    config = _outputs().config
    for outcomes in config.lottery.values():
        assert sum(o.chance for o in outcomes) == 100


def test_golden_run_stats_and_review() -> None:
    out = _outputs()
    assert out.report.setCode == "GLD"
    assert out.report.packsObserved == 8
    assert out.resolve_stats is not None and out.resolve_stats.unresolved_rate == 0.0
    # 8 packs is thin, so every lottery slot lands in the review queue.
    kinds = {item.kind for item in out.review}
    assert "under_sampled" in kinds


def test_god_pack_detections_split_out_and_reported() -> None:
    index = build_index(load_catalog(GOLDEN / "catalog.json"))
    template = PackTemplate.model_validate_json((GOLDEN / "template.json").read_text("utf-8"))
    dets = [*_detections(), Detection(sourceId="gld-box", frameOrdinal=99, godPack=True)]
    out = build_outputs(dets, index, template, capture="controlled")
    assert out.god_pack_cards == 1
    assert any(item.kind == "god_pack" for item in out.review)
    # The god-pack card is split off before resolve, so it doesn't inflate the unresolved rate.
    assert out.resolve_stats is not None and out.resolve_stats.unresolved_rate == 0.0
    assert any("god pack" in f.lower() for f in out.report.flags)


def test_write_outputs_persists_three_files(tmp_path: Path) -> None:
    paths = write_outputs(_outputs(), tmp_path)
    assert {p.name for p in paths.values()} == {"config.json", "report.json", "review.json"}
    on_disk = json.loads(paths["config"].read_text("utf-8"))
    assert on_disk == json.loads((GOLDEN / "config.json").read_text("utf-8"))
