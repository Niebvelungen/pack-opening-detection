"""Stage [3], Tier 1 -- Claude vision identifier (plan.md section 4).

Sends a keyframe to a Claude vision model and asks it to enumerate every card: the printed
``SET-NUMBER`` collector id when legible, else the visible name, plus a foil flag, a normalised
bounding box, and a self-reported confidence. Output is constrained with structured outputs
(``output_config.format``) so the response is always a valid, parseable JSON object.

The ``anthropic`` SDK lives in the optional ``vision`` extra and is imported lazily. Credentials
resolve from ``VisionConfig.api_key`` if set, else from the environment by the SDK
(``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` / an ``ant`` profile). Before building the real
client we best-effort load a local gitignored ``.env`` via ``python-dotenv`` (also in the
``vision`` extra), so an on-disk ``ANTHROPIC_API_KEY=...`` just works without exporting it; a
variable already set in the environment is never overridden. The SDK auto-retries 429/5xx with
exponential backoff. The default model is the latest Claude Opus
(``claude-opus-4-8``); per the project conventions, consult the ``claude-api`` skill before
changing the model id or request shape rather than relying on memory.

Network I/O is confined to :meth:`VisionLLMIdentifier.identify`; the request body
(:func:`build_request`) and response decoding (:func:`parse_detections`) are pure and unit-tested
against a recorded fixture, so the test suite never hits the API.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import BBox, Detection

if TYPE_CHECKING:
    from ..frames import CandidateFrame

DEFAULT_MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = (
    "You are a meticulous trading-card vision analyst. You are shown a single frame from "
    "pack-opening footage in which freshly opened cards are laid out. Identify the cards "
    "precisely and never invent cards that are not visible."
)

USER_PROMPT = (
    "Enumerate every distinct trading card fully or partly visible in this frame. For each card:\n"
    "- cardId: the printed collector id in SET-NUMBER form (e.g. 'CMF-001'), read exactly as "
    "printed including any J/^/* suffix, ONLY if it is legible; otherwise null.\n"
    "- name: the visible card name if the printed id is NOT legible; otherwise null.\n"
    "- isFoil: true if the card is clearly holo/foil, false if clearly not, null if you cannot "
    "tell.\n"
    "- bbox: the card's bounding box as normalised 0..1 coordinates {x, y, w, h} (top-left "
    "origin); null if you cannot localise it.\n"
    "- confidence: your 0..1 confidence in this card's identification.\n"
    "Exclude card backs, sealed wrappers, hands, and non-card objects. Return one entry per card."
)

# Structured-output schema. Every object property is listed in `required` and objects set
# additionalProperties:false (both mandatory for structured outputs); nullable fields use union
# types since min/max and length constraints are not supported.
_BBOX_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "x": {"type": "number"},
        "y": {"type": "number"},
        "w": {"type": "number"},
        "h": {"type": "number"},
    },
    "required": ["x", "y", "w", "h"],
    "additionalProperties": False,
}

DETECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "detections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cardId": {"type": ["string", "null"]},
                    "name": {"type": ["string", "null"]},
                    "isFoil": {"type": ["boolean", "null"]},
                    "confidence": {"type": "number"},
                    "bbox": _BBOX_SCHEMA,
                },
                "required": ["cardId", "name", "isFoil", "confidence", "bbox"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["detections"],
    "additionalProperties": False,
}

_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


@dataclass
class VisionConfig:
    """Tunables for the Claude vision identifier."""

    model: str = DEFAULT_MODEL
    max_tokens: int = 4096
    effort: str | None = None  # None -> model default; else low|medium|high|xhigh|max
    api_key: str | None = None  # None -> resolved from the environment by the SDK
    max_retries: int = 2


def encode_image(path: str | Path) -> tuple[str, str]:
    """Read an image file into ``(media_type, base64_data)`` for an image content block."""
    p = Path(path)
    media_type = _MEDIA_TYPES.get(p.suffix.lower())
    if media_type is None:
        raise ValueError(f"unsupported image type for {p.name!r}: {p.suffix!r}")
    data = base64.standard_b64encode(p.read_bytes()).decode("ascii")
    return media_type, data


def build_request(config: VisionConfig, media_type: str, b64: str) -> dict[str, Any]:
    """Assemble the ``messages.create`` keyword arguments for one frame (pure, no I/O)."""
    output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": DETECTION_SCHEMA}}
    if config.effort is not None:
        output_config["effort"] = config.effort
    return {
        "model": config.model,
        "max_tokens": config.max_tokens,
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": USER_PROMPT},
                ],
            }
        ],
        "output_config": output_config,
    }


def parse_detections(data: dict[str, Any], source_id: str, frame_ordinal: int) -> list[Detection]:
    """Convert the model's validated JSON object into :class:`Detection` records (pure)."""
    detections: list[Detection] = []
    for item in data.get("detections", []):
        raw_bbox = item.get("bbox")
        bbox = (
            BBox(
                x=float(raw_bbox["x"]),
                y=float(raw_bbox["y"]),
                w=float(raw_bbox["w"]),
                h=float(raw_bbox["h"]),
            )
            if raw_bbox is not None
            else None
        )
        detections.append(
            Detection(
                sourceId=source_id,
                frameOrdinal=frame_ordinal,
                cardId=item.get("cardId"),
                name=item.get("name"),
                isFoil=item.get("isFoil"),
                confidence=float(item.get("confidence", 0.0)),
                bbox=bbox,
            )
        )
    return detections


def load_env_file() -> bool:
    """Best-effort load a local ``.env`` so credentials there are visible to the SDK.

    No-op (returns ``False``) if ``python-dotenv`` isn't installed. Searches from the current
    working directory upward and never overrides a variable already set in the environment.
    """
    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:  # pragma: no cover - only without python-dotenv installed
        return False
    return load_dotenv(find_dotenv(usecwd=True), override=False)


def _response_text(message: Any) -> str:
    """Pull the JSON text block out of a Messages API response, guarding refusals."""
    if getattr(message, "stop_reason", None) == "refusal":
        raise RuntimeError("vision model refused the request (stop_reason=refusal)")
    for block in message.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise RuntimeError("vision response contained no text block")


class VisionLLMIdentifier:
    """Tier 1 :class:`~.base.Identifier`: identify cards in a frame via a Claude vision model.

    Pass a pre-built ``client`` (any object exposing ``messages.create``) to inject a fake in
    tests; otherwise a real ``anthropic.Anthropic`` client is created lazily on first use.
    """

    def __init__(self, config: VisionConfig | None = None, client: Any | None = None) -> None:
        self.config = config or VisionConfig()
        self._client = client

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - only without the extra installed
                raise RuntimeError(
                    "The Tier 1 vision identifier needs the anthropic SDK. Install the vision "
                    'extra: pip install -e ".[vision]"'
                ) from exc
            if self.config.api_key is None:
                load_env_file()  # surface a local .env before the SDK reads the environment
            self._client = anthropic.Anthropic(
                api_key=self.config.api_key, max_retries=self.config.max_retries
            )
        return self._client

    def identify(self, frame: CandidateFrame) -> list[Detection]:
        """Identify every card in ``frame`` (reads ``frame.path``, calls the API, parses)."""
        if frame.path is None:
            raise ValueError(f"frame {frame.source_id}#{frame.ordinal} has no saved image to read")
        media_type, b64 = encode_image(frame.path)
        request = build_request(self.config, media_type, b64)
        message = self._ensure_client().messages.create(**request)
        data = json.loads(_response_text(message))
        return parse_detections(data, frame.source_id, frame.ordinal)
