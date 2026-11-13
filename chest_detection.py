"""Detect ready chest icons, independent of the selected desktop-pet skin.

Template indexes identify visual variants, not the identity of a chest or its
30-minute countdown. Spatial locks in the watcher own that state.
"""
from __future__ import annotations

from PIL import Image

from chest_assets import load_builtin_templates
from chest_matcher import Match, find_matches


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
    desktop background are outside the comparison region. Each template's
    FFT score map is computed once even when several copies are on screen.
    """
    candidates = [(index, match) for index, template in templates
                  for match in find_matches(image, template, threshold=threshold)]
    found: list[tuple[int, Match]] = []
    for candidate in sorted(candidates, key=lambda item: item[1].score, reverse=True):
        if not any(_same_target(candidate[1], previous[1]) for previous in found):
            found.append(candidate)
    # Stable order makes independent buttons predictable as confidence varies.
    return sorted(found, key=lambda item: (item[1].y, item[1].x))


def detect_chests(image: Image.Image, threshold: float = .94) -> list[tuple[int, Match]]:
    """Use embedded ready-chest variants; no user screenshot setup is needed."""
    return detect_templates(image, load_builtin_templates(), threshold)
