"""Tests for the art-assets module (no network: only loading + selection + filename logic)."""

from __future__ import annotations

from pathlib import Path

from pack_config_miner.assets import _safe_filename, load_image_cache, select_ids
from pack_config_miner.pipeline.index import build_index, load_catalog

FIXTURES = Path(__file__).parent / "fixtures"
IMAGE_CACHE = FIXTURES / "image_cache_sample.json"
SAMPLE_CATALOG = FIXTURES / "cards_sample.json"


def test_load_image_cache_from_file() -> None:
    cache = load_image_cache(IMAGE_CACHE)
    assert cache["CMF-001"].endswith("/CMF-001.jpg")
    assert "CMF-013J" in cache


def test_select_ids_by_set_and_limit() -> None:
    cache = load_image_cache(IMAGE_CACHE)
    cmf = select_ids(cache, set_code="CMF")
    assert cmf == ["CMF-001", "CMF-002", "CMF-003", "CMF-013J"]  # TTT excluded, order preserved
    assert select_ids(cache, set_code="CMF", limit=2) == ["CMF-001", "CMF-002"]


def test_select_ids_explicit_filters_unknown() -> None:
    cache = load_image_cache(IMAGE_CACHE)
    assert select_ids(cache, ids=["TTT-118", "NOPE-999"]) == ["TTT-118"]


def test_safe_filename_sanitizes_and_keeps_extension() -> None:
    assert _safe_filename("CMF-001", "https://x/CMF-001.jpg") == "CMF-001.jpg"
    # Illegal filesystem chars are replaced; extension comes from the URL.
    assert _safe_filename("AO1*Buy", "https://x/AO1%20Buy.png") == "AO1_Buy.png"


def test_build_index_fills_image_url_when_cache_given() -> None:
    catalog = load_catalog(SAMPLE_CATALOG)
    cache = load_image_cache(IMAGE_CACHE)
    index = build_index(catalog, image_cache=cache)
    assert index.byId["CMF-001"].imageUrl == cache["CMF-001"]
    # No cache -> imageUrl stays None.
    assert build_index(catalog).byId["CMF-001"].imageUrl is None
