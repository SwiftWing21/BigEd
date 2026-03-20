"""
BigEd CC — Icon and banner generator.

Design language:
  - TOP: Neural network (AI/fleet intelligence) — nodes, connections, data flow
  - BOTTOM: Brick wall (security foundation) — SOC 2, DLP, access control
  - TEXT: "BE" or "BigEd" — brand identity
  - COLORS: Gold nodes, green accents, dark red bricks, dark background

Usage: python generate_icon.py
"""
import math
import random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def draw_brick_wall(draw, width, y_start, y_end, seed=7):
    """Draw brick wall in the LOWER portion — security foundation."""
    rng = random.Random(seed)

    bw = max(12, width // 8)
    bh = max(6, (y_end - y_start) // 8)
    mortar = max(1, width // 64)
    mortar_color = "#2d2d2d"

    brick_palette = [
        "#8B1A1A", "#9B2020", "#7B1010",
        "#A02828", "#8A1818", "#6B0F0F",
        "#952020", "#7A1515",
    ]

    # Fill mortar base in brick region only
    draw.rectangle([0, y_start, width - 1, y_end - 1], fill=mortar_color)

    row = 0
    y = y_start
    while y < y_end:
        offset = (bw // 2) if row % 2 else 0
        x = -offset
        while x < width:
            color = brick_palette[rng.randint(0, len(brick_palette) - 1)]
            x1 = x + mortar
            y1 = y + mortar
            x2 = x + bw - mortar - 1
            y2 = y + bh - mortar - 1
            if x2 > x1 and y2 > y1 and y1 >= y_start:
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


def draw_network(draw, width, height, seed=42):
    """Draw neural network in the UPPER portion — AI intelligence."""
    rng = random.Random(seed)
    node_color = "#c8a84b"
    line_color = "#5a4a1a"
    glow_color = "#4caf50"

    # Place nodes in upper region
    nodes = []
    margin = width // 8
    n_nodes = max(8, width // 14)
    for _ in range(n_nodes):
        x = rng.randint(margin, width - margin)
        y = rng.randint(margin, height - margin)
        nodes.append((x, y))

    # Draw connections (thinner, more subtle)
    for i, (x1, y1) in enumerate(nodes):
        for j, (x2, y2) in enumerate(nodes):
            if i >= j:
                continue
            dist = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
            if dist < width * 0.5 and rng.random() > 0.5:
                draw.line([(x1, y1), (x2, y2)], fill=line_color,
                         width=max(1, width // 80))

    # Draw nodes
    r = max(2, width // 28)
    for i, (x, y) in enumerate(nodes):
        # First 3 nodes are green (active agents)
        color = glow_color if i < 3 else node_color
        # Outer glow for active nodes
        if i < 3 and width >= 64:
            draw.ellipse([x - r - 2, y - r - 2, x + r + 2, y + r + 2],
                        fill=None, outline="#2a5a2a", width=1)
        draw.ellipse([x - r, y - r, x + r, y + r],
                    fill=color, outline="#1a1a1a")


def make_icon(size=64):
    """Square icon: network top + brick wall bottom + BE text."""
    img = Image.new("RGB", (size, size), "#1a1a1a")
    draw = ImageDraw.Draw(img)

    # Split: upper 55% = network, lower 45% = bricks
    split = int(size * 0.55)

    # Draw network in upper portion
    draw_network(draw, size, split, seed=42)

    # Draw bricks in lower portion
    draw_brick_wall(draw, size, split, size, seed=7)

    # Horizontal divider line (gold) at the split
    draw.line([(0, split), (size, split)], fill="#c8a84b", width=max(1, size // 32))

    # Gold border
    border_w = max(1, size // 24)
    draw.rectangle([0, 0, size - 1, size - 1], outline="#B8860B", width=border_w)

    # "BE" text centered
    if size >= 48:
        try:
            font_size = size // 3
            font = ImageFont.truetype("arial.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

        text = "BE"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = (size - tw) // 2
        ty = (size - th) // 2 - size // 20

        # Dark shadow for readability
        for dx, dy in [(-1, -1), (-1, 1), (1, -1), (1, 1), (0, 2)]:
            draw.text((tx + dx, ty + dy), text, fill="#000000", font=font)
        # Gold text
        draw.text((tx, ty), text, fill="#c8a84b", font=font)

    return img


def make_banner(width=120, height=160):
    """120x160 banner: network top + bricks bottom + BigEd text."""
    img = Image.new("RGB", (width, height), "#1a1a1a")
    draw = ImageDraw.Draw(img)

    # Split: upper 60% = network, lower 40% = bricks
    split = int(height * 0.60)

    # Network in upper portion
    draw_network(draw, width, split - 10, seed=42)

    # Bricks in lower portion
    draw_brick_wall(draw, width, split, height, seed=7)

    # Gold divider
    draw.line([(0, split), (width, split)], fill="#c8a84b", width=2)

    # Dark overlay for text area
    overlay = Image.new("RGBA", (width, 36), (0, 0, 0, 200))
    img_rgba = img.convert("RGBA")
    img_rgba.paste(overlay, (0, split - 36), overlay)
    img = img_rgba.convert("RGB")
    draw = ImageDraw.Draw(img)

    # "BigEd" text at the split line
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
    draw.text((tx, ty), text, fill="#c8a84b", font=font)

    # Small "CC" subtitle
    try:
        font_sm = ImageFont.truetype("arial.ttf", 10)
    except Exception:
        font_sm = ImageFont.load_default()
    draw.text((tx + tw + 4, ty + 8), "CC", fill="#888888", font=font_sm)

    return img


def main():
    out = Path(__file__).parent
    out.mkdir(exist_ok=True)

    banner = make_banner(120, 160)
    banner.save(out / "brick_banner.png")
    print("Saved brick_banner.png (120x160)")

    sizes = [16, 32, 48, 64, 128, 256]
    icons = [make_icon(s) for s in sizes]
    icons[0].save(
        out / "brick.ico",
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=icons[1:],
    )
    print("Saved brick.ico (multi-size)")

    make_icon(64).save(out / "brick_64.png")
    print("Saved brick_64.png")

    make_icon(256).save(out / "brick_256.png")
    print("Saved brick_256.png (preview)")


if __name__ == "__main__":
    main()
