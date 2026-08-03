#!/usr/bin/env python3
"""Create an exact public-source inventory of carpiomedia.com.

The inventory preserves page order, visible copy, links, image URLs, native video
URLs, iframe sources, YouTube/Vimeo identifiers, Squarespace block metadata, and
footer assets. It is intentionally extraction-only: it does not rewrite copy or
infer which media belongs to which section.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

BASE = "https://www.carpiomedia.com"
OUT = Path("_source_snapshot")
RAW = OUT / "raw"
JSON_DIR = OUT / "json"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
)

KNOWN_PATHS = [
    "/",
    "/home",
    "/services-nyc-media",
    "/services-nyc-media/residentialrealestatephotography",
    "/services-nyc-media/commercialrealestatephotography",
    "/services-nyc-media/professional-visual-content-creation",
    "/services-nyc-media/fpv",
    "/services-nyc-media/cinematic-drone-content-creation-for-film-tv",
    "/aerial-drone-gallery-nyc",
    "/gallery",
    "/contact",
    "/store-policies",
]

URL_RE = re.compile(r"https?:\\?/\\?/[^\"'<>\\s)]+", re.I)
YT_PATTERNS = [
    re.compile(r"(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=)|youtu\.be/)([A-Za-z0-9_-]{11})", re.I),
    re.compile(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"'),
]
VIMEO_RE = re.compile(r"vimeo\.com/(?:video/)?(\d+)", re.I)
MEDIA_EXT_RE = re.compile(r"\.(?:mp4|mov|m4v|webm|m3u8|jpg|jpeg|png|gif|webp|svg)(?:[?#]|$)", re.I)


def fetch(url: str, timeout: int = 45) -> tuple[int, str, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            return response.status, raw.decode(charset, errors="replace"), response.geturl()
    except Exception as exc:  # noqa: BLE001 - preserve failure in inventory
        return 0, f"FETCH_ERROR: {type(exc).__name__}: {exc}", url


def slug_for(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.strip("/") or "root"
    path = re.sub(r"[^A-Za-z0-9._-]+", "__", path)
    if parsed.query:
        digest = hashlib.sha1(parsed.query.encode()).hexdigest()[:10]
        path += f"__q_{digest}"
    return path


def canonicalize(url: str, base: str = BASE) -> str | None:
    if not url:
        return None
    url = url.strip().replace("\\/", "/").replace("&amp;", "&")
    if url.startswith("//"):
        url = "https:" + url
    absolute = urllib.parse.urljoin(base, url)
    parsed = urllib.parse.urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return None
    return urllib.parse.urlunparse(parsed._replace(fragment=""))


def sitemap_urls() -> set[str]:
    found: set[str] = set()
    queue = [f"{BASE}/sitemap.xml"]
    seen: set[str] = set()
    while queue and len(seen) < 20:
        sitemap = queue.pop(0)
        if sitemap in seen:
            continue
        seen.add(sitemap)
        status, body, _ = fetch(sitemap)
        if status != 200:
            continue
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            continue
        locs = [el.text.strip() for el in root.iter() if el.tag.endswith("loc") and el.text]
        for loc in locs:
            if loc.endswith(".xml"):
                queue.append(loc)
            elif urllib.parse.urlparse(loc).netloc.endswith("carpiomedia.com"):
                found.add(loc)
    return found


def ordered_visible_blocks(soup: BeautifulSoup) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    selectors = "h1,h2,h3,h4,h5,h6,p,li,blockquote,figcaption"
    for element in soup.select(selectors):
        text = " ".join(element.get_text(" ", strip=True).split())
        if not text:
            continue
        # Skip repeated navigation boilerplate while preserving actual headings/copy.
        if text in {"Skip to Content", "Open Menu Close Menu"}:
            continue
        blocks.append({"tag": element.name, "text": text})
    return blocks


def ancestry(element: Tag) -> list[str]:
    parts: list[str] = []
    current: Tag | None = element
    for _ in range(6):
        if current is None or not isinstance(current, Tag):
            break
        ident = current.name
        if current.get("id"):
            ident += f"#{current.get('id')}"
        classes = current.get("class") or []
        if classes:
            ident += "." + ".".join(classes[:4])
        parts.append(ident)
        current = current.parent if isinstance(current.parent, Tag) else None
    return parts


def nearest_text(element: Tag) -> dict[str, str]:
    result: dict[str, str] = {}
    for direction, sibling in (("previous", element.find_previous()), ("next", element.find_next())):
        if isinstance(sibling, Tag):
            text = " ".join(sibling.get_text(" ", strip=True).split())
            if text and len(text) <= 500:
                result[direction] = text
    parent = element.parent
    if isinstance(parent, Tag):
        text = " ".join(parent.get_text(" ", strip=True).split())
        if text and len(text) <= 1000:
            result["container_text"] = text
    return result


def extract_page(url: str, html: str, final_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""

    links: list[dict[str, Any]] = []
    for a in soup.find_all("a"):
        href = canonicalize(a.get("href", ""), final_url)
        if not href:
            continue
        links.append(
            {
                "href": href,
                "text": " ".join(a.get_text(" ", strip=True).split()),
                "aria_label": a.get("aria-label", ""),
                "classes": a.get("class", []),
                "ancestry": ancestry(a),
            }
        )

    images: list[dict[str, Any]] = []
    for img in soup.find_all("img"):
        sources: list[str] = []
        for attr in ("src", "data-src", "data-image", "data-original", "data-load"):
            value = img.get(attr)
            normalized = canonicalize(value, final_url) if value else None
            if normalized:
                sources.append(normalized)
        for attr in ("srcset", "data-srcset"):
            value = img.get(attr, "")
            for candidate in value.split(","):
                candidate_url = candidate.strip().split(" ")[0]
                normalized = canonicalize(candidate_url, final_url)
                if normalized:
                    sources.append(normalized)
        sources = list(dict.fromkeys(sources))
        images.append(
            {
                "sources": sources,
                "alt": img.get("alt", ""),
                "title": img.get("title", ""),
                "data_image_id": img.get("data-image-id", ""),
                "classes": img.get("class", []),
                "ancestry": ancestry(img),
                "context": nearest_text(img),
            }
        )

    embeds: list[dict[str, Any]] = []
    for element in soup.find_all(["iframe", "video", "source", "embed", "object"]):
        urls: list[str] = []
        for attr in ("src", "data-src", "data-url", "data-video-url", "data-native-video", "data-video"):
            value = element.get(attr)
            normalized = canonicalize(value, final_url) if value else None
            if normalized:
                urls.append(normalized)
        if element.name == "object":
            normalized = canonicalize(element.get("data", ""), final_url)
            if normalized:
                urls.append(normalized)
        embeds.append(
            {
                "tag": element.name,
                "urls": list(dict.fromkeys(urls)),
                "attributes": {str(k): v for k, v in element.attrs.items()},
                "ancestry": ancestry(element),
                "context": nearest_text(element),
            }
        )

    raw_urls: list[str] = []
    for match in URL_RE.findall(html):
        normalized = canonicalize(match, final_url)
        if normalized:
            raw_urls.append(normalized.rstrip("\\"))
    raw_urls = list(dict.fromkeys(raw_urls))

    media_urls = [u for u in raw_urls if MEDIA_EXT_RE.search(urllib.parse.urlparse(u).path)]
    square_media = [
        u
        for u in raw_urls
        if any(host in urllib.parse.urlparse(u).netloc for host in (
            "squarespace-cdn.com",
            "static1.squarespace.com",
            "static.squarespace.com",
            "videos.squarespace-cdn.com",
        ))
    ]

    youtube_ids: list[str] = []
    for pattern in YT_PATTERNS:
        youtube_ids.extend(pattern.findall(html.replace("\\/", "/")))
    youtube_ids = list(dict.fromkeys(youtube_ids))
    vimeo_ids = list(dict.fromkeys(VIMEO_RE.findall(html.replace("\\/", "/"))))

    scripts: list[dict[str, Any]] = []
    for index, script in enumerate(soup.find_all("script")):
        text = script.string or script.get_text() or ""
        interesting = any(
            token in text.lower()
            for token in ("youtube", "vimeo", ".mp4", "native-video", "squarespace_context", "videoid", "imageid")
        )
        if interesting:
            scripts.append(
                {
                    "index": index,
                    "src": canonicalize(script.get("src", ""), final_url),
                    "type": script.get("type", ""),
                    "id": script.get("id", ""),
                    "text": text,
                }
            )

    return {
        "requested_url": url,
        "final_url": final_url,
        "title": title,
        "meta": {
            meta.get("name") or meta.get("property") or f"meta_{i}": meta.get("content", "")
            for i, meta in enumerate(soup.find_all("meta"))
            if meta.get("content")
        },
        "visible_blocks_in_order": ordered_visible_blocks(soup),
        "links_in_dom_order": links,
        "images_in_dom_order": images,
        "embeds_in_dom_order": embeds,
        "youtube_ids": youtube_ids,
        "vimeo_ids": vimeo_ids,
        "media_urls_found_in_source": media_urls,
        "squarespace_media_urls_found_in_source": square_media,
        "interesting_scripts": scripts,
    }


def youtube_metadata(video_ids: set[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for video_id in sorted(video_ids):
        watch = f"https://www.youtube.com/watch?v={video_id}"
        endpoint = "https://www.youtube.com/oembed?" + urllib.parse.urlencode(
            {"url": watch, "format": "json"}
        )
        status, body, _ = fetch(endpoint)
        if status == 200:
            try:
                result[video_id] = json.loads(body)
            except json.JSONDecodeError:
                result[video_id] = {"status": status, "raw": body}
        else:
            result[video_id] = {"status": status, "error": body}
        time.sleep(0.1)
    return result


def main() -> int:
    OUT.mkdir(exist_ok=True)
    RAW.mkdir(exist_ok=True)
    JSON_DIR.mkdir(exist_ok=True)

    urls = {urllib.parse.urljoin(BASE, path) for path in KNOWN_PATHS}
    urls.update(sitemap_urls())
    # HTML pages only; keep store/product collections if publicly listed in sitemap.
    urls = {u for u in urls if urllib.parse.urlparse(u).netloc.endswith("carpiomedia.com")}

    pages: list[dict[str, Any]] = []
    all_youtube: set[str] = set()
    fetch_log: list[dict[str, Any]] = []

    for number, url in enumerate(sorted(urls), start=1):
        print(f"[{number}/{len(urls)}] {url}")
        status, html, final_url = fetch(url)
        slug = slug_for(url)
        fetch_log.append({"url": url, "status": status, "final_url": final_url})
        (RAW / f"{slug}.html").write_text(html, encoding="utf-8")
        if status == 200:
            page = extract_page(url, html, final_url)
            page["http_status"] = status
            pages.append(page)
            all_youtube.update(page["youtube_ids"])

        separator = "&" if "?" in url else "?"
        json_url = f"{url}{separator}format=json"
        json_status, json_body, json_final = fetch(json_url)
        fetch_log.append({"url": json_url, "status": json_status, "final_url": json_final})
        (JSON_DIR / f"{slug}.json").write_text(json_body, encoding="utf-8")
        # Also extract media IDs from the Squarespace JSON response as raw text.
        if json_status == 200:
            normalized_json = json_body.replace("\\/", "/")
            for pattern in YT_PATTERNS:
                all_youtube.update(pattern.findall(normalized_json))
        time.sleep(0.15)

    yt_meta = youtube_metadata(all_youtube)

    inventory = {
        "source_of_truth": BASE,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pages": pages,
        "youtube_metadata": yt_meta,
        "fetch_log": fetch_log,
    }
    (OUT / "inventory.json").write_text(
        json.dumps(inventory, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    summary = {
        "page_count": len(pages),
        "youtube_ids": sorted(all_youtube),
        "failed_fetches": [entry for entry in fetch_log if entry["status"] != 200],
        "pages": [
            {
                "url": page["final_url"],
                "title": page["title"],
                "images": len(page["images_in_dom_order"]),
                "embeds": len(page["embeds_in_dom_order"]),
                "youtube_ids": page["youtube_ids"],
                "vimeo_ids": page["vimeo_ids"],
            }
            for page in pages
        ],
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
