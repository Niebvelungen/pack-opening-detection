# Implementation Plan

Concrete, sequenced build steps derived from [plan.md](plan.md) §10 milestones. The strategy is
a **vertical slice first**: get M0→M5 working end-to-end on *one controlled set* before adding
breadth (Tier 2 CV, uncontrolled-footage robustness).

Legend: `[ ]` todo · `[~]` in progress · `[x]` done.

---

## Phase 0 — Project scaffolding  ✳ *do first*  ✅ DONE

- [x] `README.md`, `CLAUDE.md`, `.gitignore`
- [x] `pyproject.toml` — package `pack_config_miner` (hatchling), Python 3.12+, deps split into
      core / `vision` / `media` / `cv` / `all` / `dev` extras. Console script
      `pack-miner = pack_config_miner.cli:app`.
- [x] Source tree under a **`src/` layout** (`src/pack_config_miner/`): the [plan.md](plan.md) §9
      `contracts/` and `pipeline/` (with `identify/`) live under the package namespace so the
      console script resolves; `templates/`, `tests/`, `data/`, `out/` stay at repo root with
      `.gitkeep` (and `.gitignore` keeps the dirs but ignores their contents).
- [x] Dev tooling config (in `pyproject.toml`): `ruff` (lint+format), `mypy` (strict override on
      `contracts/`, `py.typed` marker shipped), `pytest` + `pytest-cov`.
- [x] CI stub: `.github/workflows/ci.yml` runs ruff check + ruff format --check + mypy + pytest.
- [x] Smoke tests (`tests/test_smoke.py`): import + CLI version/help.

**Exit met:** `pip install -e ".[dev]"` succeeds; `pack-miner --help` prints; ruff/mypy/pytest all green.

> Note: built/verified on Python **3.13** locally (satisfies `>=3.12`). Source kept ASCII-only so
> `rich`/`typer` help renders on the Windows cp1252 console.

---

## M0 — Contracts + catalog index  ✅ DONE

Build every §3 schema as a validated `pydantic` v2 model. These are the spine; everything else
depends on them.

- [x] `contracts/catalog.py` — `CatalogCard` (`ATK`/`DEF` accept `int | ""`, normalised to
      `int | None`; `type`/`race`/`colour` are `list[str]`; `extra="allow"` keeps passthrough
      fields), `CatalogSet`, `CatalogCluster`, `CatalogGame`, `Catalog` root, plus `IndexedCard`
      and `CatalogIndex`.
- [x] `contracts/manifest.py` — `FootageSource` (`capture: Literal["controlled","uncontrolled"]`),
      `Manifest`.
- [x] `contracts/template.py` — `SlotDef` (`kind: Literal["fixed","lottery"]`), `MatchPredicate`
      (`isFoil`/`anyType`/`anyRace`/`rarityIn`/`cardIdPrefix`), `AttributionRule`, `PackTemplate`
      (with `lottery_slots()`/`fixed_slots()` helpers).
- [x] `contracts/observation.py` — `IdentifiedCard` (`idMethod: Literal["ocr","name","embedding"]`),
      `PackObservation`.
- [x] `contracts/pack_config.py` — `Condition`, `Outcome`, `ExcludeEntry`, `SetOverrideEntry`,
      `PackConfig` (+ `to_config_dict`/`from_config_dict` for the lottery-slots-as-top-level-keys
      shape), `ConfidenceReport`. **`"rarity": <slotName>` quirk preserved** + round-trip tested.
- [x] `pipeline/index.py` ([0]) — `load_catalog`/`build_index`/`load_index` → `CatalogIndex`
      (`byId`, `bySet`, `raritiesBySet`, `typesBySet`).
- [x] Tests (`tests/test_contracts.py` + `tests/fixtures/cards_sample.json`): `ATK:""` and
      `ATK:500` both parse; index shape; template partitioning; PackConfig quirk round-trip.

**Exit met:** all §3 schemas validate; sample `cards.json` loads to a correct `CatalogIndex`;
ruff + mypy + 11 pytest all green.

---

## M1 — Ingest + frame sampling

- [ ] `pipeline/ingest.py` ([1]) — resolve a `FootageSource`: `yt-dlp` for remote URLs, passthrough
      for local paths; normalise container/fps; cache under `data/`.
- [ ] `pipeline/frames.py` ([2]) — scene-change keyframe extraction (`ffmpeg` CLI or `PyAV`);
      dedupe near-identical frames; emit candidate frames with timestamps.
- [ ] Make the vision/heavy deps optional so M0 tests don't require `ffmpeg`/`yt-dlp` installed.
- [ ] Tests: a short local fixture clip → expected keyframe count (±tolerance).

**Exit:** manifest → keyframes for one controlled clip.

---

## M2 — Vision identify (Tier 1)

> Before writing the Anthropic client, consult the `claude-api` skill for current model IDs,
> the vision API shape, and pricing. Default to the latest Claude model.

- [ ] `pipeline/identify/base.py` — `Detection` dataclass + `Identifier` protocol
      (`identify(frame) -> list[Detection]`). This interface is the Tier 1 ↔ Tier 2 seam.
- [ ] `pipeline/identify/vision_llm.py` — Claude vision client behind `Identifier`.
      Prompt: enumerate every card; return printed `SET-NUMBER` if legible else visible name;
      flag holo/foil; bbox + self-reported confidence. Structured (JSON/tool-use) output.
- [ ] Config: API key via env (`.env`, gitignored); model id configurable; retry/backoff.
- [ ] Tests: a recorded fixture (frame → canned response) so tests don't hit the network.

**Exit:** frames → detections with IDs/foil flags on a sample clip.

---

## M3 — Resolve

- [ ] `pipeline/resolve.py` ([4]) — map each `Detection` → `cardId`:
      (1) exact ID hit in `CatalogIndex.byId`; (2) else `rapidfuzz` name match **constrained to
      the source's `setCode`**; (3) else mark unresolved. Attach rarity/types/races from index.
- [ ] Track and report the unresolved rate.
- [ ] Tests: exact-hit, fuzzy-hit (typo), and unresolved cases.

**Exit:** detections → `cardId` + metadata; unresolved rate measured.

---

## M4 — Group + attribute

- [ ] `pipeline/group.py` ([5]) — `controlled` → one shot = one pack (confidence 1.0);
      `uncontrolled` → group consecutive detections into runs of `packSize` / split on scene
      boundaries; emit `groupingConfidence < 1` and flag.
- [ ] `pipeline/attribute.py` ([6]) — run template `attribution.rules` (first-match-wins) to tag
      each card's `assignedSlot`; leftovers fill fixed slots. Validate per-slot counts vs
      template — **flag mismatches, don't drop**.
- [ ] Tests: a controlled pack → valid `PackObservation`; a count-mismatch pack → flagged.

**Exit:** controlled clip → Pack Observations passing template validation.

---

## M5 — Aggregate + emit  🎯 *vertical slice complete*

- [ ] `pipeline/aggregate.py` ([7]) — tally per-slot outcome **signatures** (§5: rarity + distinguishing
      conditions), fold identical signatures, normalise to integer `chance` via **largest-remainder**
      (sum=100 per slot), emit `PackConfig`.
- [ ] `pipeline/cli.py` — `typer` app wiring [0]→[7]: `pack-miner run --catalog --manifest
      --template --out`.
- [ ] Tests: a **golden** end-to-end fixture (catalog + canned detections + template) → exact
      expected `config.json`. This is the regression anchor.

**Exit:** one set → Pack Configuration written to `out/`.

---

## M6 — Confidence + review

- [ ] Confidence Report (§3.5): per-outcome 95% CI (Wilson, §6), `status` per slot
      (`ok`/`needs_more_samples`/`review`), `flags` with "sample N more packs" hints.
- [ ] Review queue: collect unresolved detections, low-confidence IDs, template-validation
      failures, under-sampled slots into a single reviewable artifact (CSV/JSON).
- [ ] Tests: known counts → expected CI half-widths and status transitions at the threshold.

**Exit:** config emitted **with** confidence report + review queue.

---

## M7 — Tier-2 CV fallback  *(only if Tier 1 recall is insufficient)*

- [ ] `pipeline/identify/local_cv.py` — card-rectangle detection (`opencv`) + embedding match
      (CLIP/DINOv2) against catalog art via `faiss` index + OCR. Same `Identifier` interface.
- [ ] Build/persist the per-set embedding index from catalog art.
- [ ] Tests: messy-frame fixtures where Tier 1 underperforms → improved recall.

**Exit:** local detector+embedder for sources where Tier 1 recall is low.

---

## Cross-cutting

- **Fixtures:** keep a tiny sample catalog, a short clip, canned detection responses, and at
  least one golden config under `tests/fixtures/`. The golden config (M5) guards the output
  contract on every change.
- **Validation everywhere:** every stage boundary parses/serializes through its `pydantic`
  contract.
- **Open questions** ([plan.md](plan.md) §12) to resolve as we hit them: foil detectability,
  non-skeleton pack structures, vision cost vs. sampling density, catalog refresh cadence.

## Suggested execution order

`Phase 0` → `M0` → `M1` → `M2` → `M3` → `M4` → `M5` (slice done) → `M6` → `M7`.
M2 can be stubbed (canned detections) to let M3–M5 proceed before the real vision client lands.
