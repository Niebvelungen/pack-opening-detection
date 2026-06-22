"""Tests for stage [3] Tier 1 identification.

The request-building and response-parsing logic is pure and tested directly; the network call is
exercised through a fake client that replays a recorded response fixture, so the suite never hits
the Anthropic API (and does not require the ``vision`` extra to be installed).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pack_config_miner.pipeline.frames import CandidateFrame
from pack_config_miner.pipeline.identify.base import BBox, Detection, Identifier
from pack_config_miner.pipeline.identify.vision_llm import (
    DETECTION_SCHEMA,
    VisionConfig,
    VisionLLMIdentifier,
    build_request,
    encode_image,
    load_env_file,
    parse_detections,
)

FIXTURES = Path(__file__).parent / "fixtures"
VISION_RESPONSE = FIXTURES / "vision_response_sample.json"


def _load_response() -> dict:
    return json.loads(VISION_RESPONSE.read_text(encoding="utf-8"))


class _FakeMessages:
    """Stands in for ``client.messages``; records the last request and replays a canned reply."""

    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self._text = text
        self._stop_reason = stop_reason
        self.last_request: dict | None = None

    def create(self, **kwargs):
        self.last_request = kwargs
        block = SimpleNamespace(type="text", text=self._text)
        return SimpleNamespace(content=[block], stop_reason=self._stop_reason)


class _FakeClient:
    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self.messages = _FakeMessages(text, stop_reason)


def test_parse_detections_maps_fields() -> None:
    dets = parse_detections(_load_response(), source_id="s1", frame_ordinal=2)
    assert len(dets) == 3
    first = dets[0]
    assert first == Detection(
        sourceId="s1",
        frameOrdinal=2,
        cardId="CMF-001",
        name=None,
        isFoil=True,
        confidence=0.96,
        bbox=BBox(0.05, 0.1, 0.28, 0.4),
    )
    # Suffix ids are preserved verbatim; name-only + null bbox/foil round-trips.
    assert dets[1].cardId == "CMF-013J"
    assert dets[2].cardId is None
    assert dets[2].name == "Lapis, the Brilliant Sorcerer"
    assert dets[2].bbox is None and dets[2].isFoil is None


def test_parse_detections_empty() -> None:
    assert parse_detections({"detections": []}, "s1", 0) == []


def test_build_request_shape() -> None:
    req = build_request(VisionConfig(model="claude-opus-4-8"), "image/png", "BASE64DATA")
    assert req["model"] == "claude-opus-4-8"
    content = req["messages"][0]["content"]
    image, text = content[0], content[1]
    assert image["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": "BASE64DATA",
    }
    assert text["type"] == "text"
    # Structured output is requested; no effort key unless configured.
    assert req["output_config"]["format"]["schema"] is DETECTION_SCHEMA
    assert "effort" not in req["output_config"]


def test_build_request_includes_effort_when_set() -> None:
    req = build_request(VisionConfig(effort="low"), "image/jpeg", "x")
    assert req["output_config"]["effort"] == "low"


def test_encode_image_round_trips(tmp_path: Path) -> None:
    import base64

    img = tmp_path / "frame.png"
    payload = b"\x89PNG\r\n\x1a\nnot-a-real-png-but-bytes"
    img.write_bytes(payload)
    media_type, b64 = encode_image(img)
    assert media_type == "image/png"
    assert base64.standard_b64decode(b64) == payload


def test_encode_image_rejects_unknown_extension(tmp_path: Path) -> None:
    bad = tmp_path / "frame.bmp"
    bad.write_bytes(b"x")
    with pytest.raises(ValueError, match="unsupported image type"):
        encode_image(bad)


def test_identify_end_to_end_with_fake_client(tmp_path: Path) -> None:
    img = tmp_path / "s1_0003.png"
    img.write_bytes(b"\x89PNGfake")
    frame = CandidateFrame(source_id="s1", ordinal=3, frame_index=42, timestamp=1.5, path=img)
    client = _FakeClient(VISION_RESPONSE.read_text(encoding="utf-8"))
    identifier = VisionLLMIdentifier(client=client)

    dets = identifier.identify(frame)

    assert [d.cardId for d in dets] == ["CMF-001", "CMF-013J", None]
    # source/ordinal are stamped from the frame, not the model output.
    assert all(d.sourceId == "s1" and d.frameOrdinal == 3 for d in dets)
    # The request actually carried the encoded image.
    sent = client.messages.last_request
    assert sent["messages"][0]["content"][0]["source"]["media_type"] == "image/png"


def test_identify_raises_on_refusal(tmp_path: Path) -> None:
    img = tmp_path / "s1_0000.png"
    img.write_bytes(b"x")
    frame = CandidateFrame(source_id="s1", ordinal=0, frame_index=0, timestamp=0.0, path=img)
    client = _FakeClient('{"detections": []}', stop_reason="refusal")
    with pytest.raises(RuntimeError, match="refused"):
        VisionLLMIdentifier(client=client).identify(frame)


def test_identify_requires_saved_frame() -> None:
    frame = CandidateFrame(source_id="s1", ordinal=0, frame_index=0, timestamp=0.0, path=None)
    with pytest.raises(ValueError, match="no saved image"):
        VisionLLMIdentifier(client=_FakeClient("{}")).identify(frame)


def test_vision_identifier_satisfies_protocol() -> None:
    # runtime_checkable Protocol — the Tier 1 client is a valid Identifier.
    assert isinstance(VisionLLMIdentifier(client=_FakeClient("{}")), Identifier)


def test_load_env_file_reads_local_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("dotenv")
    (tmp_path / ".env").write_text(
        "PACK_MINER_TEST_KEY=from-dotenv\nPACK_MINER_PRESET=from-dotenv\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PACK_MINER_TEST_KEY", raising=False)
    # A variable already in the environment must win over the .env file.
    monkeypatch.setenv("PACK_MINER_PRESET", "from-environment")

    assert load_env_file() is True
    import os

    assert os.environ["PACK_MINER_TEST_KEY"] == "from-dotenv"
    assert os.environ["PACK_MINER_PRESET"] == "from-environment"
