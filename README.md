# Pack-Config Miner

A standalone pipeline that watches trading-card **pack-opening footage**, identifies every
card pulled, and **derives a probabilistic pack configuration** — the per-slot rarity/type
distribution for each set — automatically replacing manual tallying.

The project is self-contained and coupled to **nothing but its data contracts**:

- **In:** a *Card Catalog* (`cards.json`) + *opening footage* + a small per-set *Pack Template*.
- **Out:** a *Pack Configuration* JSON per set, plus a *Confidence Report*.

See [plan.md](plan.md) for the full design rationale and [implementation-plan.md](implementation-plan.md)
for the sequenced build steps.

## Why it works

This is the *easy* regime of card recognition:

1. **Closed-set identification** — every card is in the catalog (retrieval, not open-world recognition).
2. **Printed IDs** — each card prints a collector ID (`SET-NUMBER`, e.g. `CMF-001`); OCR yields exact matches.
3. **Free metadata** — once the ID is known, rarity/type/race come straight from the catalog.

The decisive accuracy lever is **capture control**: cards filmed flat under steady light
identify at ~100%; uncontrolled footage mainly contributes aggregate frequencies.

## Pipeline

```
Catalog ─► [0] Load & index ─► CatalogIndex
Manifest ─► [1] Ingest ─► [2] Frame sampling ─► [3] Identify (vision-LLM/OCR)
        ─► [4] Resolve ─► [5] Pack grouping ─► [6] Slot attribution ─► [7] Aggregate & emit
                                                  ─► Pack Configuration + Confidence Report
```

Two tiers: **Tier 1** (vision-LLM + OCR) ships first; **Tier 2** (local CV: detector +
embedding match) is built only for sources where Tier 1 recall is insufficient.

## Status

Early scaffolding. The first goal is a **vertical slice** (milestones M0→M5) that proves the
whole data contract end-to-end on one controlled set. See
[implementation-plan.md](implementation-plan.md) for current progress.

## Quick start

> Not yet runnable — scaffolding in progress. The intended workflow:

```bash
# 1. Install (editable, with dev extras)
pip install -e ".[dev]"

# 2. Mine one set end-to-end
pack-miner run --catalog data/cards.json --manifest data/manifest.json \
               --template templates/TTT.json --out out/

# 3. Inspect the derived config + confidence report
cat out/TTT.config.json
cat out/TTT.confidence.json
```

## Tech stack

Python 3.12+ · `pydantic` v2 (contracts) · `yt-dlp` + `ffmpeg`/`PyAV` (ingest/frames) ·
provider-agnostic vision-LLM client (Tier 1) · `rapidfuzz` (fuzzy match) ·
`opencv-python` + CLIP/DINOv2 + `faiss` (Tier 2) · `typer` (CLI) · `pytest` (tests).

## License

TBD.
