from PIL import Image, ImageDraw

from backend.ink import apply_reference_ink
from backend.models import Layer


def layer(content: str) -> Layer:
    return Layer(
        id="title",
        kind="text",
        content=content,
        x=0,
        y=0,
        width=1,
        height=1,
        color="#202124",
    )


def test_reference_ink_splits_a_phrase_without_changing_words():
    image = Image.new("RGB", (400, 160), "#f4f4f2")
    draw = ImageDraw.Draw(image)
    for index, color in enumerate(("#202124", "#202124", "#b58d61", "#b58d61")):
        draw.rectangle((index * 100 + 16, 20, index * 100 + 72, 68), fill=color)
    for index, color in enumerate(("#202124", "#202124", "#b58d61", "#b58d61")):
        draw.rectangle((index * 100 + 16, 96, index * 100 + 72, 144), fill=color)
    painted = apply_reference_ink(image, [layer("柔和分流\n柔和分流")])[0]
    assert "".join(run.text for run in painted.runs) == "柔和分流\n柔和分流"
    assert [run.text for run in painted.runs] == ["柔和", "分流\n", "柔和", "分流"]
    assert painted.runs[0].color.lower() == "#202124"
    assert painted.runs[1].color.lower() == "#b58d61"
    assert painted.runs[3].color.lower() == "#b58d61"


def test_single_ink_keeps_one_color():
    image = Image.new("RGB", (200, 80), "#f4f4f2")
    ImageDraw.Draw(image).rectangle((20, 16, 170, 64), fill="#303436")
    painted = apply_reference_ink(image, [layer("细密微孔")])[0]
    assert painted.runs == []
    assert painted.color == "#202124"


def test_smooth_gradient_is_not_flattened_to_one_ink():
    image = Image.new("RGB", (240, 100), "#ffffff")
    draw = ImageDraw.Draw(image)
    for x in range(20, 220):
        t = (x - 20) / 199
        if (x - 20) % 50 < 35:
            draw.line((x, 20, x, 80), fill=(int(30 + 140 * t), 70, int(180 - 80 * t)))
    painted = apply_reference_ink(image, [layer("渐变文字")])[0]
    assert painted.gradient_end is not None
    assert painted.gradient_angle == 90
    assert painted.color != painted.gradient_end


def test_explicit_multicolor_gradient_from_layout_survives_sampling():
    from backend.models import TextRun

    original = layer("渐变文字")
    original.runs = [
        TextRun(text="渐变", color="#2854a3", gradient_end="#b58d61", gradient_angle=90),
        TextRun(text="文字", color="#202124"),
    ]
    assert apply_reference_ink(Image.new("RGB", (240, 100), "#ffffff"), [original]) == [original]
