"""M0 tests: contract round-trips, the ATK/DEF int|"" quirk, and catalog index shape."""

from __future__ import annotations

from pathlib import Path

from pack_config_miner.contracts.catalog import CatalogCard
from pack_config_miner.contracts.pack_config import PackConfig
from pack_config_miner.contracts.template import PackTemplate
from pack_config_miner.pipeline.index import build_index, load_catalog, load_index

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_CATALOG = FIXTURES / "cards_sample.json"


# --- ATK/DEF int | "" quirk (plan.md section 3.1) ---------------------------


def test_atk_def_accepts_int() -> None:
    card = CatalogCard(id="X-1", name="n", rarity="C", ATK=500, DEF=500)
    assert card.atk == 500
    assert card.def_ == 500


def test_atk_def_accepts_empty_string_as_none() -> None:
    card = CatalogCard.model_validate(
        {"id": "X-2", "name": "n", "rarity": "C", "ATK": "", "DEF": ""}
    )
    assert card.atk is None
    assert card.def_ is None


def test_card_passthrough_fields_preserved() -> None:
    card = CatalogCard.model_validate(
        {"id": "X-3", "name": "n", "rarity": "C", "cost": "{W}{1}", "flavour": "hi"}
    )
    dumped = card.model_dump(by_alias=True)
    assert dumped["cost"] == "{W}{1}"
    assert dumped["flavour"] == "hi"


# --- Catalog index (stage [0]) ---------------------------------------------


def test_load_and_index_shape() -> None:
    index = load_index(SAMPLE_CATALOG)

    assert set(index.byId) == {"CMF-001", "CMF-002", "CMF-003"}
    assert index.bySet["CMF"] == ["CMF-001", "CMF-002", "CMF-003"]
    assert index.raritiesBySet["CMF"] == {"U": 1, "C": 1, "SR": 1}
    assert index.typesBySet["CMF"] == ["Resonator", "Magic Stone", "Ruler"]

    entry = index.byId["CMF-001"]
    assert entry.setCode == "CMF"
    assert entry.rarity == "U"
    assert entry.types == ["Resonator"]
    assert entry.races == ["Human"]
    assert entry.colours == ["W"]


def test_index_handles_blank_atk_card() -> None:
    catalog = load_catalog(SAMPLE_CATALOG)
    index = build_index(catalog)
    # CMF-002 had ATK/DEF "" -- still indexed fine.
    assert index.byId["CMF-002"].types == ["Magic Stone"]


# --- PackTemplate (plan.md section 3.3) -------------------------------------


def test_template_slot_partitioning() -> None:
    template = PackTemplate.model_validate(
        {
            "setCode": "TTT",
            "packSize": 9,
            "layout": [
                {"slot": "C", "kind": "fixed", "count": 6},
                {"slot": "BS", "kind": "lottery", "count": 1},
                {"slot": "R-SR-MR", "kind": "lottery", "count": 1},
                {"slot": "FOIL", "kind": "lottery", "count": 1},
            ],
            "attribution": {
                "rules": [
                    {"slot": "FOIL", "match": {"isFoil": True}},
                    {"slot": "BS", "match": {"anyType": ["Magic Stone"]}},
                    {"slot": "R-SR-MR", "match": {"rarityIn": ["R", "SR", "MR"]}},
                ]
            },
        }
    )
    assert [s.slot for s in template.fixed_slots()] == ["C"]
    assert [s.slot for s in template.lottery_slots()] == ["BS", "R-SR-MR", "FOIL"]
    assert template.attribution.rules[0].match.isFoil is True


# --- PackConfig on-disk shape + the rarity:slotName quirk (section 3.5) ------


def test_pack_config_round_trip_preserves_quirk() -> None:
    on_disk = {
        "packImage": "",
        "slots": ["C", "C", "BS", "R-SR-MR", "FOIL"],
        "excludes": [{"rarity": "R-SR-MR", "type": ["Token"]}],
        "set_override": [{"rarity": "BS", "setCodes": ["CMF"]}],
        "BS": [
            {"chance": 100, "rarity": "C", "conditions": [{"equals": True, "type": "Magic Stone"}]}
        ],
        "R-SR-MR": [
            {"chance": 5, "rarity": "MR"},
            {"chance": 28, "rarity": "SR"},
            {"chance": 67, "rarity": "R", "conditions": [{"equals": False, "type": "Ruler"}]},
        ],
        "FOIL": [{"chance": 100, "rarity": "N"}],
    }

    config = PackConfig.from_config_dict(on_disk)

    # Lottery slots are not reserved fields.
    assert set(config.lottery) == {"BS", "R-SR-MR", "FOIL"}
    # Quirk: excludes/set_override keyed by "rarity" holding the SLOT NAME.
    assert config.excludes[0].rarity == "R-SR-MR"
    assert config.set_override[0].rarity == "BS"

    # Round-trips back to the exact on-disk shape.
    assert config.to_config_dict() == on_disk


def test_pack_config_omits_optional_condition_fields() -> None:
    config = PackConfig.from_config_dict({"slots": ["X"], "X": [{"chance": 100, "rarity": "R"}]})
    dumped = config.to_config_dict()
    # No conditions key when absent; no null leakage.
    assert dumped["X"] == [{"chance": 100, "rarity": "R"}]
    assert "excludes" not in dumped
    assert "set_override" not in dumped
