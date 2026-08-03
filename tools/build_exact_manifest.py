#!/usr/bin/env python3
"""Build compact, human-auditable manifests from the raw site snapshot."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path("_source_snapshot")
INVENTORY = ROOT / "inventory.json"
OUT = ROOT / "manifest"
PAGES = OUT / "pages"


def choose_image_url(item: dict[str, Any]) -> str:
    sources = item.get("sources") or []
    if not sources:
        return ""
    # Prefer an original Squarespace URL without resize query parameters.
    clean = [u for u in sources if "images.squarespace-cdn.com" in u and "format=" not in u]
    return (clean or sources)[0]


def clean_text(value: str) -> str:
    return " ".join((value or "").split())


def classify_url(url: str) -> str:
    lowered = url.lower()
    if "youtube" in lowered or "youtu.be" in lowered:
        return "youtube"
    if "vimeo" in lowered:
        return "vimeo"
    if re.search(r"\.(mp4|mov|m4v|webm|m3u8)(?:[?#]|$)", lowered):
        return "native-video"
    if re.search(r"\.(jpg|jpeg|png|gif|webp|svg)(?:[?#]|$)", lowered):
        return "image"
    return "other"


def page_slug(url: str) -> str:
    path = url.split("?", 1)[0].replace("https://www.carpiomedia.com", "").strip("/")
    return re.sub(r"[^a-zA-Z0-9._-]+", "__", path or "root")


def dedupe_dicts(items: list[dict[str, Any]], key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    output: list[dict[str, Any]] = []
    for item in items:
        key = tuple(json.dumps(item.get(field), sort_keys=True, default=str) for field in key_fields)
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def main() -> None:
    data = json.loads(INVENTORY.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    PAGES.mkdir(parents=True, exist_ok=True)

    site_summary: dict[str, Any] = {
        "source_of_truth": data.get("source_of_truth"),
        "generated_utc": data.get("generated_utc"),
        "youtube_metadata": data.get("youtube_metadata", {}),
        "pages": [],
    }

    unique_pages: dict[str, dict[str, Any]] = {}
    for page in data.get("pages", []):
        unique_pages[page.get("final_url") or page.get("requested_url")] = page

    for url, page in unique_pages.items():
        images = []
        for index, image in enumerate(page.get("images_in_dom_order", []), start=1):
            selected = choose_image_url(image)
            images.append(
                {
                    "dom_index": index,
                    "url": selected,
                    "all_sources": image.get("sources", []),
                    "alt": clean_text(image.get("alt", "")),
                    "title": clean_text(image.get("title", "")),
                    "data_image_id": image.get("data_image_id", ""),
                    "ancestry": image.get("ancestry", []),
                    "context": image.get("context", {}),
                }
            )

        media = []
        for index, embed in enumerate(page.get("embeds_in_dom_order", []), start=1):
            for media_url in embed.get("urls", []):
                media.append(
                    {
                        "dom_index": index,
                        "tag": embed.get("tag"),
                        "url": media_url,
                        "type": classify_url(media_url),
                        "attributes": embed.get("attributes", {}),
                        "ancestry": embed.get("ancestry", []),
                        "context": embed.get("context", {}),
                    }
                )

        for media_url in page.get("media_urls_found_in_source", []):
            media.append(
                {
                    "dom_index": None,
                    "tag": "source-regex",
                    "url": media_url,
                    "type": classify_url(media_url),
                    "attributes": {},
                    "ancestry": [],
                    "context": {},
                }
            )

        media = dedupe_dicts(media, ("url", "tag", "dom_index"))

        links = []
        for index, link in enumerate(page.get("links_in_dom_order", []), start=1):
            links.append(
                {
                    "dom_index": index,
                    "text": clean_text(link.get("text", "")),
                    "href": link.get("href", ""),
                    "aria_label": clean_text(link.get("aria_label", "")),
                    "classes": link.get("classes", []),
                    "ancestry": link.get("ancestry", []),
                }
            )

        compact = {
            "url": url,
            "title": page.get("title", ""),
            "meta": page.get("meta", {}),
            "visible_blocks_in_order": page.get("visible_blocks_in_order", []),
            "links_in_dom_order": links,
            "images_in_dom_order": images,
            "media_in_dom_and_source_order": media,
            "youtube_ids": page.get("youtube_ids", []),
            "youtube_metadata": {
                video_id: data.get("youtube_metadata", {}).get(video_id, {})
                for video_id in page.get("youtube_ids", [])
            },
            "vimeo_ids": page.get("vimeo_ids", []),
            "squarespace_media_urls": page.get("squarespace_media_urls_found_in_source", []),
        }

        slug = page_slug(url)
        path = PAGES / f"{slug}.json"
        path.write_text(json.dumps(compact, indent=2, ensure_ascii=False), encoding="utf-8")

        site_summary["pages"].append(
            {
                "url": url,
                "title": page.get("title", ""),
                "manifest": str(path),
                "visible_blocks": len(compact["visible_blocks_in_order"]),
                "images": len(images),
                "media": len(media),
                "youtube_ids": compact["youtube_ids"],
            }
        )

    (OUT / "site-manifest.json").write_text(
        json.dumps(site_summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # A short Markdown audit is useful in GitHub and is intentionally factual only.
    lines = ["# Exact Carpio Media source audit", "", f"Source: {site_summary['source_of_truth']}", ""]
    for page in site_summary["pages"]:
        lines.extend(
            [
                f"## {page['title']}",
                f"- URL: {page['url']}",
                f"- Visible blocks: {page['visible_blocks']}",
                f"- Images: {page['images']}",
                f"- Media URLs: {page['media']}",
                f"- YouTube IDs: {', '.join(page['youtube_ids']) or 'None'}",
                f"- Manifest: `{page['manifest']}`",
                "",
            ]
        )
    (OUT / "AUDIT.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
