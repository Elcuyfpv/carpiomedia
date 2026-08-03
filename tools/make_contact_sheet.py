#!/usr/bin/env python3
"""Create full and compact contact sheets from rendered audit screenshots."""

from __future__ import annotations

import base64
import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path("_source_snapshot/visual-audit")
SHOTS = ROOT / "shots.json"


def encode_file(source: Path, destination: Path) -> None:
    encoded = base64.b64encode(source.read_bytes()).decode("ascii")
    destination.write_text("\n".join(textwrap.wrap(encoded, 76)) + "\n", encoding="ascii")


def build_sheet(cards, columns: int, card_w: int, card_h: int, thumb_size, output: Path, quality: int) -> None:
    rows = (len(cards) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * card_w, rows * card_h), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()

    for index, (label, original) in enumerate(cards):
        image = original.copy()
        image.thumbnail(thumb_size, Image.Resampling.LANCZOS)
        col = index % columns
        row = index // columns
        x = col * card_w + max(4, (card_w - image.width) // 2)
        y = row * card_h + 18
        draw.text((col * card_w + 5, row * card_h + 3), label, fill="black", font=font)
        sheet.paste(image, (x, y))

    sheet.save(output, "JPEG", quality=quality, optimize=True)


def main() -> None:
    data = json.loads(SHOTS.read_text(encoding="utf-8"))
    files = [Path(item) for item in data["desktopShots"] + data["mobileShots"]]
    cards = []
    for file in files:
        with Image.open(file) as source:
            cards.append((file.stem, source.convert("RGB").copy()))

    full = ROOT / "contact-sheet.jpg"
    build_sheet(cards, columns=2, card_w=590, card_h=440, thumb_size=(560, 390), output=full, quality=58)
    encode_file(full, ROOT / "contact-sheet.b64")

    tiny = ROOT / "contact-sheet-tiny.jpg"
    build_sheet(cards, columns=2, card_w=240, card_h=180, thumb_size=(228, 150), output=tiny, quality=24)
    encode_file(tiny, ROOT / "contact-sheet-tiny.b64")


if __name__ == "__main__":
    main()
