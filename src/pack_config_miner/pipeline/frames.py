"""Stage [2] -- Frame sampling (plan.md section 4).

Extract "reveal" keyframes -- frames where cards are laid out -- from an ingested
:class:`MediaClip`, deduplicating near-identical frames so a controlled one-pack-per-shot clip
yields roughly one keyframe per pack.

The pipeline is split so the *decision* logic is testable without a video decoder installed:

* :func:`select_keyframe_indices` is pure -- it picks keyframes from a sequence of cheap
  per-frame *signatures* (perceptual fingerprints). Scene-change detection and dedup are the
  same operation here: keep a frame only when it differs enough from the last kept frame.
* :func:`frame_signature`, :func:`iter_decoded_frames` and :func:`sample_keyframes` do the heavy
  lifting (``numpy`` / PyAV / Pillow, all in the ``media`` extra) and are imported lazily.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .ingest import MediaClip

# A perceptual fingerprint of a frame: a small flat sequence of brightness samples.
Signature = Sequence[int]

DEFAULT_THRESHOLD = 12.0
DEFAULT_MIN_GAP = 1
DEFAULT_SIG_SIZE = 16


@dataclass(frozen=True)
class CandidateFrame:
    """A keyframe selected for identification.

    ``ordinal`` is the keyframe's position among the selected frames (0-based); ``frame_index``
    is its index in the decoded stream; ``path`` is where the image was written (``None`` if not
    saved).
    """

    source_id: str
    ordinal: int
    frame_index: int
    timestamp: float
    path: Path | None = None


def signature_distance(a: Signature, b: Signature) -> float:
    """Mean absolute difference between two equal-length signatures."""
    if len(a) != len(b):
        raise ValueError("signatures must be the same length")
    if not a:
        return 0.0
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)


def select_keyframe_indices(
    signatures: Sequence[Signature],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    min_gap: int = DEFAULT_MIN_GAP,
) -> list[int]:
    """Pick keyframe indices from per-frame signatures (pure, no decoder needed).

    The first frame is always kept as a baseline. A later frame is kept only when it differs
    from the last kept frame by at least ``threshold`` (scene change / not a near-duplicate) and
    sits at least ``min_gap`` frames after it (rate limit). Larger ``threshold`` => fewer, more
    distinct keyframes.
    """
    if not signatures:
        return []
    kept = [0]
    last_idx = 0
    for i in range(1, len(signatures)):
        if i - last_idx < min_gap:
            continue
        if signature_distance(signatures[last_idx], signatures[i]) >= threshold:
            kept.append(i)
            last_idx = i
    return kept


def _to_gray(image: Any) -> Any:
    """Luminance of an ``(H, W, 3)`` RGB array, or pass through an already-2D array."""
    import numpy as np

    arr = np.asarray(image)
    if arr.ndim == 2:
        return arr.astype(np.float64)
    weights = np.array([0.299, 0.587, 0.114])
    return arr[..., :3].astype(np.float64) @ weights


def frame_signature(image: Any, *, size: int = DEFAULT_SIG_SIZE) -> tuple[int, ...]:
    """Downscale a frame to a ``size``x``size`` grayscale fingerprint (needs ``numpy``).

    Accepts an RGB ``(H, W, 3)`` or grayscale ``(H, W)`` array and block-averages it to a
    fixed-size grid of 0-255 brightness samples -- cheap, resolution-independent, and robust to
    minor compression noise.
    """
    import numpy as np

    gray = _to_gray(image)
    h, w = gray.shape
    ys = np.linspace(0, h, size + 1).astype(int)
    xs = np.linspace(0, w, size + 1).astype(int)
    out: list[int] = []
    for i in range(size):
        for j in range(size):
            block = gray[ys[i] : ys[i + 1], xs[j] : xs[j + 1]]
            out.append(round(float(block.mean())) if block.size else 0)
    return tuple(out)


@dataclass(frozen=True)
class _DecodedFrame:
    frame_index: int
    timestamp: float
    image: Any  # numpy (H, W, 3) RGB array


def iter_decoded_frames(path: str | Path, *, sample_every: int = 1) -> Iterator[_DecodedFrame]:
    """Decode a video's frames as RGB arrays, keeping every ``sample_every``-th frame (PyAV).

    Subsampling with ``sample_every`` trades temporal resolution for speed before the (cheaper)
    signature pass runs. ``timestamp`` is in seconds when the stream carries presentation
    timestamps, else the frame ordinal.
    """
    try:
        import av
    except ImportError as exc:  # pragma: no cover - exercised only without the extra installed
        raise RuntimeError(
            'Frame sampling needs PyAV. Install the media extra: pip install -e ".[media]"'
        ) from exc

    if sample_every < 1:
        raise ValueError("sample_every must be >= 1")

    container = av.open(str(path))
    try:
        stream = container.streams.video[0]
        time_base = stream.time_base
        for n, frame in enumerate(container.decode(stream)):
            if n % sample_every != 0:
                continue
            if frame.pts is not None and time_base is not None:
                ts = float(frame.pts * time_base)
            else:
                ts = float(n)
            yield _DecodedFrame(n, ts, frame.to_ndarray(format="rgb24"))
    finally:
        container.close()


def _save_image(image: Any, path: Path) -> None:
    from PIL import Image

    Image.fromarray(image).save(path)


def sample_keyframes(
    clip: MediaClip,
    out_dir: str | Path | None = None,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    min_gap: int = DEFAULT_MIN_GAP,
    sample_every: int = 1,
    sig_size: int = DEFAULT_SIG_SIZE,
    save: bool = True,
) -> list[CandidateFrame]:
    """Decode ``clip``, select scene-change keyframes, and (optionally) write them as PNGs.

    Streams the decoder one frame at a time -- only the last *kept* frame's signature is held, so
    memory is O(1) in clip length (a 29-minute video would otherwise need every decoded frame in
    RAM at once). Selection mirrors :func:`select_keyframe_indices`: the first decoded frame is
    always kept, then a frame is kept when it differs from the last kept frame by >= ``threshold``
    and sits at least ``min_gap`` decoded steps after it. Returned records are in stream order; with
    ``save`` + ``out_dir`` each keyframe is written to ``<out_dir>/<source_id>_<ordinal>.png``.
    """
    out_path = Path(out_dir) if out_dir is not None else None
    if save and out_path is not None:
        out_path.mkdir(parents=True, exist_ok=True)

    frames: list[CandidateFrame] = []
    last_sig: tuple[int, ...] | None = None
    last_kept_step: int = 0
    ordinal = 0

    for step, d in enumerate(iter_decoded_frames(clip.path, sample_every=sample_every)):
        sig = frame_signature(d.image, size=sig_size)
        if last_sig is None:
            keep = True
        else:
            keep = (step - last_kept_step) >= min_gap and signature_distance(
                last_sig, sig
            ) >= threshold
        if not keep:
            continue

        path: Path | None = None
        if save and out_path is not None:
            path = out_path / f"{clip.source_id}_{ordinal:04d}.png"
            _save_image(d.image, path)
        frames.append(
            CandidateFrame(
                source_id=clip.source_id,
                ordinal=ordinal,
                frame_index=d.frame_index,
                timestamp=d.timestamp,
                path=path,
            )
        )
        last_sig = sig
        last_kept_step = step
        ordinal += 1

    return frames
