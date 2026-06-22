"""End-to-end orchestration: catalog + footage + template -> config + report + review queue.

Wires stages [0]->[7] for one set. The deterministic tail ([4] resolve -> [5] group -> [6]
attribute -> [7] aggregate -> confidence -> review) is factored into
:func:`detections_to_observations` / :func:`build_outputs` so it can be driven from canned
detections in tests (the golden fixture) without any video or network. :func:`run_pipeline` adds
the live front half ([1] ingest -> [2] frames -> [3] identify) for real footage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..contracts.catalog import CatalogIndex
from ..contracts.manifest import Capture, Manifest
from ..contracts.observation import PackObservation
from ..contracts.pack_config import ConfidenceReport, PackConfig
from ..contracts.template import PackTemplate
from .aggregate import aggregate
from .attribute import attribute_pack
from .confidence import build_confidence_report
from .group import group_packs
from .identify.base import Detection, Identifier
from .resolve import ResolveStats, dedupe_consecutive, resolve_detections
from .review import ReviewItem, build_review_queue


@dataclass
class PipelineOutputs:
    """Everything one run produces for a set."""

    config: PackConfig
    report: ConfidenceReport
    review: list[ReviewItem] = field(default_factory=list)
    resolve_stats: ResolveStats | None = None
    god_pack_cards: int = 0


def detections_to_observations(
    detections: list[Detection],
    index: CatalogIndex,
    template: PackTemplate,
    *,
    capture: Capture,
) -> tuple[list[PackObservation], list[str], ResolveStats]:
    """Run the deterministic tail [4]->[6]: detections -> (observations, flags, resolve stats)."""
    resolved, _ = resolve_detections(detections, index, template.setCode)
    resolved = dedupe_consecutive(resolved)  # collapse lingering / Ruler front-back runs
    stats = ResolveStats(total=len(resolved), resolved=sum(1 for r in resolved if r.resolved))
    groups = group_packs(
        resolved, set_code=template.setCode, capture=capture, pack_size=template.packSize
    )
    observations = []
    validation_flags: list[str] = []
    for group in groups:
        observation, flags = attribute_pack(group, template)
        observations.append(observation)
        validation_flags.extend(flags)
    return observations, validation_flags, stats


def build_outputs(
    detections: list[Detection],
    index: CatalogIndex,
    template: PackTemplate,
    *,
    capture: Capture,
    max_hits_per_card: int = 1,
) -> PipelineOutputs:
    """Resolve -> group -> attribute -> aggregate -> confidence -> review for one set.

    God-pack detections (monochrome, no art) are split off before resolution and reported
    separately -- they are a pack-level variant, not per-slot outcomes, and would otherwise inflate
    the unresolved rate.
    """
    god_packs = [d for d in detections if d.godPack]
    normal = [d for d in detections if not d.godPack]
    observations, validation_flags, stats = detections_to_observations(
        normal, index, template, capture=capture
    )
    result = aggregate(observations, template, max_hits_per_card=max_hits_per_card)
    report = build_confidence_report(template.setCode, len(observations), result.tallies)
    review = build_review_queue(observations, validation_flags, report)
    if result.debiased:
        report.flags.append(
            f"de-biased {result.debiased} table-bleed re-detection(s) "
            "(a pulled rare re-counted as the hit in later packs)"
        )
    if god_packs:
        frames = {d.frameOrdinal for d in god_packs}
        review.append(
            ReviewItem(
                kind="god_pack",
                detail=(
                    f"{len(god_packs)} monochrome god-pack card(s) across {len(frames)} frame(s) "
                    "-- no reference art; all MR/Ruler/J-Ruler"
                ),
            )
        )
        report.flags.append(
            f"god packs: {len(god_packs)} monochrome card(s) detected -- model as a separate "
            "all-MR/Ruler/J-Ruler pack variant"
        )
    return PipelineOutputs(
        config=result.config,
        report=report,
        review=review,
        resolve_stats=stats,
        god_pack_cards=len(god_packs),
    )


def write_outputs(outputs: PipelineOutputs, out_dir: str | Path) -> dict[str, Path]:
    """Write config.json, report.json, and review.json into ``out_dir``; return their paths."""
    from .review import review_queue_to_json

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "config": out / "config.json",
        "report": out / "report.json",
        "review": out / "review.json",
    }
    paths["config"].write_text(
        json.dumps(outputs.config.to_config_dict(), indent=2), encoding="utf-8"
    )
    paths["report"].write_text(outputs.report.model_dump_json(indent=2), encoding="utf-8")
    paths["review"].write_text(review_queue_to_json(outputs.review), encoding="utf-8")
    return paths


def _make_identifier(
    tier: str,
    set_code: str,
    art_root: str | Path,
    model: str | None,
    effort: str | None,
) -> Identifier:
    """Build the identifier for ``tier``: ``cv`` (default) / ``vision`` / ``hybrid`` (cv+vision)."""

    def _vision() -> Identifier:
        from .identify.vision_llm import VisionConfig, VisionLLMIdentifier

        cfg = VisionConfig()
        if model is not None:
            cfg.model = model
        if effort is not None:
            cfg.effort = effort
        return VisionLLMIdentifier(cfg)

    def _cv() -> Identifier:
        from .identify.local_cv import LocalCVIdentifier, build_art_index

        return LocalCVIdentifier(build_art_index(art_root, set_code))

    if tier == "vision":
        return _vision()
    if tier == "hybrid":
        from .identify.hybrid import HybridIdentifier
        from .identify.vision_llm import VisionConfig, VisionLLMIdentifier

        # Tier 1 fallback for foils -> default to the cheaper Sonnet/low unless overridden.
        cfg = VisionConfig(model=model or "claude-sonnet-4-6", effort=effort or "low")
        return HybridIdentifier(_cv(), VisionLLMIdentifier(cfg))
    return _cv()


def run_pipeline(
    catalog_path: str | Path,
    manifest_path: str | Path,
    template_path: str | Path,
    *,
    identifier: Identifier | None = None,
    tier: str = "cv",
    model: str | None = None,
    effort: str | None = None,
    art_root: str | Path = "data/art",
    cache_dir: str | Path = "data/media",
    frames_dir: str | Path = "data/frames",
    max_hits_per_card: int = 1,
) -> PipelineOutputs:
    """Full pipeline for one set: ingest -> frames -> identify -> ... -> outputs.

    Processes every manifest source whose ``setCode`` matches the template. The front half needs
    the ``media`` (ffmpeg/PyAV) extra. Identification defaults to ``tier="cv"`` (Tier 2 local art
    match -- free, no hallucination, the primary path; ``cv`` extra, built from ``art_root``);
    ``tier="vision"`` is the optional Tier 1 LLM fallback (``vision`` extra + key,
    ``model``/``effort``). A pre-built ``identifier`` overrides both. Returns the computed outputs;
    persisting them is the caller's job (:func:`write_outputs`).
    """
    from .frames import sample_keyframes
    from .index import load_index
    from .ingest import ingest_source, load_manifest

    index = load_index(catalog_path)
    manifest: Manifest = load_manifest(manifest_path)
    template = PackTemplate.model_validate_json(Path(template_path).read_text(encoding="utf-8"))

    if identifier is None:
        identifier = _make_identifier(tier, template.setCode, art_root, model, effort)

    sources = [s for s in manifest.sources if s.setCode == template.setCode]
    detections: list[Detection] = []
    capture: Capture = "controlled"
    for source in sources:
        capture = source.capture
        clip = ingest_source(source, cache_dir)
        for frame in sample_keyframes(clip, frames_dir):
            detections.extend(identifier.identify(frame))

    return build_outputs(
        detections, index, template, capture=capture, max_hits_per_card=max_hits_per_card
    )
