#!/usr/bin/env python3
"""Resolve every Squarespace Alexandria native-video template exactly.

Squarespace exposes each uploaded native video through the block's alexandriaUrl.
Replacing the {variant} token with playlist.m3u8 preserves the original asset and
lets the one-page build use the same source file without substituting media.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

ROOT = Path("_source_snapshot")
RAW = ROOT / "raw"
OUT = ROOT / "manifest" / "native-videos.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125 Safari/537.36"


def probe_once(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            sample = response.read(200).decode("utf-8", errors="replace")
            return {
                "ok": 200 <= response.status < 400,
                "status": response.status,
                "final_url": response.geturl(),
                "content_type": response.headers.get("Content-Type", ""),
                "sample": sample,
            }
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "final_url": url, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": 0, "final_url": url, "error": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()

    for path in sorted(RAW.glob("*.html")):
        soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "lxml")
        canonical = soup.find("link", rel="canonical")
        page_url = canonical.get("href") if canonical and canonical.get("href") else ""

        for order, node in enumerate(soup.select(".sqs-native-video[data-config-video]"), start=1):
            try:
                config = json.loads(node.get("data-config-video", "{}"))
            except json.JSONDecodeError:
                continue

            system_id = config.get("systemDataId") or config.get("id") or ""
            unique_key = f"{page_url}|{system_id}"
            if unique_key in seen:
                continue
            seen.add(unique_key)

            block = node.find_parent(class_="sqs-block-video")
            caption = ""
            if block:
                caption_node = block.select_one(".video-caption")
                if caption_node:
                    caption = " ".join(caption_node.get_text(" ", strip=True).split())

            template = config.get("alexandriaUrl", "")
            playlist_url = template.replace("{variant}", "playlist.m3u8") if template else ""
            verification = probe_once(playlist_url) if playlist_url else {
                "ok": False,
                "status": 0,
                "error": "No alexandriaUrl in source block",
            }

            records.append(
                {
                    "page_url": page_url,
                    "source_file": str(path),
                    "order_on_page": order,
                    "block_id": block.get("id", "") if block else "",
                    "caption": caption,
                    "config": config,
                    "resolved_url": playlist_url,
                    "verification": verification,
                }
            )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
