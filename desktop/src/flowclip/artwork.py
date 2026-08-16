from __future__ import annotations

from PIL import Image, ImageDraw


ACCENT = "#117864"


def create_app_icon(size: int = 64) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = max(3, size // 12)
    radius = max(4, size // 9)
    draw.rounded_rectangle(
        (margin, margin, size - margin, size - margin),
        radius=radius,
        fill=ACCENT,
    )
    paper = (size * 0.27, size * 0.24, size * 0.73, size * 0.78)
    draw.rounded_rectangle(paper, radius=max(2, size // 20), fill="white")
    draw.rounded_rectangle(
        (size * 0.38, size * 0.15, size * 0.62, size * 0.31),
        radius=max(2, size // 24),
        fill="#d5ebe6",
        outline="white",
        width=max(1, size // 32),
    )
    for y in (0.44, 0.56, 0.68):
        draw.line(
            (size * 0.36, size * y, size * 0.64, size * y),
            fill="#7a8a87",
            width=max(1, size // 24),
        )
    return image
