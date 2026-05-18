"""Pure geometry helpers shared by template-rule extraction and aggregation-
region extraction. Both features need to ask the same question: which layout
words sit inside a user-drawn polygon? Azure DI polygons are flat
`[x0, y0, x1, y1, ...]` arrays in the page's native unit (inches for PDFs,
pixels for images), so all helpers operate in that unit."""

from typing import Sequence

from app.domain import WordData


def axis_aligned_bounds(
    polygon: Sequence[float] | None,
) -> tuple[float, float, float, float] | None:
    if polygon is None or len(polygon) < 2:
        return None

    min_x = float("inf")
    min_y = float("inf")
    max_x = float("-inf")
    max_y = float("-inf")

    for i in range(0, len(polygon) - 1, 2):
        x = polygon[i]
        y = polygon[i + 1]
        if x < min_x:
            min_x = x
        if x > max_x:
            max_x = x
        if y < min_y:
            min_y = y
        if y > max_y:
            max_y = y

    return (min_x, min_y, max_x, max_y)


def word_center_inside(
    word_polygon: Sequence[float] | None,
    bounds: tuple[float, float, float, float],
) -> bool:
    # Centroid (rather than overlap) is the correct test for short layout
    # words — a word straddling the edge of a region is "in" or "out" by
    # where its mass sits, not by whether any pixel touches the region.
    if word_polygon is None or len(word_polygon) < 2:
        return False

    sum_x = 0.0
    sum_y = 0.0
    count = 0
    for i in range(0, len(word_polygon) - 1, 2):
        sum_x += word_polygon[i]
        sum_y += word_polygon[i + 1]
        count += 1

    if count == 0:
        return False

    cx = sum_x / count
    cy = sum_y / count
    min_x, min_y, max_x, max_y = bounds
    return min_x <= cx <= max_x and min_y <= cy <= max_y


def words_inside_region(
    words: Sequence[WordData],
    region_polygon: Sequence[float] | None,
) -> list[WordData]:
    bounds = axis_aligned_bounds(region_polygon)
    if bounds is None:
        return []
    return [w for w in words if word_center_inside(w.polygon, bounds)]
