"""Bounded native-artwork searches in colour-proposed chest regions.

Screen resolution is not a UI scale. We find small brown/gold connected
regions in physical pixels, then check only plausible icon sizes locally.
No full-screen FFT or full-screen template bank is allocated here.
"""
from dataclasses import replace
from functools import lru_cache
import math

import numpy as np
from PIL import Image

from chest_artwork import render_ready_interior
from chest_matcher import find_matches_many, _PreparedImage, _opaque_rgb


MIN_SCALE = .45
MAX_SCALE = 6.0


def _references(width, height):
    return [render_ready_interior(width, height, method, px, py)
            for method in ("bilinear", "bicubic")
            for px in (0., .25, .5, .75) for py in (0., .25, .5, .75)]


@lru_cache(maxsize=384)
def _colour_envelope(width, height):
    """Small cell-mean ranges covering every phase; no frame pixels cached."""
    references = [np.asarray(image, dtype=np.float64) for image in _references(width, height)]
    rows, columns = np.linspace(0, height, 5, dtype=int), np.linspace(0, width, 5, dtype=int)
    cells = []
    for y in range(4):
        for x in range(4):
            left, top, right, bottom = columns[x], rows[y], columns[x+1], rows[y+1]
            means = np.array([reference[top:bottom, left:right].mean(axis=(0, 1))
                              for reference in references])
            cells.append((left, top, right, bottom, means.min(axis=0), means.max(axis=0)))
    return sorted(cells, key=lambda cell: abs(cell[0]+cell[2]-width)+abs(cell[1]+cell[3]-height))


def _envelope_allows(prepared, width, height, threshold):
    """Reject only when every native phase must exceed the allowed RGB MSE.

    A cell's squared mean error is a lower bound on its pixel squared error.
    Ranges enlarge the set of possible references, so rejection remains a
    necessary-condition test and cannot lower the recognition threshold.
    """
    if threshold <= 0:
        return True
    rows, columns = prepared.height-height+1, prepared.width-width+1
    lower = np.zeros((rows, columns), dtype=np.float64)
    limit = -6*45.0**2*math.log(threshold)*width*height
    for left, top, right, bottom, low, high in _colour_envelope(width, height):
        count = (right-left)*(bottom-top)
        for channel in range(3):
            integral = prepared.integrals[channel]
            means = (integral[bottom:bottom+rows, right:right+columns]
                     - integral[top:top+rows, right:right+columns]
                     - integral[bottom:bottom+rows, left:left+columns]
                     + integral[top:top+rows, left:left+columns])/count
            distance = np.maximum(np.maximum(low[channel]-means, means-high[channel]), 0)
            lower += distance*distance*count
        if float(lower.min()) > limit+1e-6:
            return False
    return True


def colour_regions(image):
    """Return small brown components using a half-size run-length labelling.

    The palette is only a proposal filter. It cannot authorize a click; each
    proposal still needs the user's unchanged RGB correlation threshold.
    """
    pixels = np.asarray(_opaque_rgb(image))
    red, green, blue = (pixels[:, :, i] for i in range(3))
    mask = ((red >= 90) & (red <= 195) & (green >= 45) & (green <= 150)
            & (blue >= 15) & (blue <= 115) & (red > green) & (green > blue)
            & (red-green >= 22) & (green-blue >= 13))
    del red, green, blue
    # OR rather than interpolating: small icons retain their brown pixels.
    height, width = mask.shape
    padded = np.zeros(((height+1)//2*2, (width+1)//2*2), dtype=bool)
    padded[:height, :width] = mask
    small = padded[::2, ::2] | padded[1::2, ::2] | padded[::2, 1::2] | padded[1::2, 1::2]
    del padded, mask
    parents, bounds, weights = [], [], []

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    previous = []
    for y, row in enumerate(small):
        transitions = np.flatnonzero(np.diff(np.r_[False, row, False]))
        current = []
        cursor = 0
        for left, right in transitions.reshape(-1, 2):
            left, right = int(left), int(right)
            while cursor < len(previous) and previous[cursor][1] < left:
                cursor += 1
            neighbours = []
            position = cursor
            while position < len(previous) and previous[position][0] <= right:
                neighbours.append(root(previous[position][2]))
                position += 1
            if neighbours:
                label = min(neighbours)
                for other in set(neighbours):
                    if other != label:
                        parents[other] = label
                        a, b, c, d = bounds[other]
                        box = bounds[label]
                        box[:] = min(box[0], a), min(box[1], b), max(box[2], c), max(box[3], d)
                        weights[label] += weights[other]
                box = bounds[label]
                box[:] = min(box[0], left), box[1], max(box[2], right), y+1
                weights[label] += right-left
            else:
                label = len(parents)
                parents.append(label)
                bounds.append([left, y, right, y+1])
                weights.append(right-left)
            current.append((left, right, label))
        previous = current
    regions = []
    for label, box in enumerate(bounds):
        if parents[label] != label:
            continue
        left, top, right, bottom = box
        w, h = (right-left)*2, (bottom-top)*2
        if (weights[label] >= 8 and 6 <= w <= 196 and 4 <= h <= 150
                and .9 <= w/h <= 3.1 and weights[label]*4 >= w*h*.55):
            # Wood encloses a bright gold lock/sparkle. Brown digits on a
            # white countdown or status bar lack this interior colour.
            x0, y0, x1, y1 = left*2, top*2, min(width, right*2), min(height, bottom*2)
            middle = (x0+x1)//2
            band = max(1, w//10)
            centre = pixels[y0:y1, max(x0, middle-band):min(x1, middle+band)]
            r, g, b = (centre[:, :, i] for i in range(3))
            gold = (r >= 215) & (g >= 150) & (b >= 65) & (r > b) & (r-b >= 35)
            if np.count_nonzero(gold) >= max(1, h*band*.12):
                regions.append((x0, y0, x1, y1))
    return regions


def detect_scaled(image, templates, threshold):
    """Keep native screenshot scores and add scale/phase-native artwork."""
    found = []
    for left, top, right, bottom in colour_regions(image):
        component_width, component_height = right-left, bottom-top
        margin = max(6, math.ceil(component_width*.65))
        x0, y0 = max(0, left-margin), max(0, top-margin)
        x1, y1 = min(image.width, right+margin), min(image.height, bottom+margin)
        patch = image.crop((x0, y0, x1, y1))
        fixed = find_matches_many(patch, [t for _, t in templates], threshold=threshold,
                                  sparse_pixel_budget=32_768)
        matches = [(index, replace(match, x=match.x+x0, y=match.y+y0))
                   for (index, _), items in zip(templates, fixed) for match in items]
        found.extend(matches)
        if matches:
            continue
        # A ready chest's wood spans 28 CSS pixels. Include rasterisation
        # rounding and partially separated lid/body components in the band.
        estimated = component_width/28
        lo = max(round(32*MIN_SCALE), math.floor(32*estimated*.60)-3)
        hi = min(round(32*MAX_SCALE), math.ceil(32*estimated*1.12)+3)
        widths = sorted(range(lo, hi+1), key=lambda value: abs(value-32*estimated))
        prepared = _PreparedImage(patch)
        for width in widths:
            references = []
            for height in {round(width*25/32), math.ceil(width*25/32)}:
                if min(width, height) < 8 or width > patch.width or height > patch.height:
                    continue
                if _envelope_allows(prepared, width, height, threshold):
                    references.extend(_references(width, height))
            searches = find_matches_many(patch, references, threshold=threshold, max_results=2,
                                         sparse_pixel_budget=32_768)
            native = [(5, replace(match, x=match.x+x0, y=match.y+y0, scale=width/32))
                      for matches in searches for match in matches]
            if native:
                found.extend(native)
                break
    return found
