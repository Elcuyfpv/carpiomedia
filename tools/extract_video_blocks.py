#!/usr/bin/env python3
"""Extract complete configuration for every Squarespace video block."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

ROOT = Path("_source_snapshot")
RAW = ROOT / "raw"
OUT = ROOT / "manifest" / "video-blocks"


def clean(value: str) -> str:
    return " ".join((value or "").split())


def serialize_attrs(element: Tag) -> dict[str, object]:
    return {str(key): value for key, value in element.attrs.items()}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for path in sorted(RAW.glob("*.html")):
        html = path.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")
        canonical = soup.find("link", rel="canonical")
        base = canonical.get("href") if canonical and canonical.get("href") else "https://www.carpiomedia.com/"

        selectors = [
            ".sqs-block-video",
            "video",
            "[data-video-url]",
            "[data-native-video]",
            "[data-video-id]",
            "[data-playback-id]",
            "[data-config-url]",
            "[data-video-config]",
        ]
        elements: list[Tag] = []
        seen: set[int] = set()
        for selector in selectors:
            for element in soup.select(selector):
                marker = id(element)
                if marker in seen:
                    continue
                # For descendants of a video block, capture them in that block's node dump.
                parent_video_block = element.find_parent(class_="sqs-block-video")
                if parent_video_block is not None and "sqs-block-video" not in (element.get("class") or []):
                    continue
                seen.add(marker)
                elements.append(element)

        page_items = []
        for order, element in enumerate(elements, start=1):
            descendants = []
            for node in element.find_all(True):
                attrs = serialize_attrs(node)
                if attrs or node.name in {"video", "source", "iframe", "script"}:
                    descendants.append(
                        {
                            "tag": node.name,
                            "attrs": attrs,
                            "text": (node.string or "") if node.name == "script" else "",
                        }
                    )
            raw = str(element)
            urls = list(dict.fromkeys(re.findall(r"https?:\\?/\\?/[^\"'<>\\s)]+", raw, flags=re.I)))
            page_items.append(
                {
                    "order": order,
                    "tag": element.name,
                    "id": element.get("id", ""),
                    "classes": element.get("class", []),
                    "attrs": serialize_attrs(element),
                    "text": clean(element.get_text(" ", strip=True)),
                    "descendants": descendants,
                    "urls_in_raw_html": [url.replace("\\/", "/") for url in urls],
                    "raw_html": raw,
                }
            )

        if page_items:
            output = {"url": base, "source_file": str(path), "video_blocks": page_items}
            out_file = OUT / f"{path.stem}.json"
            out_file.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
            index.append({"url": base, "file": str(out_file), "video_blocks": len(page_items)})

    (OUT / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
