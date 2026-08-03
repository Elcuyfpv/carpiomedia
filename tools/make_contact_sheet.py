#!/usr/bin/env python3
"""Create a compact contact sheet from rendered audit screenshots."""

from __future__ import annotations

import base64
import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path("_source_snapshot/visual-audit")
SHOTS = ROOT / "shots.json"
OUTPUT = ROOT / "contact-sheet.jpg"
ENCODED = ROOT / "contact-sheet.b64"


def main() -> None:
    data = json.loads(SHOTS.read_text(encoding="utf-8"))
    files = [Path(item) for item in data["desktopShots"] + data["mobileShots"]]
    cards = []
    for file in files:
        with Image.open(file) as source:
            image = source.convert("RGB")
            image.thumbnail((560, 390), Image.Resampling.LANCZOS)
            cards.append((file.stem, image.copy()))

    columns = 2
    card_w, card_h = 590, 440
    rows = (len(cards) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * card_w, rows * card_h), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()

    for index, (label, image) in enumerate(cards):
        col = index % columns
        row = index // columns
        x = col * card_w + 15
        y = row * card_h + 30
        draw.text((x, 8 + row * card_h), label, fill="black", font=font)
        sheet.paste(image, (x, y))

    sheet.save(OUTPUT, "JPEG", quality=58, optimize=True)
    encoded = base64.b64encode(OUTPUT.read_bytes()).decode("ascii")
    ENCODED.write_text("\n".join(textwrap.wrap(encoded, 76)) + "\n", encoding="ascii")


if __name__ == "__main__":
    main()
