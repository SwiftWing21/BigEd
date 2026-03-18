"""
Run this once to generate brick.ico and brick_banner.png
Usage: python generate_icon.py
"""
import random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def draw_brick_wall(width, height, seed=7):
    rng = random.Random(seed)
    img = Image.new("RGB", (width, height), "#4a4a4a")
    draw = ImageDraw.Draw(img)

    bw = max(16, width // 7)
    bh = max(8, height // 18)
    mortar = 2
    mortar_color = "#5c5c5c"

    brick_palette = [
        "#8B1A1A", "#9B2020", "#7B1010",
        "#A02828", "#8A1818", "#6B0F0F",
        "#952020", "#7A1515",
    ]

    # Fill mortar base
    draw.rectangle([0, 0, width - 1, height - 1], fill=mortar_color)

    row = 0
    y = 0
    while y < height + bh:
        offset = (bw // 2) if row % 2 else 0
        x = -offset
        while x < width:
            color = brick_palette[rng.randint(0, len(brick_palette) - 1)]
            x1 = x + mortar
            y1 = y + mortar
            x2 = x + bw - mortar - 1
            y2 = y + bh - mortar - 1
            if x2 > x1 and y2 > y1:
                draw.rectangle([x1, y1, x2, y2], fill=color)
                # Subtle highlight on top edge
                draw.line([x1, y1, x2, y1], fill="#B02020", width=1)
                # Subtle shadow on bottom edge
                draw.line([x1, y2, x2, y2], fill="#5B0808", width=1)
                # Occasional texture dots
                for _ in range(rng.randint(0, 3)):
                    tx = rng.randint(x1, x2)
                    ty = rng.randint(y1, y2)
                    draw.point((tx, ty), fill="#6B1010")
            x += bw
        y += bh
        row += 1

    return img


def make_banner(width=120, height=160):
    """120x160 banner for app header."""
    img = draw_brick_wall(width, height)
    draw = ImageDraw.Draw(img)

    # Dark semi-transparent overlay at bottom for text
    overlay = Image.new("RGBA", (width, 40), (0, 0, 0, 160))
    img_rgba = img.convert("RGBA")
    img_rgba.paste(overlay, (0, height - 40), overlay)
    img = img_rgba.convert("RGB")
    draw = ImageDraw.Draw(img)

    # "FC" initials centered at bottom
    try:
        font = ImageFont.truetype("arial.ttf", 22)
    except Exception:
        font = ImageFont.load_default()

    text = "FC"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (width - tw) // 2
    ty = height - 35 + (35 - th) // 2
    draw.text((tx + 1, ty + 1), text, fill="#000000", font=font)
    draw.text((tx, ty), text, fill="#FFD700", font=font)

    return img


def make_icon(size=64):
    """Square icon for window and .exe."""
    img = draw_brick_wall(size, size)
    draw = ImageDraw.Draw(img)

    # Thin gold border
    draw.rectangle([0, 0, size - 1, size - 1], outline="#B8860B", width=2)

    return img


def main():
    out = Path(__file__).parent
    out.mkdir(exist_ok=True)

    # 120x160 banner PNG
    banner = make_banner(120, 160)
    banner.save(out / "brick_banner.png")
    print("Saved brick_banner.png (120x160)")

    # Multi-size .ico (16, 32, 48, 64, 128)
    sizes = [16, 32, 48, 64, 128]
    icons = [make_icon(s) for s in sizes]
    icons[0].save(
        out / "brick.ico",
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=icons[1:],
    )
    print("Saved brick.ico (multi-size)")

    # Also save a 64px PNG for taskbar fallback
    make_icon(64).save(out / "brick_64.png")
    print("Saved brick_64.png")


if __name__ == "__main__":
    main()
