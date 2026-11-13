"""Small, dependency-light RGB template matcher for visible desktop buttons.

The score is ``max(NCC, 0) * exp(-RGB_MSE / (2 * 45**2))``.  NCC removes
the mean separately in each RGB channel; the pixel error then penalizes
incorrect colours or brightness.  An identical screenshot scores 1.0.  A
threshold of 0.94 is suitable for a static button recorded from the screen.
Scores are similarities, not statistical probabilities.

Only opaque templates are supported.  Capture a template from the visible
desktop so its background is included.  Transparent PNGs are rejected instead
of silently converting transparent pixels to black or white.

Correlation uses NumPy FFTs and integral images, without a sliding-window
tensor. High-threshold searches first reject impossible average colours and
score the remaining patches directly; dense candidate sets use FFTs instead.
Coordinates returned by find_match are relative to its input image.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

import numpy as np
from PIL import Image


@dataclass
class Match:
    x: int
    y: int
    width: int
    height: int
    score: float
    scale: float = 1.0

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


def _opaque_rgb(image: Image.Image) -> Image.Image:
    if not isinstance(image, Image.Image):
        raise TypeError("Expected a PIL Image")
    if image.width <= 0 or image.height <= 0:
        raise ValueError("Image is empty")
    if "A" in image.getbands() or "transparency" in image.info:
        alpha = image.convert("RGBA").getchannel("A")
        if alpha.getextrema()[0] != 255:
            raise ValueError("模板不能带透明像素，请从实际可见的按钮截图录制。")
    return image.convert("RGB")


def load_template(path: Path | str) -> Image.Image:
    """Read a valid opaque image, close its file, and return RGB pixels."""
    with Image.open(path) as image:
        rgb = _opaque_rgb(image)
        rgb.load()
    validate_template(rgb)
    return rgb


def validate_template(image: Image.Image) -> None:
    """Reject tiny or nearly uniform templates that produce unreliable NCC."""
    rgb = _opaque_rgb(image)
    if min(rgb.size) < 8:
        raise ValueError("按钮模板宽、高都至少需要 8 像素。")
    pixels = np.asarray(rgb, dtype=np.float64)
    # Removing each channel's mean also rejects a solid, strongly coloured box.
    centered = pixels - pixels.mean(axis=(0, 1), keepdims=True)
    contrast = float(np.sqrt(np.mean(centered * centered)))
    if not math.isfinite(contrast) or contrast < 4.0:
        raise ValueError("模板接近纯色，请框选包含图标、文字或边缘的完整按钮。")


def _integral(pixels: np.ndarray) -> np.ndarray:
    height, width = pixels.shape
    result = np.zeros((height + 1, width + 1), dtype=np.float64)
    result[1:, 1:] = np.cumsum(
        np.cumsum(pixels, axis=0, dtype=np.float64), axis=1, dtype=np.float64
    )
    return result


def _window_sum(integral: np.ndarray, width: int, height: int) -> np.ndarray:
    return (
        integral[height:, width:]
        - integral[:-height, width:]
        - integral[height:, :-width]
        + integral[:-height, :-width]
    )


def _fft_length(size: int) -> int:
    """Find a small 2/3/5-smooth FFT length at least as large as size."""
    best = 1 << (size - 1).bit_length()
    power_two = 1
    while power_two <= best:
        power_three = power_two
        while power_three <= best:
            candidate = power_three
            while candidate < size:
                candidate *= 5
            best = min(best, candidate)
            power_three *= 3
        power_two *= 2
    return best


class _PreparedImage:
    """Share one frame's lazy working arrays across its template variants.

    Nothing is cached between frames, so large desktop arrays are released
    after a search rather than being retained for the entire monitoring run.
    """
    def __init__(self, image: Image.Image):
        self.rgb = _opaque_rgb(image)
        self.pixels = np.asarray(self.rgb)
        self.height, self.width = self.pixels.shape[:2]
        self.fft_shape = (_fft_length(self.height), _fft_length(self.width))
        self._integrals = None
        self._image_ffts = None
        self._squared_integral = None

    @property
    def integrals(self):
        if self._integrals is None:
            self._integrals = [_integral(self.pixels[:, :, c]) for c in range(3)]
        return self._integrals

    @property
    def image_ffts(self):
        if self._image_ffts is None:
            self._image_ffts = [np.fft.rfft2(self.pixels[:, :, c], s=self.fft_shape)
                                for c in range(3)]
        return self._image_ffts

    @property
    def squared_integral(self):
        if self._squared_integral is None:
            squared = np.zeros((self.height, self.width), dtype=np.float64)
            for channel in range(3):
                squared += np.square(self.pixels[:, :, channel], dtype=np.float64)
            self._squared_integral = _integral(squared)
        return self._squared_integral


_SPARSE_MAX_CANDIDATES = 4096


def _sparse_candidates(prepared, target, centered, means, template_energy,
                       scale, threshold, max_results):
    """Return exact sparse scores, or None when a dense FFT is cheaper.

    A passing score requires MSE <= -2*45**2*log(threshold), since NCC <= 1.
    Squared differences of channel means are a lower bound on pixel MSE.
    This necessary condition discards ordinary desktop colours without any
    template FFT; it does not introduce an approximate matching threshold.
    """
    if threshold < .90 or threshold == 1.0:
        return None
    height, width = target.shape[:2]
    count = height * width
    bound = np.zeros((prepared.height-height+1, prepared.width-width+1), dtype=np.float64)
    for channel in range(3):
        difference = _window_sum(prepared.integrals[channel], width, height)
        difference /= count
        difference -= means[channel]
        np.square(difference, out=difference)
        bound += difference
    del difference
    # A small tolerance protects equality at the acceptance boundary from
    # floating point roundoff in the necessary-condition calculation.
    limit = -6.0 * 45.0**2 * math.log(threshold)
    possible = bound <= limit + 1e-8
    candidate_count = int(np.count_nonzero(possible))
    if candidate_count > _SPARSE_MAX_CANDIDATES:
        return None
    ys, xs = np.nonzero(possible)
    del bound, possible
    if not candidate_count:
        return []
    scores = np.zeros(candidate_count, dtype=np.float64)
    rows = np.arange(height)[None, :, None]
    columns = np.arange(width)[None, None, :]
    # Bound temporary patch storage independently of template/UI scale.
    batch_size = max(1, min(256, 131072 // (count * 3)))
    target_squared = float(np.sum(target * target))
    for start in range(0, candidate_count, batch_size):
        end = min(candidate_count, start+batch_size)
        patches = prepared.pixels[ys[start:end, None, None]+rows,
                                  xs[start:end, None, None]+columns].astype(np.float64)
        channel_sums = patches.sum(axis=(1, 2))
        squared = np.einsum("nijk,nijk->n", patches, patches)
        energy = squared - np.sum(channel_sums * channel_sums, axis=1) / count
        correlation = np.einsum("nijk,ijk->n", patches, centered)
        values = correlation / np.sqrt(np.maximum(energy, 1.0) * template_energy)
        np.clip(values, 0.0, 1.0, out=values)
        values[energy <= 1.0] = 0.0
        original_dot = correlation + channel_sums @ means
        mse = np.maximum((squared - 2.0*original_dot + target_squared) / (3*count), 0.0)
        values *= np.exp(-mse / (2.0 * 45.0**2))
        scores[start:end] = values
    matches = []
    for _ in range(max_results):
        best = int(np.argmax(scores))
        score = float(scores[best])
        if score <= 0 or score < threshold:
            break
        x, y = int(xs[best]), int(ys[best])
        matches.append(Match(x, y, width, height, score, scale))
        scores[(np.abs(xs-x) <= max(1, width//2))
               & (np.abs(ys-y) <= max(1, height//2))] = 0.0
    return matches


def _find_candidates(
    image: Image.Image,
    template: Image.Image,
    scales: tuple[float, ...] = (1.0,),
    threshold: float = 0.0,
    max_results: int = 1,
    _prepared: _PreparedImage | None = None,
) -> list[Match]:
    """Compute each score map once, then extract spatially distinct peaks."""
    prepared = _prepared if _prepared is not None else _PreparedImage(image)
    rgb = prepared.rgb
    template_rgb = _opaque_rgb(template)
    validate_template(template_rgb)
    if not scales:
        raise ValueError("At least one template scale is required")

    variants: list[tuple[float, Image.Image]] = []
    seen_sizes: set[tuple[int, int]] = set()
    for raw_scale in scales:
        scale = float(raw_scale)
        if not math.isfinite(scale) or scale <= 0:
            raise ValueError("Template scales must be finite and positive")
        size = (
            max(1, round(template_rgb.width * scale)),
            max(1, round(template_rgb.height * scale)),
        )
        if size in seen_sizes:
            continue
        seen_sizes.add(size)
        if min(size) < 8 or size[0] > rgb.width or size[1] > rgb.height:
            continue
        variant = (
            template_rgb
            if size == template_rgb.size
            else template_rgb.resize(size, Image.Resampling.LANCZOS)
        )
        variants.append((scale, variant))
    if not variants:
        return []

    image_height, image_width = prepared.height, prepared.width
    fft_shape = prepared.fft_shape

    candidates: list[Match] = []
    for scale, variant in variants:
        target = np.asarray(variant, dtype=np.float64)
        height, width = target.shape[:2]
        count = width * height
        means = target.mean(axis=(0, 1))
        centered = target - means
        template_energy = float(np.sum(centered * centered))
        if template_energy < 16.0 * count * 3:
            continue
        sparse = _sparse_candidates(prepared, target, centered, means,
                                    template_energy, scale, threshold, max_results)
        if sparse is not None:
            candidates.extend(sparse)
            continue
        # A circular correlation of size >= the image has the same values as
        # linear correlation in the valid region, where nothing wraps.
        image_ffts = prepared.image_ffts
        spectrum = np.zeros_like(image_ffts[0])
        for channel in range(3):
            target_fft = np.fft.rfft2(centered[:, :, channel], s=fft_shape)
            spectrum += image_ffts[channel] * np.conjugate(target_fft)
        del target_fft
        correlation = np.fft.irfft2(spectrum, s=fft_shape)[
            : image_height - height + 1, : image_width - width + 1
        ].copy()
        del spectrum

        patch_squared_sum = _window_sum(prepared.squared_integral, width, height)
        patch_energy = patch_squared_sum.copy()
        original_dot = correlation.copy()
        for channel in range(3):
            channel_sum = _window_sum(prepared.integrals[channel], width, height)
            patch_energy -= channel_sum * channel_sum / count
            original_dot += means[channel] * channel_sum
        del channel_sum
        usable = patch_energy > 1.0
        np.maximum(patch_energy, 1.0, out=patch_energy)
        denominator = np.sqrt(patch_energy * template_energy)
        scores = correlation / denominator
        np.clip(scores, 0.0, 1.0, out=scores)
        scores[~usable] = 0.0
        mse = (
            patch_squared_sum - 2.0 * original_dot + float(np.sum(target * target))
        ) / (3 * count)
        np.maximum(mse, 0.0, out=mse)
        scores *= np.exp(-mse / (2.0 * 45.0**2))
        for _ in range(max_results):
            flat_index = int(np.argmax(scores))
            y, x = np.unravel_index(flat_index, scores.shape)
            score = float(scores[y, x])
            if score <= 0 or score < threshold:
                break
            candidates.append(Match(int(x), int(y), width, height, score, scale))
            # A few shifted peaks around one icon describe the same button.
            # Suppress them on the score map without another FFT or screenshot.
            dx, dy = max(1, width//2), max(1, height//2)
            scores[max(0, y-dy):y+dy+1, max(0, x-dx):x+dx+1] = 0.0
    result: list[Match] = []
    for match in sorted(candidates, key=lambda item: item.score, reverse=True):
        if not any(_same_target(match, previous) for previous in result):
            result.append(match)
            if len(result) >= max_results:
                break
    return result


def _same_target(a: Match, b: Match) -> bool:
    intersection = max(0, min(a.x+a.width, b.x+b.width)-max(a.x, b.x)) * max(
        0, min(a.y+a.height, b.y+b.height)-max(a.y, b.y))
    return intersection >= .5 * min(a.width*a.height, b.width*b.height)


def find_match(
    image: Image.Image,
    template: Image.Image,
    scales: tuple[float, ...] = (1.0,),
) -> Match | None:
    """Return the best positive match, including low scores for diagnostics.

    Callers decide their acceptance threshold. Coordinates are relative to
    the supplied image; scale values resize the recorded template.
    """
    candidates = _find_candidates(image, template, scales)
    return candidates[0] if candidates else None


def find_matches(
    image: Image.Image,
    template: Image.Image,
    scales: tuple[float, ...] = (1.0,),
    threshold: float = .94,
    max_results: int = 8,
) -> list[Match]:
    """Return distinct matching buttons, computing a score map once per scale."""
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("Threshold must be finite and between zero and one")
    if not isinstance(max_results, int) or max_results <= 0:
        raise ValueError("max_results must be a positive integer")
    return _find_candidates(image, template, scales, threshold, max_results)


def find_matches_many(
    image: Image.Image,
    templates: list[Image.Image],
    threshold: float = .94,
    max_results: int = 8,
) -> list[list[Match]]:
    """Search several fixed-size templates while preparing the image once."""
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("Threshold must be finite and between zero and one")
    if not isinstance(max_results, int) or max_results <= 0:
        raise ValueError("max_results must be a positive integer")
    if not templates:
        return []
    prepared = _PreparedImage(image)
    return [_find_candidates(image, template, threshold=threshold,
                             max_results=max_results, _prepared=prepared)
            for template in templates]


def _self_test() -> None:
    """Deterministic algorithm check; does not capture screens or send input."""
    rng = np.random.default_rng(20261112)
    template_pixels = rng.integers(30, 190, size=(24, 32, 3), dtype=np.uint8)
    template = Image.fromarray(template_pixels)
    screen = rng.integers(0, 255, size=(180, 240, 3), dtype=np.uint8)
    screen[73:97, 129:161] = template_pixels
    match = find_match(Image.fromarray(screen), template)
    assert match is not None and (match.x, match.y) == (129, 73), match
    assert match.score > 0.999999, match
    assert match.center == (145, 85)
    assert find_match(Image.new("RGB", (240, 180), "gray"), template) is None
    try:
        validate_template(Image.new("RGB", (32, 24), "red"))
    except ValueError:
        pass
    else:
        raise AssertionError("Solid-colour template was accepted")
    try:
        validate_template(Image.new("RGBA", (32, 24), (255, 0, 0, 0)))
    except ValueError:
        pass
    else:
        raise AssertionError("Transparent template was accepted")
    print("chest_matcher self-test passed")


if __name__ == "__main__":
    _self_test()
