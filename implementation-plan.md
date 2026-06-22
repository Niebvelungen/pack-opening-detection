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

## M1 — Ingest + frame sampling  ✅ DONE

- [x] `pipeline/ingest.py` ([1]) — `load_manifest`, `is_remote`, `ingest_source`/`ingest_manifest`
      → `MediaClip` (frozen dataclass: source id, set, capture, local path). `yt-dlp` for remote
      URLs (lazy import, cached under `data/media` as `<id>.<ext>`), passthrough + existence check
      for local paths. Container/fps normalisation is deferred to the decoder (PyAV reads any
      container; the sampler controls effective fps), so ingest only lands a readable file.
- [x] `pipeline/frames.py` ([2]) — **pure** `select_keyframe_indices` (scene-change + dedup in one
      pass: keep a frame only when it differs from the last kept frame by ≥ `threshold`, rate-limited
      by `min_gap`) over cheap per-frame `frame_signature` fingerprints; `iter_decoded_frames`
      (PyAV) + `sample_keyframes` orchestrate decode → select → save PNGs, emitting `CandidateFrame`
      records (ordinal, frame index, timestamp, path).
- [x] Heavy deps stay optional: `yt-dlp`/`av`/`numpy`/`pillow` in the `media` extra, all imported
      lazily; M0 core tests need none of them. mypy override ignores their missing stubs.
- [x] CLI: `pack-miner ingest` and `pack-miner sample-frames` wire [1]/[1]+[2] for manual runs.
- [x] Tests (`tests/test_ingest.py`, `tests/test_frames.py`): manifest round-trip, remote/local
      split, local passthrough + missing-file; pure selection logic (first-frame baseline,
      threshold density, `min_gap`, scene-change vs dedup) tested decoder-free; `numpy`/PyAV/Pillow
      paths `importorskip`-guarded, incl. an end-to-end synthetic 3-scene clip → 3 deduped keyframes.

**Exit met:** manifest → keyframes for one (synthetic) controlled clip; ruff + mypy + 30 pytest green.

> Note: this dev box had no Python ≥3.12 (only pyenv 3.7/3.9); installed CPython **3.13** via winget
> and built a `.venv` (gitignored). Suite verified on 3.13 with the `media` extra installed.

---

## M2 — Vision identify (Tier 1)  ✅ DONE

> Consulted the `claude-api` skill before writing the client (per CLAUDE.md): model
> `claude-opus-4-8`, base64 image content blocks, structured output via `output_config.format`,
> SDK-managed retry/backoff, `ANTHROPIC_API_KEY` resolved from env.

- [x] `pipeline/identify/base.py` — `BBox` + `Detection` (frozen dataclasses: `sourceId`,
      `frameOrdinal`, nullable `cardId`/`name`/`isFoil`/`bbox`, `confidence`) + `runtime_checkable`
      `Identifier` protocol (`identify(frame) -> list[Detection]`) — the Tier 1 ↔ Tier 2 seam.
- [x] `pipeline/identify/vision_llm.py` — `VisionLLMIdentifier` behind `Identifier`. Prompt
      enumerates every card → printed `SET-NUMBER` if legible (suffixes preserved) else visible
      name; holo/foil flag; normalised bbox; self-reported confidence. **Structured output**
      (`output_config.format` json_schema, all-required + `additionalProperties:false`, nullable
      via union types). `anthropic` lazy-imported (`vision` extra); refusal `stop_reason` guarded.
- [x] Config: `VisionConfig` (model id, `max_tokens`, `effort`, `api_key`, `max_retries`); API key
      via env / gitignored `.env`; SDK retry/backoff. Network I/O isolated in `identify`; request
      build + response parse are pure functions.
- [x] CLI: `pack-miner identify --image <png> [--source --model --effort]` runs Tier 1 on one frame.
- [x] Tests (`tests/test_identify.py` + `tests/fixtures/vision_response_sample.json`): pure
      `parse_detections`/`build_request`/`encode_image`; **recorded fixture replayed through a fake
      client** (no network, no `vision` extra needed) for `identify` end-to-end; refusal + missing-
      frame errors; `Identifier` protocol conformance.

**Exit met:** frames → detections with ids/foil flags via a swappable identifier; ruff + mypy +
40 pytest green (fixture-driven, offline). Live-clip run is a credentialed manual step (`identify`).

---

## M3 — Resolve  ✅ DONE

- [x] `pipeline/resolve.py` ([4]) — `resolve_detection`/`resolve_detections` map each `Detection`
      → `ResolvedDetection`: (1) exact id hit in `CatalogIndex.byId` (`idMethod="ocr"`); (2) else
      `rapidfuzz` (`fuzz.WRatio` via `process.extractOne`) name match **constrained to the source's
      set** (`index.bySet[set_code]`) at/above `score_cutoff` (default 85, `idMethod="name"`,
      `matchScore` recorded); (3) else unresolved (`cardId=None`, flagged not dropped). A misread id
      that misses falls through to the name path. rarity/types/races attached from the index; the
      original `Detection` is kept for foil/confidence/bbox + source/frame.
- [x] `ResolveStats` (total/resolved/unresolved/`unresolved_rate`) returned alongside the batch —
      the unresolved rate is measured for QA gating (M6).
- [x] Tests (`tests/test_resolve.py`): exact-hit (ocr + metadata), fuzzy-hit (typo → name + score),
      misread-id-falls-through-to-name, unresolved, **set-constraint** (valid name, wrong set →
      unresolved), `score_cutoff` boundary, and batch unresolved-rate (incl. empty = 0.0).

**Exit met:** detections → `cardId` + metadata via a provider-agnostic resolver; unresolved rate
measured; ruff + mypy + 48 pytest green.

---

## M4 — Group + attribute  ✅ DONE

- [x] `pipeline/group.py` ([5]) — `group_packs` → `PackGroup`s. `controlled` → one keyframe = one
      pack (group by `frameOrdinal`, `groupingConfidence` 1.0); `uncontrolled` → consecutive runs
      of `packSize`, `groupingConfidence` 0.6, every pack flagged + partial-run flag.
- [x] `pipeline/attribute.py` ([6]) — `attribute_pack` runs `attribution.rules` first-match-wins
      (`card_matches` predicate: `isFoil`/`anyType`/`anyRace`/`rarityIn`/`cardIdPrefix`, AND); cards
      matching no rule fall to the fixed slot named for their rarity. Validates per-slot counts vs
      template — **mismatches flagged, nothing dropped**. Unresolved detections counted, not
      attributed. Emits a `PackObservation` + flag list.
- [x] Tests (`tests/test_group.py`, `tests/test_attribute.py`): controlled one-pack-per-frame +
      uncontrolled chunking/flags; first-match-wins (foil rule beats `rarityIn`), fixed leftover,
      count-mismatch flagged, unresolved counting, predicate-field matrix.

**Exit met:** controlled detections → Pack Observations with per-slot validation; flags surfaced.

---

## M5 — Aggregate + emit  🎯 *vertical slice complete*  ✅ DONE

- [x] `pipeline/aggregate.py` ([7]) — `aggregate` folds observations into a `PackConfig`. Per-card
      `signature` = rarity + conditions from the slot's `distinguish` types (§5); identical
      signatures fold; vacuous `equals:false` conditions stripped where a rarity never split;
      counts → integer `chance` via **largest-remainder** (deterministic tie-break, sums to 100),
      outcomes emitted most-frequent-first. Carries `SlotTally` forward for confidence. Added an
      optional `distinguish` field to `SlotDef` (contract + §3.3 doc updated together).
- [x] `pipeline/run.py` — `detections_to_observations` / `build_outputs` (the offline [4]→[7] +
      confidence + review tail) and `run_pipeline` ([1]→[7] with the Tier 1 identifier, injectable
      for Tier 2). `pipeline/cli.py` `run --catalog --manifest --template --out` writes
      `config.json` / `report.json` / `review.json`.
- [x] Tests: **golden end-to-end** (`tests/fixtures/golden/` catalog + template + 8 canned packs →
      exact `config.json`, parsed-JSON equal + contract round-trip + every slot sums to 100), plus
      `tests/test_aggregate.py` (signature/largest-remainder units).

**Exit met:** one set → Pack Configuration written to `out/`; golden regression anchors the output.

---

## M6 — Confidence + review  ✅ DONE

- [x] `pipeline/confidence.py` — `build_confidence_report`: per-outcome **Wilson** 95% CI
      half-width (`ci95`, percentage points), per-slot `status` (`ok` / `needs_more_samples` when
      max CI > 5pp / `review` on a singleton outcome), and "sample ~N more packs" flags derived
      from the target margin.
- [x] `pipeline/review.py` — `build_review_queue` collects unresolved detections, low-confidence
      ids, template-validation failures, and under-sampled slots into one `ReviewItem` list
      (`review.json`).
- [x] Tests (`tests/test_confidence.py`, `tests/test_review.py`): Wilson monotonicity + pp scaling,
      the three status transitions at the thresholds, and review-queue assembly across all kinds.

**Exit met:** config emitted **with** confidence report + review queue.

---

## M7 — Tier-2 CV fallback  ✅ DONE  *(Tier 1 recall measured insufficient on uncontrolled footage)*

> **Why triggered:** on a real uncontrolled JRV box-opening, Tier 1 recall was measured at ~21%
> (Opus, honest nulls) and Sonnet hallucinated ids (`JRV-001` everywhere). Tier 2 fixes both.

- [x] `pipeline/identify/local_cv.py` — `LocalCVIdentifier` behind `Identifier`. **ORB** keypoint
      descriptors → **FAISS binary (Hamming) index** over reference art; identify by descriptor
      **voting** (nearest within `max_hamming`, card with most votes ≥ `min_votes` wins, confidence
      scales from the threshold). `detect_card_regions` does opencv contour/quad detection + perspective
      warp, with a whole-frame fallback. Emits `Detection(idMethod="embedding")`; **hallucination is
      structurally impossible** (only indexed ids can be output). Chose ORB+FAISS over CLIP/DINOv2 to
      stay within the `cv` extra — no multi-GB model download. `cv2`/`faiss`/`numpy` lazy-imported.
- [x] `build_art_index` (from the fetch-art manifest, per-set) + `save_art_index`/`load_art_index`
      (FAISS + labels). `Detection.idMethod` added; resolve honours `"embedding"` on exact hits.
- [x] CLI: `build-art-index`, `identify-cv`, and `run --tier cv --art-root` (Tier 1↔Tier 2 is a flag).
- [x] Tests (`tests/test_local_cv.py`, `importorskip`-guarded): synthetic textured cards → index →
      **warped+noised query matches the right id**, unrelated rejected, region detection + fallback,
      save/load round-trip, `embedding` Detection + protocol conformance. Plus a resolve test.

**Exit met (verified on real footage):** Tier 2 resolved **10/10 detections** to real JRV cards on the
pilot frames (vs Tier 1's 21% / hallucinations) and produced a full `config.json` over all 848 frames
in **16 s at $0** — reading cards (`JRV-062J`, `JRV-004`, `JRV-070`) whose printed id Tier 1 couldn't.

### Post-M7: Tier 2 promoted to primary + god-pack handling

- [x] **Tier 2 is the default identifier** (`run --tier cv`, `run_pipeline(tier="cv")`); Tier 1 vision
      is the optional fallback (`--tier vision`). Confirmed JRV pack structure (N cards first, FOIL
      slot last) — the template layout already reflects this.
- [x] **God packs** (domain: a rare all-MR/Ruler/J-Ruler variant whose cards are *monochrome* and
      have *no reference art*, so Tier 2 cannot art-match them). `Detection.godPack` + `is_monochrome`
      / `mean_saturation` in `local_cv.py`: a region that fails the art match but reads monochrome is
      emitted as a god-pack card (`cardId=None`, `godPack=True`) instead of being lost as noise.
      `build_outputs` splits god-pack detections off **before** resolve (so they don't inflate the
      unresolved rate), counts them on `PipelineOutputs.god_pack_cards`, and surfaces a `god_pack`
      review item + a report flag. Tests cover monochrome discrimination, god-pack emission, the
      disable switch, and the pipeline split. *(On the JRV double-box run: 0 god packs in the sampled
      keyframes — rare event, none captured — feature verified via unit tests.)*

> **Open follow-up (§12 "non-skeleton pack structures"):** god packs are reported as a separate
> signal, not yet modeled as a pack-level variant in `PackConfig` (the per-slot shape can't express
> "X% of packs are an entirely different pack"). The natural full fix is a Tier-1-as-god-pack-fallback
> hybrid (vision reads the legible printed id on the monochrome cards) + a pack-variant field.

### Post-M7: hybrid foils + uncontrolled-footage quality (driven by the JRV double-box run)

Measured on a real uncontrolled JRV box-opening (cv-only Tier 2 gave a usable but biased config):

- [x] **Hybrid identifier** (`pipeline/identify/hybrid.py`, `--tier hybrid`) — Tier 2 leads; frames it
      can't match escalate to Tier 1 (defaults to Sonnet/low for cost) which reads name/id **and the
      foil flag**; god-pack frames are skipped. This populated the previously-empty **FOIL slot**
      (was empty → `N`-dominant, plausible). ~115 paid calls over 848 frames ≈ $0.70.
- [x] **Consecutive-dedup** (`resolve.dedupe_consecutive` + `base_card_id`) — collapses a card
      lingering across keyframes and a double-faced **Ruler/J-Ruler** (front `JRV-062` / back
      `JRV-062J` share a base id; `J`/`^`/`*` suffixes stripped). Keyed on `(base id, foil)` so a
      rare and its separate foil in one pack aren't merged. (733 raw → 576 distinct on the run.)
      Removed the now-wrong Ruler/J-Ruler `distinguish` from the JRV template.
- [x] **Boundary grouping** (`group._group_by_boundary`) — uncontrolled footage splits on the reveal
      pattern (a pack ends at its foil, the next opens at the next `N`); falls back to fixed chunks
      when no foils are present.
- [x] **Capacity-aware, rarity-ranked attribution** — cards placed rarest-first, each slot filling
      only to its `count`, so a **guaranteed-R** fixed slot and the variable **RARE** hit each land
      correctly. JRV template updated to `N×7 + R(fixed) + RARE(lottery) + FOIL(lottery)`.
- [x] **Observed-outcome floor** (`aggregate._floor_observed`) — a rare-but-real outcome (one XR
      seen) never rounds to 0%; bumped to ≥1% with points taken from the largest (still sums to 100).
- [x] **Table-bleed de-biasing** (`aggregate(max_hits_per_card=1)`, `--max-hits`) — in heuristic
      footage a pulled rare left on the table is re-detected as the "hit" in many later packs; each
      distinct card counts as a hit at most `max_hits` times per slot (gated on grouping confidence so
      controlled/golden is never touched). **Dropped the dominant bias: RARE `MR 47% → 19%` (cap 1),
      `R` now the dominant hit at 52% as expected.** Hybrid detections cached at
      `data/jrv_hybrid_detections.json` for free re-tuning.

> **Honest state of the JRV config:** the *shape* is now realistic (R-dominant RARE, plausible FOIL),
> but de-biasing exposed how **thin** the real sample is (RARE ~21–30 genuine hits, FOIL ~5 distinct)
> — the confidence report correctly flags `needs_more_samples`. Much of the earlier signal was bleed.
> Remaining levers: better multi-card region detection (Tier 2 yields ~1 card/frame), a Tier1+Tier2
> per-card hybrid, and ultimately controlled footage for a trustworthy config.

All checks green throughout: ruff + ruff format + mypy + **106 pytest**.

---

## Card-art assets  ✅ available

Reference card images are available as a flat `{cardId: url}` map (Force of Will), keyed by the
same collector id as the catalog:
`https://raw.githubusercontent.com/Niebvelungen/TCG-Arena-FoW/refs/heads/main/image_cache.json`
(7,247 cards, 117 sets, hosted on `fowsim.s3.amazonaws.com`; the printed `SET-NUMBER` id is
legible on the scans, confirming the Tier 1 OCR premise).

- [x] `assets.py` — `load_image_cache` (local path or URL), `select_ids` (by set / limit),
      `fetch_art` (cache, skip-existing, polite delay, writes an id→filename `manifest.json`).
- [x] `pack-miner fetch-art --cache <path|url> [--set CMF] [--limit N] [--out data/art]` CLI.
- [x] `IndexedCard.imageUrl` + optional `image_cache` arg to `build_index` (fills `imageUrl`).
- [ ] **Use now (M1–M5):** composite downloaded art into synthetic "controlled capture" frames
      as deterministic fixtures, so the vertical slice is testable before real footage exists.
- [ ] **Use later (M7):** build the per-set CLIP/DINOv2 + `faiss` embedding index from `data/art/`.

Bulk download (all ~7k images) is a single `fetch-art` invocation but is **deferred** — only a
small per-set sample is fetched as needed (art and `image_cache.json` live under gitignored `data/`).

## Cross-cutting

- **Fixtures:** keep a tiny sample catalog, a short clip, canned detection responses, and at
  least one golden config under `tests/fixtures/`. The golden config (M5) guards the output
  contract on every change. Card-art-derived synthetic frames supplement these (see above).
- **Validation everywhere:** every stage boundary parses/serializes through its `pydantic`
  contract.
- **Open questions** ([plan.md](plan.md) §12) to resolve as we hit them: foil detectability,
  non-skeleton pack structures, vision cost vs. sampling density, catalog refresh cadence.

## Suggested execution order

`Phase 0` → `M0` → `M1` → `M2` → `M3` → `M4` → `M5` (slice done) → `M6` → `M7`.
M2 can be stubbed (canned detections) to let M3–M5 proceed before the real vision client lands.
