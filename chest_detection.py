"""Detect ready chest icons, independent of the selected desktop-pet skin.

Template indexes identify visual variants, not the identity of a chest or its
30-minute countdown. Spatial locks in the watcher own that state.
"""
from __future__ import annotations

import math
from PIL import Image

from chest_assets import load_builtin_templates
from chest_matcher import Match, find_matches_many


def _same_target(a: Match, b: Match) -> bool:
    overlap = max(0, min(a.x+a.width, b.x+b.width)-max(a.x, b.x)) * max(
        0, min(a.y+a.height, b.y+b.height)-max(a.y, b.y))
    return overlap >= .5 * min(a.width*a.height, b.width*b.height)


def detect_templates(
    image: Image.Image,
    templates: list[tuple[int, Image.Image]],
    threshold: float = .94,
) -> list[tuple[int, Match]]:
    """Detect all ready icons, merging overlapping matches across variants.

    Templates contain only the icon's opaque interior, so pet pixels and
    desktop background are outside the comparison region. Image preparation
    is shared between variants. Built-in artwork uses colour proposals and
    local native-size/phase searches; every match retains the RGB score.
    """
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("Threshold must be finite and between zero and one")
    if any(index == 5 and template.info.get("wuwatappo_native") for index, template in templates):
        from chest_scaling import detect_scaled
        candidates = detect_scaled(image, templates, threshold)
    else:
        searches = find_matches_many(image, [template for _, template in templates], threshold=threshold)
        candidates = [(index, match) for (index, _), matches in zip(templates, searches)
                      for match in matches]
    found: list[tuple[int, Match]] = []
    for candidate in sorted(candidates, key=lambda item: item[1].score, reverse=True):
        if not any(_same_target(candidate[1], previous[1]) for previous in found):
            found.append(candidate)
    # Stable order makes independent buttons predictable as confidence varies.
    return sorted(found, key=lambda item: (item[1].y, item[1].x))


def detect_chests(image: Image.Image, threshold: float = .94) -> list[tuple[int, Match]]:
    """Use embedded ready-chest variants; no user screenshot setup is needed."""
    return detect_templates(image, load_builtin_templates(), threshold)
