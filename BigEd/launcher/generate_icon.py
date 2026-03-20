"""
BigEd CC — Icon and banner generator.
Creates brick.ico (app icon) and brick_banner.png (header banner).

The icon represents BigEd CC's identity: a brick wall foundation with
a neural network overlay — infrastructure meets AI fleet intelligence.

Usage: python generate_icon.py
"""
import math
import random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def draw_brick_wall(width, height, seed=7):
    """Draw a textured brick wall background."""
    rng = random.Random(seed)
    img = Image.new("RGB", (width, height), "#1a1a1a")
    draw = ImageDraw.Draw(img)

    bw = max(12, width // 8)
    bh = max(6, height // 16)
    mortar = max(1, width // 64)
    mortar_color = "#2d2d2d"

    brick_palette = [
        "#8B1A1A", "#9B2020", "#7B1010",
        "#A02828", "#8A1818", "#6B0F0F",
        "#952020", "#7A1515",
    ]

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
                draw.line([x1, y1, x2, y1], fill="#B02020", width=1)
                draw.line([x1, y2, x2, y2], fill="#5B0808", width=1)
                for _ in range(rng.randint(0, 2)):
                    tx = rng.randint(x1, x2)
                    ty = rng.randint(y1, y2)
                    draw.point((tx, ty), fill="#6B1010")
            x += bw
        y += bh
        row += 1

    return img


def draw_neural_overlay(draw, width, height, seed=42):
    """Draw a subtle neural network / circuit pattern overlay."""
    rng = random.Random(seed)
    node_color = "#c8a84b"  # gold
    line_color = "#8B6914"  # dim gold
    glow_color = "#4caf50"  # green accent

    # Place nodes
    nodes = []
    margin = width // 6
    for _ in range(6 + width // 20):
        x = rng.randint(margin, width - margin)
        y = rng.randint(margin, height - margin)
        nodes.append((x, y))

    # Draw connections (edges) first
    for i, (x1, y1) in enumerate(nodes):
        for j, (x2, y2) in enumerate(nodes):
            if i >= j:
                continue
            dist = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
            if dist < width * 0.45 and rng.random() > 0.4:
                draw.line([(x1, y1), (x2, y2)], fill=line_color, width=max(1, width // 64))

    # Draw nodes on top
    r = max(2, width // 24)
    for i, (x, y) in enumerate(nodes):
        color = glow_color if i < 2 else node_color
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color, outline="#1a1a1a")


def make_banner(width=120, height=160):
    """120x160 banner for app header."""
    img = draw_brick_wall(width, height)
    draw = ImageDraw.Draw(img)

    # Neural overlay on upper portion
    draw_neural_overlay(draw, width, height - 40, seed=42)

    # Dark overlay at bottom for text
    overlay = Image.new("RGBA", (width, 44), (0, 0, 0, 180))
    img_rgba = img.convert("RGBA")
    img_rgba.paste(overlay, (0, height - 44), overlay)
    img = img_rgba.convert("RGB")
    draw = ImageDraw.Draw(img)

    # "BE" initials (BigEd)
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except Exception:
        font = ImageFont.load_default()

    text = "BE"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (width - tw) // 2
    ty = height - 40 + (36 - th) // 2
    draw.text((tx + 1, ty + 1), text, fill="#000000", font=font)
    draw.text((tx, ty), text, fill="#c8a84b", font=font)

    return img


def make_icon(size=64):
    """Square icon — brick wall + neural network overlay + gold border."""
    img = draw_brick_wall(size, size, seed=7)
    draw = ImageDraw.Draw(img)

    # Neural network overlay
    draw_neural_overlay(draw, size, size, seed=42)

    # Gold border with slight glow effect
    border_w = max(1, size // 24)
    draw.rectangle([0, 0, size - 1, size - 1], outline="#B8860B", width=border_w)

    # Inner border accent
    if size >= 48:
        draw.rectangle(
            [border_w, border_w, size - 1 - border_w, size - 1 - border_w],
            outline="#8B6914", width=1,
        )

    # "B" letter centered for larger sizes
    if size >= 48:
        try:
            font_size = size // 3
            font = ImageFont.truetype("arial.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()
        text = "B"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = (size - tw) // 2
        ty = (size - th) // 2 - size // 16
        # Shadow
        draw.text((tx + 1, ty + 1), text, fill="#000000", font=font)
        # Gold letter
        draw.text((tx, ty), text, fill="#c8a84b", font=font)

    return img


def main():
    out = Path(__file__).parent
    out.mkdir(exist_ok=True)

    # 120x160 banner PNG
    banner = make_banner(120, 160)
    banner.save(out / "brick_banner.png")
    print("Saved brick_banner.png (120x160)")

    # Multi-size .ico (16, 32, 48, 64, 128, 256)
    sizes = [16, 32, 48, 64, 128, 256]
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

    # Preview 256px for inspection
    make_icon(256).save(out / "brick_256.png")
    print("Saved brick_256.png (preview)")


if __name__ == "__main__":
    main()
