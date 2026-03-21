"""
BigEd CC — Icon and banner generator.

Design language:
  - TOP: Circuit/data flow pattern (clean, modern — not cluttered nodes)
  - BOTTOM: Brick wall (security foundation)
  - TEXT: "BE" on small icons, "BigEd" on banner
  - COLORS: Teal/cyan (#00bcd4) primary, green (#4caf50) accents, dark red bricks
  - NO YELLOW/GOLD on icons — clean tech aesthetic

BigEd as a personality: approachable AI assistant, not a corporate tool.

Usage: python generate_icon.py
"""
import math
import random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ── Color palette ────────────────────────────────────────────────────────────
TEAL = "#00bcd4"
TEAL_DIM = "#00838f"
GREEN = "#4caf50"
GREEN_DIM = "#2e7d32"
WHITE = "#e0e0e0"
BG_DARK = "#121212"
BRICK_MORTAR = "#1a1a1a"
BORDER = "#00bcd4"

BRICK_PALETTE = [
    "#8B1A1A", "#9B2020", "#7B1010",
    "#A02828", "#8A1818", "#6B0F0F",
    "#952020", "#7A1515",
]


def draw_bricks(draw, width, y_start, y_end, seed=7):
    """Brick wall in lower portion — security foundation."""
    rng = random.Random(seed)
    bw = max(10, width // 8)
    bh = max(5, (y_end - y_start) // 8)
    mortar = max(1, width // 64)

    draw.rectangle([0, y_start, width - 1, y_end - 1], fill=BRICK_MORTAR)

    row = 0
    y = y_start
    while y < y_end:
        offset = (bw // 2) if row % 2 else 0
        x = -offset
        while x < width:
            color = BRICK_PALETTE[rng.randint(0, len(BRICK_PALETTE) - 1)]
            x1, y1 = x + mortar, y + mortar
            x2, y2 = x + bw - mortar - 1, y + bh - mortar - 1
            if x2 > x1 and y2 > y1 and y1 >= y_start:
                draw.rectangle([x1, y1, x2, y2], fill=color)
                draw.line([x1, y1, x2, y1], fill="#B02020", width=1)
                draw.line([x1, y2, x2, y2], fill="#5B0808", width=1)
            x += bw
        y += bh
        row += 1


def draw_circuit(draw, width, height, seed=42):
    """Clean circuit pattern — data flow lines with endpoint dots."""
    rng = random.Random(seed)
    line_color = TEAL_DIM
    node_color = TEAL
    active_color = GREEN

    margin = width // 6
    # Horizontal + vertical circuit lines (grid-like, not messy)
    n_lines = max(4, width // 20)
    points = []

    for _ in range(n_lines):
        x = rng.randint(margin, width - margin)
        y = rng.randint(margin, height - margin)
        points.append((x, y))

    # Draw clean L-shaped connections (horizontal then vertical)
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        mid_x = (x1 + x2) // 2
        lw = max(1, width // 48)
        # Horizontal segment
        draw.line([(x1, y1), (mid_x, y1)], fill=line_color, width=lw)
        # Vertical segment
        draw.line([(mid_x, y1), (mid_x, y2)], fill=line_color, width=lw)
        # Horizontal to destination
        draw.line([(mid_x, y2), (x2, y2)], fill=line_color, width=lw)

    # Draw endpoint nodes
    r = max(2, width // 28)
    for i, (x, y) in enumerate(points):
        color = active_color if i < 2 else node_color
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color, outline=BG_DARK)


def make_icon(size=64):
    """Square icon: circuit top + bricks bottom + BE text."""
    img = Image.new("RGB", (size, size), BG_DARK)
    draw = ImageDraw.Draw(img)

    split = int(size * 0.55)

    # Circuit pattern (upper)
    draw_circuit(draw, size, split, seed=42)

    # Bricks (lower)
    draw_bricks(draw, size, split, size, seed=7)

    # Teal divider
    draw.line([(0, split), (size, split)], fill=TEAL, width=max(1, size // 32))

    # Border
    bw = max(1, size // 24)
    draw.rectangle([0, 0, size - 1, size - 1], outline=BORDER, width=bw)

    # "BE" text
    if size >= 48:
        try:
            font = ImageFont.truetype("arial.ttf", size // 3)
        except Exception:
            font = ImageFont.load_default()

        text = "BE"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx = (size - tw) // 2
        ty = (size - th) // 2 - size // 16

        # Shadow
        for dx, dy in [(-1, -1), (-1, 1), (1, -1), (1, 1), (0, 2)]:
            draw.text((tx + dx, ty + dy), text, fill="#000000", font=font)
        # White text (clean, no yellow)
        draw.text((tx, ty), text, fill=WHITE, font=font)

    return img


def make_banner(width=120, height=160):
    """Banner: circuit top + bricks bottom + BigEd text."""
    img = Image.new("RGB", (width, height), BG_DARK)
    draw = ImageDraw.Draw(img)

    split = int(height * 0.60)

    draw_circuit(draw, width, split - 10, seed=42)
    draw_bricks(draw, width, split, height, seed=7)
    draw.line([(0, split), (width, split)], fill=TEAL, width=2)

    # Dark overlay for text
    overlay = Image.new("RGBA", (width, 36), (0, 0, 0, 200))
    img_rgba = img.convert("RGBA")
    img_rgba.paste(overlay, (0, split - 36), overlay)
    img = img_rgba.convert("RGB")
    draw = ImageDraw.Draw(img)

    # "BigEd" text
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        font = ImageFont.load_default()

    text = "BigEd"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    tx = (width - tw) // 2
    ty = split - 30

    draw.text((tx + 1, ty + 1), text, fill="#000000", font=font)
    draw.text((tx, ty), text, fill=WHITE, font=font)

    # "CC" subtitle in teal
    try:
        font_sm = ImageFont.truetype("arial.ttf", 10)
    except Exception:
        font_sm = ImageFont.load_default()
    draw.text((tx + tw + 4, ty + 8), "CC", fill=TEAL, font=font_sm)

    return img


def main():
    out = Path(__file__).parent
    out.mkdir(exist_ok=True)

    banner = make_banner(120, 160)
    banner.save(out / "brick_banner.png")
    print("Saved brick_banner.png")

    sizes = [16, 32, 48, 64, 128, 256]
    icons = [make_icon(s) for s in sizes]
    icons[0].save(
        out / "brick.ico", format="ICO",
        sizes=[(s, s) for s in sizes], append_images=icons[1:],
    )
    print("Saved brick.ico")

    make_icon(64).save(out / "brick_64.png")
    make_icon(256).save(out / "brick_256.png")
    print("Saved brick_64.png + brick_256.png")


if __name__ == "__main__":
    main()
