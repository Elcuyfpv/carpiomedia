#!/usr/bin/env python3
"""Resolve Squarespace Alexandria native-video templates to playable URLs."""

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


def probe(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-1023"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return {
                "ok": 200 <= response.status < 400,
                "status": response.status,
                "final_url": response.geturl(),
                "content_type": response.headers.get("Content-Type", ""),
                "content_length": response.headers.get("Content-Length", ""),
            }
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "final_url": url, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": 0, "final_url": url, "error": f"{type(exc).__name__}: {exc}"}


def candidate_urls(template: str, variants: str) -> list[str]:
    pairs = [part.strip() for part in variants.split(",") if part.strip()]
    candidates: list[str] = []
    for pair in pairs:
        width, _, height = pair.partition(":")
        replacements = [
            pair,
            f"{width}x{height}" if height else width,
            f"{width}w",
            width,
            f"{pair}.mp4",
            f"{width}x{height}.mp4" if height else f"{width}.mp4",
            f"{width}w.mp4",
        ]
        for replacement in replacements:
            candidates.append(template.replace("{variant}", replacement))
    candidates.extend(
        [
            template.replace("{variant}", "original"),
            template.replace("{variant}", "original.mp4"),
        ]
    )
    return list(dict.fromkeys(candidates))


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
                caption = " ".join(caption_node.get_text(" ", strip=True).split()) if caption_node else ""
            template = config.get("alexandriaUrl", "")
            attempts = []
            resolved = None
            if template:
                for candidate in candidate_urls(template, config.get("systemDataVariants", "")):
                    result = probe(candidate)
                    attempts.append({"candidate": candidate, **result})
                    content_type = result.get("content_type", "").lower()
                    if result.get("ok") and ("video" in content_type or result.get("final_url", "").lower().endswith((".mp4", ".m3u8"))):
                        resolved = result.get("final_url") or candidate
                        break
            records.append(
                {
                    "page_url": page_url,
                    "source_file": str(path),
                    "order_on_page": order,
                    "block_id": block.get("id", "") if block else "",
                    "caption": caption,
                    "config": config,
                    "resolved_url": resolved,
                    "attempts": attempts,
                }
            )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
