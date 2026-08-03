#!/usr/bin/env python3
"""Map every public Squarespace block in exact DOM order.

This report is the guardrail against pairing a title with the wrong video/image.
It groups visible text and media inside each Squarespace block and section while
also preserving header and footer assets.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

ROOT = Path("_source_snapshot")
RAW = ROOT / "raw"
OUT = ROOT / "manifest" / "blocks"

YT_RE = re.compile(
    r"(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=)|youtu\.be/)([A-Za-z0-9_-]{11})",
    re.I,
)
YT_JSON_RE = re.compile(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"')
VIMEO_RE = re.compile(r"vimeo\.com/(?:video/)?(\d+)", re.I)
URL_RE = re.compile(r"https?:\\?/\\?/[^\"'<>\\s)]+", re.I)
MEDIA_RE = re.compile(r"\.(?:mp4|mov|m4v|webm|m3u8|jpg|jpeg|png|gif|webp|svg)(?:[?#]|$)", re.I)


def clean(value: str) -> str:
    return " ".join((value or "").split())


def normalize_url(value: str | None, base: str) -> str | None:
    if not value:
        return None
    value = value.strip().replace("\\/", "/").replace("&amp;", "&")
    if value.startswith("//"):
        value = "https:" + value
    absolute = urljoin(base, value)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return None
    return urlunparse(parsed._replace(fragment=""))


def element_urls(element: Tag, base: str) -> list[str]:
    urls: list[str] = []
    for node in element.find_all(True):
        for attr in (
            "src",
            "data-src",
            "data-image",
            "data-url",
            "data-video-url",
            "data-native-video",
            "data-video",
            "data-poster",
            "poster",
            "href",
        ):
            normalized = normalize_url(node.get(attr), base)
            if normalized:
                urls.append(normalized)
        for attr in ("srcset", "data-srcset"):
            for candidate in (node.get(attr) or "").split(","):
                normalized = normalize_url(candidate.strip().split(" ")[0], base)
                if normalized:
                    urls.append(normalized)
    raw = str(element)
    for match in URL_RE.findall(raw):
        normalized = normalize_url(match.rstrip("\\"), base)
        if normalized:
            urls.append(normalized)
    return list(dict.fromkeys(urls))


def media_type(url: str) -> str:
    lower = url.lower()
    if "youtube" in lower or "youtu.be" in lower:
        return "youtube"
    if "vimeo" in lower:
        return "vimeo"
    if re.search(r"\.(mp4|mov|m4v|webm|m3u8)(?:[?#]|$)", lower):
        return "native-video"
    if re.search(r"\.(jpg|jpeg|png|gif|webp|svg)(?:[?#]|$)", lower):
        return "image"
    return "link"


def direct_text_nodes(block: Tag) -> list[dict[str, str]]:
    ordered: list[dict[str, str]] = []
    for element in block.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "figcaption"]):
        text = clean(element.get_text(" ", strip=True))
        if text:
            ordered.append({"tag": element.name, "text": text})
    return ordered


def summarize_element(element: Tag, base: str, order: int) -> dict[str, Any]:
    html = str(element).replace("\\/", "/")
    urls = element_urls(element, base)
    images: list[dict[str, Any]] = []
    for image in element.find_all("img"):
        candidates: list[str] = []
        for attr in ("src", "data-src", "data-image", "data-original"):
            normalized = normalize_url(image.get(attr), base)
            if normalized:
                candidates.append(normalized)
        for attr in ("srcset", "data-srcset"):
            for candidate in (image.get(attr) or "").split(","):
                normalized = normalize_url(candidate.strip().split(" ")[0], base)
                if normalized:
                    candidates.append(normalized)
        images.append(
            {
                "sources": list(dict.fromkeys(candidates)),
                "alt": clean(image.get("alt", "")),
                "title": clean(image.get("title", "")),
            }
        )

    youtube_ids = list(dict.fromkeys(YT_RE.findall(html) + YT_JSON_RE.findall(html)))
    vimeo_ids = list(dict.fromkeys(VIMEO_RE.findall(html)))
    media = [{"url": url, "type": media_type(url)} for url in urls if MEDIA_RE.search(urlparse(url).path) or media_type(url) in {"youtube", "vimeo"}]

    return {
        "order": order,
        "tag": element.name,
        "id": element.get("id", ""),
        "classes": element.get("class", []),
        "data_block_type": element.get("data-block-type", ""),
        "data_definition_name": element.get("data-definition-name", ""),
        "data_section_id": element.get("data-section-id", ""),
        "texts_in_order": direct_text_nodes(element),
        "images_in_order": images,
        "youtube_ids": youtube_ids,
        "vimeo_ids": vimeo_ids,
        "media_urls": media,
        "all_urls": urls,
    }


def unique_direct_blocks(section: Tag) -> list[Tag]:
    blocks = section.select(".sqs-block")
    output: list[Tag] = []
    seen: set[int] = set()
    for block in blocks:
        marker = id(block)
        if marker in seen:
            continue
        # Exclude nested sqs-blocks where a parent sqs-block is already present.
        if block.find_parent(class_="sqs-block") is not None:
            continue
        seen.add(marker)
        output.append(block)
    return output


def page_url_from_soup(soup: BeautifulSoup) -> str:
    canonical = soup.find("link", rel="canonical")
    if canonical and canonical.get("href"):
        return canonical.get("href")
    og = soup.find("meta", property="og:url")
    if og and og.get("content"):
        return og.get("content")
    return "https://www.carpiomedia.com/"


def build_file(path: Path) -> dict[str, Any]:
    html = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    base = page_url_from_soup(soup)

    header = soup.find("header")
    footer = soup.find("footer")
    main = soup.find("main") or soup.find(id="page") or soup.body

    sections: list[dict[str, Any]] = []
    if isinstance(main, Tag):
        found_sections = main.select("section.page-section")
        if not found_sections:
            found_sections = [main]
        for section_order, section in enumerate(found_sections, start=1):
            blocks = [
                summarize_element(block, base, block_order)
                for block_order, block in enumerate(unique_direct_blocks(section), start=1)
            ]
            sections.append(
                {
                    "order": section_order,
                    "id": section.get("id", ""),
                    "classes": section.get("class", []),
                    "data_section_id": section.get("data-section-id", ""),
                    "background_media": [
                        {"url": url, "type": media_type(url)}
                        for url in element_urls(section, base)
                        if MEDIA_RE.search(urlparse(url).path)
                    ],
                    "blocks": blocks,
                }
            )

    return {
        "source_file": str(path),
        "url": base,
        "title": soup.title.get_text(" ", strip=True) if soup.title else "",
        "header": summarize_element(header, base, 1) if isinstance(header, Tag) else None,
        "sections": sections,
        "footer": summarize_element(footer, base, 1) if isinstance(footer, Tag) else None,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    index: list[dict[str, Any]] = []
    for path in sorted(RAW.glob("*.html")):
        if path.name.endswith("__q.html"):
            continue
        result = build_file(path)
        out_path = OUT / f"{path.stem}.json"
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        index.append(
            {
                "url": result["url"],
                "title": result["title"],
                "file": str(out_path),
                "sections": len(result["sections"]),
                "blocks": sum(len(section["blocks"]) for section in result["sections"]),
            }
        )
    (OUT / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
