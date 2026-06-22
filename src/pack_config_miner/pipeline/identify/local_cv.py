"""Stage [3], Tier 2 -- Local CV identifier (plan.md sections 4, 10 / M7).

The fallback for footage where Tier 1 can't read the printed id (motion blur, small/angled cards):
match the card **art** instead of its text. We index ORB keypoint descriptors of every reference
image for a set into a FAISS binary (Hamming) index, then identify a card by searching its ORB
descriptors against the index and voting -- the catalog id with the most close descriptor matches
wins. This is robust to lighting, scale, and moderate perspective, and needs only the ``cv``
extra (``opencv`` + ``faiss`` + ``numpy``) -- no deep-model download.

Same provider-agnostic seam as Tier 1: :class:`LocalCVIdentifier` implements
:class:`~.base.Identifier` and emits :class:`~.base.Detection` records stamped
``idMethod="embedding"`` so resolve attributes them correctly. ``cv2`` / ``faiss`` / ``numpy``
are imported lazily.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import BBox, Detection

if TYPE_CHECKING:
    from ..frames import CandidateFrame

ORB_BITS = 256  # ORB descriptors are 32 bytes = 256 bits (Hamming space)
DEFAULT_NFEATURES = 500
DEFAULT_MAX_HAMMING = 64  # a query<->index descriptor match must be at least this close
DEFAULT_MIN_VOTES = 12  # a card needs at least this many matched descriptors to be accepted
DEFAULT_SAT_THRESHOLD = 32.0  # mean HSV saturation below this reads as monochrome (god pack)
CARD_W, CARD_H = 320, 446  # canonical warp size (FoW card aspect ~0.717)
_REF_WIDTH = 480  # reference art is resized to this width before ORB for consistency
_MANIFEST_NAME = "manifest.json"
_INDEX_FILE = "art.faiss"
_LABELS_FILE = "art_labels.json"


@dataclass
class ArtIndex:
    """A searchable ORB index over one set's reference art.

    ``index`` is a FAISS ``IndexBinaryFlat``; ``labels[i]`` is the catalog id that descriptor row
    ``i`` came from; ``card_ids`` is the distinct set indexed.
    """

    index: Any  # faiss.IndexBinaryFlat
    labels: list[str]
    card_ids: list[str]
    set_code: str | None = None


def _imports():
    try:
        import cv2
        import faiss
        import numpy as np
    except ImportError as exc:  # pragma: no cover - only without the cv extra installed
        raise RuntimeError(
            'The Tier 2 local-CV identifier needs the cv extra: pip install -e ".[cv]"'
        ) from exc
    return cv2, faiss, np


def card_descriptors(image_bgr: Any, *, nfeatures: int = DEFAULT_NFEATURES) -> Any | None:
    """ORB descriptors for a card image (``(n, 32)`` uint8), or ``None`` if too few features."""
    cv2, _faiss, np = _imports()
    img = image_bgr
    h, w = img.shape[:2]
    if w != _REF_WIDTH:
        scale = _REF_WIDTH / float(w)
        img = cv2.resize(img, (_REF_WIDTH, max(1, round(h * scale))))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    orb = cv2.ORB_create(nfeatures=nfeatures)
    _kp, des = orb.detectAndCompute(gray, None)
    if des is None or len(des) == 0:
        return None
    return np.ascontiguousarray(des, dtype=np.uint8)


def mean_saturation(image_bgr: Any) -> float:
    """Mean HSV saturation (0..255) of a card crop -- low means monochrome (a god-pack card)."""
    cv2, _faiss, np = _imports()
    if image_bgr.ndim != 3:
        return 0.0
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    return float(np.mean(hsv[:, :, 1]))


def is_monochrome(image_bgr: Any, *, sat_threshold: float = DEFAULT_SAT_THRESHOLD) -> bool:
    """True if the crop is essentially greyscale -- the visual signature of a god-pack card."""
    return mean_saturation(image_bgr) < sat_threshold


def _read_manifest(art_root: Path) -> dict[str, str]:
    path = art_root / _MANIFEST_NAME
    if not path.exists():
        raise FileNotFoundError(
            f"no {_MANIFEST_NAME} under {art_root} -- run `pack-miner fetch-art` first"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def build_art_index(
    art_root: str | Path,
    set_code: str | None = None,
    *,
    nfeatures: int = DEFAULT_NFEATURES,
) -> ArtIndex:
    """Build an :class:`ArtIndex` from downloaded reference art (uses the fetch-art manifest).

    Only ids whose collector id starts with ``<set_code>-`` are indexed when ``set_code`` is given.
    """
    cv2, faiss, _np = _imports()
    root = Path(art_root)
    manifest = _read_manifest(root)

    prefix = f"{set_code}-" if set_code else None
    index = faiss.IndexBinaryFlat(ORB_BITS)
    labels: list[str] = []
    card_ids: list[str] = []
    for card_id, filename in manifest.items():
        if prefix is not None and not card_id.startswith(prefix):
            continue
        image = cv2.imread(str(root / filename))
        if image is None:
            continue
        des = card_descriptors(image, nfeatures=nfeatures)
        if des is None:
            continue
        index.add(des)
        labels.extend([card_id] * len(des))
        card_ids.append(card_id)

    return ArtIndex(index=index, labels=labels, card_ids=card_ids, set_code=set_code)


def save_art_index(art_index: ArtIndex, out_dir: str | Path) -> None:
    """Persist an :class:`ArtIndex` (FAISS index + labels) to ``out_dir``."""
    _cv2, faiss, _np = _imports()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    faiss.write_index_binary(art_index.index, str(out / _INDEX_FILE))
    (out / _LABELS_FILE).write_text(
        json.dumps(
            {
                "labels": art_index.labels,
                "card_ids": art_index.card_ids,
                "set_code": art_index.set_code,
            }
        ),
        encoding="utf-8",
    )


def load_art_index(in_dir: str | Path) -> ArtIndex:
    """Load an :class:`ArtIndex` previously written by :func:`save_art_index`."""
    _cv2, faiss, _np = _imports()
    src = Path(in_dir)
    index = faiss.read_index_binary(str(src / _INDEX_FILE))
    meta = json.loads((src / _LABELS_FILE).read_text(encoding="utf-8"))
    return ArtIndex(
        index=index,
        labels=meta["labels"],
        card_ids=meta["card_ids"],
        set_code=meta.get("set_code"),
    )


def _order_quad(pts: Any) -> Any:
    """Order four points as top-left, top-right, bottom-right, bottom-left."""
    _cv2, _faiss, np = _imports()
    pts = pts.reshape(4, 2).astype("float32")
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array(
        [pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]],
        dtype="float32",
    )


def detect_card_regions(image_bgr: Any, *, min_area_frac: float = 0.02) -> list[tuple[Any, BBox]]:
    """Find card-shaped quads in a frame and perspective-warp each to a canonical crop.

    Returns ``(warped_bgr, bbox)`` per detected card (bbox in normalised frame coordinates). Falls
    back to a single whole-frame region when no quad is found, so single-card images still work.
    """
    cv2, _faiss, np = _imports()
    h, w = image_bgr.shape[:2]
    frame_area = float(h * w)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    dst = np.array([[0, 0], [CARD_W, 0], [CARD_W, CARD_H], [0, CARD_H]], dtype="float32")
    regions: list[tuple[Any, BBox]] = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area_frac * frame_area:
            continue
        approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        quad = _order_quad(approx)
        warped = cv2.warpPerspective(
            image_bgr, cv2.getPerspectiveTransform(quad, dst), (CARD_W, CARD_H)
        )
        x, y, bw, bh = cv2.boundingRect(approx)
        regions.append((warped, BBox(x / w, y / h, bw / w, bh / h)))

    if not regions:
        return [(image_bgr, BBox(0.0, 0.0, 1.0, 1.0))]
    return regions


def match_region(
    image_bgr: Any,
    art_index: ArtIndex,
    *,
    max_hamming: int = DEFAULT_MAX_HAMMING,
    min_votes: int = DEFAULT_MIN_VOTES,
    nfeatures: int = DEFAULT_NFEATURES,
) -> tuple[str, float] | None:
    """Identify one card crop against the index by descriptor voting; ``(cardId, confidence)``.

    Each query descriptor votes for the card of its nearest index descriptor when within
    ``max_hamming`` bits. The top card wins if it clears ``min_votes``; confidence scales from 0.5
    at the threshold toward 1.0.
    """
    _cv2, _faiss, _np = _imports()
    if art_index.index.ntotal == 0:
        return None
    des = card_descriptors(image_bgr, nfeatures=nfeatures)
    if des is None:
        return None

    distances, indices = art_index.index.search(des, 1)
    votes: dict[str, int] = {}
    for j in range(len(des)):
        if distances[j, 0] <= max_hamming:
            card = art_index.labels[int(indices[j, 0])]
            votes[card] = votes.get(card, 0) + 1
    if not votes:
        return None
    card_id = max(votes, key=lambda c: votes[c])
    top = votes[card_id]
    if top < min_votes:
        return None
    confidence = min(1.0, top / float(2 * min_votes))
    return card_id, round(confidence, 3)


class LocalCVIdentifier:
    """Tier 2 :class:`~.base.Identifier`: identify cards by art match (ORB + FAISS).

    Detects card regions in each frame and matches every region against the set's :class:`ArtIndex`.
    Set ``detect=False`` to treat the whole frame as one card (for pre-cropped scans).
    """

    def __init__(
        self,
        art_index: ArtIndex,
        *,
        detect: bool = True,
        max_hamming: int = DEFAULT_MAX_HAMMING,
        min_votes: int = DEFAULT_MIN_VOTES,
        god_packs: bool = True,
        sat_threshold: float = DEFAULT_SAT_THRESHOLD,
    ) -> None:
        self.art_index = art_index
        self.detect = detect
        self.max_hamming = max_hamming
        self.min_votes = min_votes
        self.god_packs = god_packs
        self.sat_threshold = sat_threshold

    def identify(self, frame: CandidateFrame) -> list[Detection]:
        cv2, _faiss, _np = _imports()
        if frame.path is None:
            raise ValueError(f"frame {frame.source_id}#{frame.ordinal} has no saved image to read")
        image = cv2.imread(str(frame.path))
        if image is None:
            return []

        regions = detect_card_regions(image) if self.detect else [(image, BBox(0.0, 0.0, 1.0, 1.0))]
        detections: list[Detection] = []
        for crop, bbox in regions:
            match = match_region(
                crop, self.art_index, max_hamming=self.max_hamming, min_votes=self.min_votes
            )
            if match is not None:
                card_id, confidence = match
                detections.append(
                    Detection(
                        sourceId=frame.source_id,
                        frameOrdinal=frame.ordinal,
                        cardId=card_id,
                        confidence=confidence,
                        bbox=bbox,
                        idMethod="embedding",
                    )
                )
            elif self.god_packs and is_monochrome(crop, sat_threshold=self.sat_threshold):
                # No art match + monochrome => a god-pack card (no reference art exists for it).
                detections.append(
                    Detection(
                        sourceId=frame.source_id,
                        frameOrdinal=frame.ordinal,
                        bbox=bbox,
                        godPack=True,
                    )
                )
        return detections
