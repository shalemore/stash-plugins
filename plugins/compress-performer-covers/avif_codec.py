"""Encodes a cover to the best-looking AVIF that fits a size limit, with FFmpeg's libaom.

The settings come from a benchmark on real covers scaled to 720px and scored with SSIMULACRA2 at
~95 KiB. All-intra mode with libaom's image-quality tuning and full chroma looked better than the
video mode used before and encoded 5 times faster. Lower cpu-used values took over 4 times as long
for a barely visible gain.
"""

import math
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

MAX_WIDTH = 720
MIN_DIMENSION = 160
# CRF runs from 0 to 63, lower is better. A cover that only fits above MAX_CRF would look too
# degraded, so a smaller one is encoded instead.
MAX_CRF = 25
# A typical cover fits at about this CRF, and its size shrinks by about this ratio per CRF step.
FIRST_GUESS_CRF = 12
SIZE_RATIO_PER_CRF = 0.94

ENCODER = ["-c:v", "libaom-av1", "-usage", "allintra", "-cpu-used", "6", "-row-mt", "1"]
# libaom 3.12 added this tuning for still images.
IMAGE_TUNING = ["-aom-params", "tune=iq"]


@dataclass(frozen=True)
class CompressedCover:
    data: bytes
    crf: int
    width: int
    height: int


def resolve_executable(configured_path: str | None, name: str) -> str:
    if configured_path and Path(configured_path).is_file():
        return configured_path
    if discovered := shutil.which(name):
        return discovered
    detail = f"; Stash returned path: {configured_path}" if configured_path else ""
    raise RuntimeError(f"Could not find {name}{detail}")


def run_ffmpeg(ffmpeg: str, *args: str) -> None:
    command = [ffmpeg, "-v", "error", "-y", *args]
    try:
        result = subprocess.run(command, capture_output=True, check=False, timeout=180)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("FFmpeg timed out") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not execute {ffmpeg}: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"FFmpeg failed: {message or 'unknown error'}")


class Encoder:
    def __init__(self, ffmpeg: str) -> None:
        self.ffmpeg: str = ffmpeg
        self.args: list[str] = self._supported_args()

    def _supported_args(self) -> list[str]:
        """Checks on a tiny image that FFmpeg can write AVIF, and drops the image tuning when its
        libaom is too old for it."""
        sample = ["-f", "lavfi", "-i", "color=gray:size=64x64", "-frames:v", "1"]
        error = None
        with tempfile.TemporaryDirectory(prefix="stash-cover-") as temp:
            for args in ([*ENCODER, *IMAGE_TUNING], ENCODER):
                try:
                    run_ffmpeg(
                        self.ffmpeg,
                        *sample,
                        *args,
                        "-crf",
                        "30",
                        "-f",
                        "avif",
                        f"{temp}/check.avif",
                    )
                    return args
                except RuntimeError as exc:
                    error = exc
        raise RuntimeError(f"FFmpeg cannot encode AVIF with libaom-av1: {error}")

    def scale(self, source: Path, width: str) -> tuple[Path, int, int]:
        """Decodes and scales the source into a lossless file next to it, so that each encode
        only reads it, and returns the file and its dimensions."""
        scaled = source.with_name("scaled.y4m")
        filters = f"scale={width}:-2:flags=lanczos"
        args = ["-i", str(source), "-frames:v", "1", "-vf", filters, "-pix_fmt", "yuv444p"]
        run_ffmpeg(self.ffmpeg, *args, str(scaled))
        # A Y4M file starts with a text header such as "YUV4MPEG2 W720 H1080 ...".
        with scaled.open("rb") as file:
            match = re.search(rb" W(\d+) H(\d+)", file.readline())
        if not match:
            raise RuntimeError("Could not read the dimensions of the scaled cover")
        return scaled, int(match[1]), int(match[2])

    def encode(self, scaled: Path, crf: int) -> bytes:
        output = scaled.with_name("cover.avif")
        args = ["-i", str(scaled), "-frames:v", "1", *self.args, "-crf", str(crf)]
        run_ffmpeg(self.ffmpeg, *args, "-pix_fmt", "yuv444p", "-f", "avif", str(output))
        return output.read_bytes()


def best_fit(encode: Callable[[int], bytes], target_bytes: int) -> tuple[int, bytes] | None:
    """The lowest CRF up to MAX_CRF whose encoding fits, and that encoding.

    A cover's size shrinks about exponentially with the CRF, so each guess interpolates between
    the sizes seen so far. A search takes 3 or 4 encodes, where bisecting takes 5 or 6.
    """
    low, high = 0, MAX_CRF
    sizes: dict[int, int] = {}
    best: tuple[int, bytes] | None = None
    crf = FIRST_GUESS_CRF
    while True:
        data = encode(crf)
        sizes[crf] = len(data)
        if len(data) <= target_bytes:
            best, high = (crf, data), crf - 1
        else:
            low = crf + 1
        if low > high:
            return best
        # Should the guesses keep missing, as for a cover whose size jumps, bisecting ends it.
        guess = interpolate(sizes, target_bytes) if len(sizes) < 5 else (low + high) // 2
        crf = min(max(guess, low), high)


def interpolate(sizes: dict[int, int], target_bytes: int) -> int:
    """The CRF at which the size would just reach the target, estimated from the two sizes seen
    closest to it."""
    fits = sorted(crf for crf, size in sizes.items() if size <= target_bytes)
    too_big = sorted((crf for crf, size in sizes.items() if size > target_bytes), reverse=True)
    pair = [too_big[0], fits[0]] if fits and too_big else fits[:2] or too_big[:2]
    if len(pair) == 2:
        low, high = sorted(pair)
        ratio = math.pow(sizes[high] / sizes[low], 1 / (high - low))
    else:
        ratio = SIZE_RATIO_PER_CRF
    start = fits[0] if fits else too_big[0]
    if ratio >= 1:
        return start + 1
    return start + math.ceil(math.log(target_bytes / sizes[start]) / math.log(ratio))


def compress_cover(encoder: Encoder, source: bytes, target_bytes: int) -> CompressedCover:
    with tempfile.TemporaryDirectory(prefix="stash-cover-") as temp:
        source_path = Path(temp) / "source"
        _ = source_path.write_bytes(source)

        scaled, width, height = encoder.scale(source_path, f"min({MAX_WIDTH}\\,iw)")
        while True:
            if fit := best_fit(partial(encoder.encode, scaled), target_bytes):
                return CompressedCover(fit[1], fit[0], width, height)
            next_width = round(width * 0.9)
            if next_width < MIN_DIMENSION and height * 0.9 < MIN_DIMENSION:
                break
            scaled, width, height = encoder.scale(source_path, str(next_width - next_width % 2))
    raise RuntimeError(
        f"Could not compress the cover to {target_bytes / 1024:.1f} KiB at CRF {MAX_CRF} or "
        f"better and at least {MIN_DIMENSION}px"
    )
