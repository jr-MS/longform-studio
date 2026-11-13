from __future__ import annotations

from PIL import Image

from .models import Layer, TextRun


def apply_reference_ink(image: Image.Image, layers: list[Layer]) -> list[Layer]:
    rgb = image.convert("RGB")
    return [
        _paint(rgb, layer) if layer.kind == "text" and layer.content else layer
        for layer in layers
    ]


def _paint(image: Image.Image, layer: Layer) -> Layer:
    if layer.gradient_end or layer.runs:
        return layer
    crop = _crop(image, layer)
    if crop.width < 8 or crop.height < 8:
        return layer
    gradient = _gradient(crop)
    if gradient:
        return layer.model_copy(update=gradient)
    runs = _runs(crop, layer.content, layer.color)
    if len(runs) < 2 or "".join(run.text for run in runs) != layer.content:
        return layer
    return layer.model_copy(update={"runs": runs, "color": runs[0].color})


def _gradient(crop: Image.Image) -> dict | None:
    pixels = list(crop.getdata())
    width, height = crop.size
    background = _median(_border(pixels, width, height))
    samples = [
        (index % width, index // width, color)
        for index, color in enumerate(pixels)
        if _distance(color, background) > 50
    ]
    if len(samples) < 80:
        return None
    # A true gradient varies smoothly, unlike two separately colored words.
    for axis, angle in ((0, 90), (1, 180)):
        low = min(item[axis] for item in samples)
        high = max(item[axis] for item in samples)
        if high - low < 16:
            continue
        bins: list[list[tuple[int, int, int]]] = [[] for _ in range(8)]
        for item in samples:
            bins[min(7, (item[axis] - low) * 8 // (high - low + 1))].append(item[2])
        points = [(index / 7, _median(colors)) for index, colors in enumerate(bins) if len(colors) >= 8]
        if len(points) < 6:
            continue
        mean_x = sum(x for x, _ in points) / len(points)
        means = [sum(color[c] for _, color in points) / len(points) for c in range(3)]
        variance = sum((x - mean_x) ** 2 for x, _ in points)
        slopes = [
            sum((x - mean_x) * (color[c] - means[c]) for x, color in points) / variance
            for c in range(3)
        ]
        start = tuple(means[c] - slopes[c] * mean_x for c in range(3))
        end = tuple(start[c] + slopes[c] for c in range(3))
        residual = sum(
            sum((color[c] - start[c] - slopes[c] * x) ** 2 for c in range(3))
            for x, color in points
        )
        total = sum(sum((color[c] - means[c]) ** 2 for c in range(3)) for _, color in points)
        if _distance(start, end) > 65 and total > 0 and residual / total < 0.06:
            return {"color": _hex(start), "gradient_end": _hex(end), "gradient_angle": angle, "runs": []}
    return None


def _crop(image: Image.Image, layer: Layer) -> Image.Image:
    width, height = image.size
    x0 = max(0, min(width - 1, round(layer.x * width)))
    y0 = max(0, min(height - 1, round(layer.y * height)))
    x1 = max(x0 + 1, min(width, round((layer.x + layer.width) * width)))
    y1 = max(y0 + 1, min(height, round((layer.y + layer.height) * height)))
    return image.crop((x0, y0, x1, y1))


def _runs(crop: Image.Image, content: str, fallback: str) -> list[TextRun]:
    pixels = list(crop.getdata())
    width, height = crop.size
    background = _median(_border(pixels, width, height))
    lines = content.split("\n")
    nonempty = [line for line in lines if line]
    bands = _bands(pixels, width, height)
    if len(bands) != len(nonempty):
        bands = [
            (index * height // len(lines), max(index * height // len(lines) + 1, (index + 1) * height // len(lines)))
            for index, line in enumerate(lines)
            if line
        ]
    pieces: list[tuple[str, str]] = []
    band_index = 0
    for index, line in enumerate(lines):
        if line:
            y0, y1 = bands[band_index]
            band_index += 1
            pieces.extend(_line(pixels, width, y0, y1, line, background, fallback))
        if index < len(lines) - 1:
            if pieces:
                text, color = pieces[-1]
                pieces[-1] = (text + "\n", color)
            else:
                pieces.append(("\n", fallback))
    merged: list[TextRun] = []
    for text, color in pieces:
        if merged and _same_family(merged[-1].color, color):
            merged[-1] = TextRun(text=merged[-1].text + text, color=merged[-1].color)
        else:
            merged.append(TextRun(text=text, color=color))
    return _keep_real_accents(merged, fallback)


def _line(
    pixels: list[tuple[int, int, int]],
    width: int,
    y0: int,
    y1: int,
    line: str,
    background: tuple[int, int, int],
    fallback: str,
) -> list[tuple[str, str]]:
    letters = [char for char in line if char not in _PUNCTUATION and not char.isspace()]
    slots = _slots(pixels, width, y0, y1, len(letters), background)
    if len(slots) != len(letters):
        return [(line, fallback)]
    colors = [_glyph_color(_slot_pixels(pixels, width, y0, y1, slot), background) for slot in slots]
    colored: list[tuple[str, str]] = []
    letter_index = 0
    previous = fallback
    for char in line:
        if char in _PUNCTUATION or char.isspace():
            colored.append((char, previous))
            continue
        sampled = colors[letter_index]
        letter_index += 1
        previous = _hex(sampled) if sampled is not None else fallback
        colored.append((char, previous))
    return colored


def _slot_pixels(
    pixels: list[tuple[int, int, int]],
    width: int,
    y0: int,
    y1: int,
    slot: tuple[int, int],
) -> list[tuple[int, int, int]]:
    start, end = slot
    return [pixels[y * width + x] for y in range(y0, y1) for x in range(start, min(end, width))]


def _glyph_color(
    slot: list[tuple[int, int, int]],
    background: tuple[int, int, int],
) -> tuple[int, int, int] | None:
    contrasted = [pixel for pixel in slot if _distance(pixel, background) > 28]
    warm = [pixel for pixel in contrasted if _warm(pixel)]
    if len(warm) >= 8:
        return _median(warm)
    if len(contrasted) < 6:
        return None
    return _median(contrasted)


_PUNCTUATION = set("，。、；：！？,.;:!?｜|·•—-～~（）()【】[]《》<>「」\"'“”‘’")


def _keep_real_accents(runs: list[TextRun], fallback: str) -> list[TextRun]:
    if len(runs) < 2:
        return runs
    base = max(runs, key=lambda run: len(run.text.replace("\n", "")))
    kept: list[TextRun] = []
    for run in runs:
        letters = sum(char not in _PUNCTUATION and not char.isspace() for char in run.text)
        accent = letters >= 2 and _warm(_rgb(run.color)) and not _same_family(run.color, base.color)
        color = run.color if accent else base.color
        if kept and _same_family(kept[-1].color, color):
            kept[-1] = TextRun(text=kept[-1].text + run.text, color=kept[-1].color)
        else:
            chosen = color if accent or not _close(color, fallback) else fallback
            kept.append(TextRun(text=run.text, color=chosen))
    return kept


def _bands(pixels: list[tuple[int, int, int]], width: int, height: int) -> list[tuple[int, int]]:
    rows = [
        sum(1 for x in range(1, width - 1) if _distance(pixels[y * width + x - 1], pixels[y * width + x + 1]) > 18)
        for y in range(height)
    ]
    peak = max(rows, default=0)
    if peak < 4:
        return []
    active = [index for index, value in enumerate(rows) if value >= peak * 0.22]
    if not active:
        return []
    bands: list[tuple[int, int]] = []
    start = previous = active[0]
    for y in active[1:]:
        if y > previous + 4:
            bands.append((start, previous + 1))
            start = y
        previous = y
    bands.append((start, previous + 1))
    return bands


def _slots(
    pixels: list[tuple[int, int, int]],
    width: int,
    y0: int,
    y1: int,
    count: int,
    background: tuple[int, int, int],
) -> list[tuple[int, int]]:
    if count < 1 or y1 <= y0:
        return []
    active = [
        x
        for x in range(width)
        if sum(1 for y in range(y0, y1) if _distance(pixels[y * width + x], background) > 36) > 2
    ]
    if not active:
        return []
    groups: list[list[int]] = [[active[0], active[0] + 1]]
    for x in active[1:]:
        if x > groups[-1][1] + 6:
            groups.append([x, x + 1])
        else:
            groups[-1][1] = x + 1
    groups = [group for group in groups if group[1] - group[0] >= 12]
    if not groups:
        return []
    widths = sorted(group[1] - group[0] for group in groups)
    typical = widths[len(widths) // 2]
    tight = [item for item in widths if item <= typical * 1.25] or widths
    typical = sorted(tight)[len(tight) // 2]
    trimmed: list[tuple[int, int]] = []
    for left, right in groups:
        if right - left <= typical * 1.45:
            trimmed.append((left, right))
            continue
        edges = [
            sum(1 for y in range(y0, y1) if _distance(pixels[y * width + x - 1], pixels[y * width + x + 1]) > 18)
            for x in range(left + 1, max(left + 2, right - 1))
        ]
        best = 0
        best_score = -1
        window = min(typical, len(edges))
        for offset in range(0, max(1, len(edges) - window + 1)):
            score = sum(edges[offset : offset + window])
            if score > best_score:
                best_score = score
                best = offset
        trimmed.append((left + best, min(right, left + best + typical)))
    if len(trimmed) == count:
        return trimmed
    left, right = trimmed[0][0], trimmed[-1][1]
    step = (right - left) / count
    return [(int(left + index * step), max(int(left + index * step) + 1, int(left + (index + 1) * step))) for index in range(count)]


def _warm(color: tuple[int, int, int]) -> bool:
    return color[0] - color[2] >= 28 and color[0] >= color[1] - 8


def _same_family(a: str, b: str) -> bool:
    if _close(a, b):
        return True
    left, right = _rgb(a), _rgb(b)
    return _warm(left) and _warm(right)


def _border(pixels: list[tuple[int, int, int]], width: int, height: int):
    last = (height - 1) * width
    return [
        *pixels[:width],
        *pixels[last : last + width],
        *(pixels[y * width] for y in range(height)),
        *(pixels[y * width + width - 1] for y in range(height)),
    ]


def _median(colors: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    return tuple(sorted(color[channel] for color in colors)[len(colors) // 2] for channel in range(3))


def _distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return sum((a[i] - b[i]) ** 2 for i in range(3)) ** 0.5


def _close(a: str, b: str) -> bool:
    return _distance(_rgb(a), _rgb(b)) <= 42


def _rgb(value: str) -> tuple[int, int, int]:
    raw = value.removeprefix("#")
    return tuple(int(raw[index : index + 2], 16) for index in (0, 2, 4))


def _hex(color: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(channel))) for channel in color))
