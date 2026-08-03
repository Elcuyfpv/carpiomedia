#!/usr/bin/env python3
"""Build a one-page Carpio Media site from the exact public Squarespace DOM.

No marketing copy, title, caption, image, video, logo, client mark, certification,
social link, or footer element is authored here. The script only consolidates the
existing public pages, rewrites same-site navigation to anchors, and converts the
existing Squarespace native-video configuration into browser-playable HLS.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

ROOT = Path("_source_snapshot")
RAW = ROOT / "raw"
OUTPUT = Path("exact-preview.html")
AUDIT = ROOT / "exact-build-audit.json"
BASE = "https://www.carpiomedia.com"

PAGE_ORDER = [
    ("/", "home"),
    ("/services-nyc-media", "services"),
    ("/services-nyc-media/residentialrealestatephotography", "residential-real-estate"),
    ("/services-nyc-media/commercialrealestatephotography", "commercial-real-estate"),
    ("/services-nyc-media/professional-visual-content-creation", "content-creation"),
    ("/services-nyc-media/fpv", "fpv"),
    ("/services-nyc-media/cinematic-drone-content-creation-for-film-tv", "film-tv"),
    ("/about", "about"),
    ("/aerial-drone-gallery-nyc", "gallery"),
    ("/contact", "contact"),
    ("/faq-1", "faq"),
]

ANCHOR_MAP = {
    "/": "#home",
    "/home": "#home",
    "/services-nyc-media": "#services",
    "/services-nyc-media/residentialrealestatephotography": "#residential-real-estate",
    "/services-nyc-media/commercialrealestatephotography": "#commercial-real-estate",
    "/services-nyc-media/professional-visual-content-creation": "#content-creation",
    "/services-nyc-media/fpv": "#fpv",
    "/services-nyc-media/cinematic-drone-content-creation-for-film-tv": "#film-tv",
    "/about": "#about",
    "/aerial-drone-gallery-nyc": "#gallery",
    "/gallery": "#gallery",
    "/contact": "#contact",
    "/faq-1": "#faq",
}

TEXT_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "figcaption"]
YT_ID_RE = re.compile(r"(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=)|youtu\.be/)([A-Za-z0-9_-]{11})", re.I)


def clean_text(value: str) -> str:
    return " ".join((value or "").split())


def canonical_url(soup: BeautifulSoup) -> str:
    link = soup.find("link", rel="canonical")
    if link and link.get("href"):
        return link.get("href")
    meta = soup.find("meta", property="og:url")
    if meta and meta.get("content"):
        return meta.get("content")
    return BASE + "/"


def load_pages() -> dict[str, tuple[Path, BeautifulSoup]]:
    pages: dict[str, tuple[Path, BeautifulSoup]] = {}
    for path in sorted(RAW.glob("*.html")):
        soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "lxml")
        url = canonical_url(soup)
        parsed = urlparse(url)
        normalized_path = parsed.path.rstrip("/") or "/"
        # Prefer root.html over duplicate home snapshots for the homepage.
        if normalized_path in pages and path.stem != "root":
            continue
        pages[normalized_path] = (path, soup)
    return pages


def find_main(soup: BeautifulSoup) -> Tag:
    candidates = [
        soup.find("main", id="page"),
        soup.find("main"),
        soup.find(id="page"),
    ]
    for candidate in candidates:
        if isinstance(candidate, Tag):
            return candidate
    raise RuntimeError(f"Main content not found for {canonical_url(soup)}")


def visible_texts(node: Tag) -> list[str]:
    output: list[str] = []
    for element in node.find_all(TEXT_TAGS):
        text = clean_text(element.get_text(" ", strip=True))
        if text and text not in {"Skip to Content", "Open Menu Close Menu"}:
            output.append(text)
    return output


def normalize_resource(value: str | None, page_url: str) -> str | None:
    if not value:
        return value
    stripped = value.strip().replace("\\/", "/")
    if stripped.startswith(("#", "mailto:", "tel:", "data:", "javascript:")):
        return stripped
    if stripped.startswith("//"):
        return "https:" + stripped
    absolute = urljoin(page_url, stripped)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return stripped
    return urlunparse(parsed._replace(fragment=parsed.fragment))


def rewrite_resources(node: Tag, page_url: str) -> None:
    for element in node.find_all(True):
        for attr in ("src", "data-src", "data-image", "poster", "data-poster", "action"):
            if element.get(attr):
                element[attr] = normalize_resource(element.get(attr), page_url)
        for attr in ("srcset", "data-srcset"):
            if not element.get(attr):
                continue
            candidates = []
            for candidate in element.get(attr).split(","):
                pieces = candidate.strip().split()
                if not pieces:
                    continue
                pieces[0] = normalize_resource(pieces[0], page_url) or pieces[0]
                candidates.append(" ".join(pieces))
            element[attr] = ", ".join(candidates)

        href = element.get("href")
        if href:
            normalized = normalize_resource(href, page_url) or href
            parsed = urlparse(normalized)
            if parsed.netloc.endswith("carpiomedia.com"):
                path = parsed.path.rstrip("/") or "/"
                if path in ANCHOR_MAP:
                    normalized = ANCHOR_MAP[path]
            element["href"] = normalized

        style = element.get("style")
        if style and "url(" in style:
            def repl(match: re.Match[str]) -> str:
                quote = match.group(1) or ""
                source = match.group(2)
                normalized = normalize_resource(source, page_url) or source
                return f"url({quote}{normalized}{quote})"
            element["style"] = re.sub(r"url\((['\"]?)(.*?)\1\)", repl, style)


def native_video_record(block: Tag, node: Tag) -> dict[str, Any]:
    config = json.loads(node.get("data-config-video", "{}"))
    template = config.get("alexandriaUrl", "")
    playlist = template.replace("{variant}", "playlist.m3u8") if template else ""
    caption_node = block.select_one(".video-caption")
    caption = clean_text(caption_node.get_text(" ", strip=True)) if caption_node else ""
    return {
        "block_id": block.get("id", ""),
        "caption": caption,
        "system_data_id": config.get("systemDataId", ""),
        "playlist": playlist,
        "aspect_ratio": config.get("aspectRatio"),
        "duration_seconds": config.get("durationSeconds"),
    }


def convert_native_videos(node: Tag, page_url: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    soup = node if isinstance(node, BeautifulSoup) else node
    for native in list(soup.select(".sqs-native-video[data-config-video]")):
        block = native.find_parent(class_="sqs-block-video") or native
        try:
            record = native_video_record(block, native)
        except json.JSONDecodeError:
            continue
        records.append(record)
        if not record["playlist"]:
            continue

        owner = native
        player = native.select_one(".native-video-player")
        if isinstance(player, Tag):
            owner = player

        video = BeautifulSoup("<video></video>", "html.parser").video
        video["class"] = ["exact-native-video"]
        video["controls"] = ""
        video["playsinline"] = ""
        video["preload"] = "metadata"
        video["data-hls-src"] = record["playlist"]
        video["data-native-id"] = record["system_data_id"]
        video["aria-label"] = record["caption"] or "Carpio Media video"

        poster = block.find("img") if isinstance(block, Tag) else None
        if isinstance(poster, Tag):
            poster_url = poster.get("data-src") or poster.get("src")
            if poster_url:
                video["poster"] = normalize_resource(poster_url, page_url)

        owner.clear()
        owner.append(video)
    return records


def source_youtube_ids(node: Tag) -> list[str]:
    return list(dict.fromkeys(YT_ID_RE.findall(str(node).replace("\\/", "/"))))


def collect_head_assets(pages: list[BeautifulSoup], template: BeautifulSoup) -> None:
    head = template.head
    if head is None:
        raise RuntimeError("Template has no head")

    signatures: set[str] = set()
    for existing in head.find_all(["link", "style", "script"]):
        signatures.add(hashlib.sha1(str(existing).encode()).hexdigest())

    for page in pages:
        if page.head is None:
            continue
        for element in page.head.find_all(["link", "style", "script"], recursive=False):
            # Page context JSON and analytics are collection-specific and unsafe to duplicate.
            if element.name == "script":
                script_type = element.get("type", "")
                script_id = element.get("id", "")
                text = element.string or ""
                if (
                    script_type == "application/ld+json"
                    or "Static.SQUARESPACE_CONTEXT" in text
                    or "websiteId" in text
                    or script_id.startswith("squarespace-")
                ):
                    continue
            signature = hashlib.sha1(str(element).encode()).hexdigest()
            if signature in signatures:
                continue
            signatures.add(signature)
            head.append(copy.copy(element))


def add_runtime_assets(template: BeautifulSoup) -> None:
    head = template.head
    body = template.body
    if head is None or body is None:
        raise RuntimeError("Template missing head or body")

    style = template.new_tag("style", id="carpio-one-page-bridge")
    style.string = """
html{scroll-behavior:smooth;scroll-padding-top:110px}
.onepage-anchor{position:relative;top:-1px;height:1px;overflow:hidden}
.exact-native-video{display:block;width:100%;height:100%;aspect-ratio:16/9;background:#000;object-fit:contain}
[data-onepage-source]{position:relative}
.header-menu-nav-folder-content a[href^='#'],.header-nav-list a[href^='#']{cursor:pointer}
@media(max-width:767px){html{scroll-padding-top:80px}}
"""
    head.append(style)

    hls_script = template.new_tag("script", src="https://cdn.jsdelivr.net/npm/hls.js@1.5.18/dist/hls.min.js")
    hls_script["defer"] = ""
    body.append(hls_script)

    runtime = template.new_tag("script", id="carpio-one-page-runtime")
    runtime.string = """
window.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('video[data-hls-src]').forEach(function (video) {
    var source = video.getAttribute('data-hls-src');
    if (!source) return;
    if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = source;
    } else if (window.Hls && window.Hls.isSupported()) {
      var hls = new window.Hls({enableWorker:true});
      hls.loadSource(source);
      hls.attachMedia(video);
    } else {
      var fallback = document.createElement('a');
      fallback.href = source;
      fallback.textContent = 'Open video';
      fallback.target = '_blank';
      video.replaceWith(fallback);
    }
  });
  document.querySelectorAll('a[href^="#"]').forEach(function (link) {
    link.addEventListener('click', function () {
      var menu = document.querySelector('.header-menu');
      if (menu) menu.classList.remove('is-open');
      document.body.classList.remove('header--menu-open');
    });
  });
});
"""
    body.append(runtime)


def segment_wrapper(template: BeautifulSoup, anchor: str, source_url: str, sections: list[Tag]) -> Tag:
    wrapper = template.new_tag("div")
    wrapper["class"] = ["onepage-source-segment"]
    wrapper["data-onepage-source"] = source_url

    marker = template.new_tag("div", id=anchor)
    marker["class"] = ["onepage-anchor"]
    marker["aria-hidden"] = "true"
    wrapper.append(marker)

    for section in sections:
        wrapper.append(copy.copy(section))
    return wrapper


def extract_sections(main: Tag) -> list[Tag]:
    sections = main.find_all("section", class_="page-section", recursive=False)
    if sections:
        return sections
    return [child for child in main.children if isinstance(child, Tag)]


def rewrite_header_navigation(template: BeautifulSoup) -> None:
    for link in template.select("header a[href], .header-menu a[href]"):
        href = link.get("href", "")
        normalized = normalize_resource(href, BASE + "/") or href
        parsed = urlparse(normalized)
        if parsed.netloc.endswith("carpiomedia.com"):
            path = parsed.path.rstrip("/") or "/"
            if path in ANCHOR_MAP:
                link["href"] = ANCHOR_MAP[path]


def main() -> int:
    pages = load_pages()
    missing = [path for path, _ in PAGE_ORDER if path not in pages]
    if missing:
        raise RuntimeError(f"Missing exact source pages: {missing}")

    template_path, root_soup = pages["/"]
    template = BeautifulSoup(template_path.read_text(encoding="utf-8", errors="replace"), "lxml")
    template_main = find_main(template)
    template_main.clear()

    ordered_soups = [pages[path][1] for path, _ in PAGE_ORDER]
    collect_head_assets(ordered_soups, template)

    source_text_by_page: dict[str, list[str]] = {}
    native_records: list[dict[str, Any]] = []
    source_youtube_by_page: dict[str, list[str]] = {}

    for path, anchor in PAGE_ORDER:
        source_path, source_soup = pages[path]
        source_url = BASE + (path if path != "/" else "/")
        source_main = find_main(source_soup)
        source_sections = extract_sections(source_main)
        source_text_by_page[path] = visible_texts(source_main)
        source_youtube_by_page[path] = source_youtube_ids(source_main)

        copied_sections: list[Tag] = []
        for section in source_sections:
            copied = copy.copy(section)
            rewrite_resources(copied, source_url)
            native_records.extend(convert_native_videos(copied, source_url))
            copied_sections.append(copied)

        template_main.append(segment_wrapper(template, anchor, source_url, copied_sections))

    rewrite_resources(template, BASE + "/")
    rewrite_header_navigation(template)
    add_runtime_assets(template)

    # Keep one exact footer—the footer already present in the homepage template.
    output_html = "<!doctype html>\n" + str(template)
    OUTPUT.write_text(output_html, encoding="utf-8")

    output_soup = BeautifulSoup(output_html, "lxml")
    output_texts = visible_texts(find_main(output_soup))
    output_youtube = source_youtube_ids(find_main(output_soup))
    output_native_ids = {
        video.get("data-native-id", "")
        for video in output_soup.select("video[data-native-id]")
        if video.get("data-native-id")
    }

    page_audits = []
    missing_text: list[dict[str, str]] = []
    cursor = 0
    for path, _ in PAGE_ORDER:
        source_texts = source_text_by_page[path]
        segment = output_soup.select_one(f'[data-onepage-source="{BASE + (path if path != "/" else "/")}"]')
        segment_texts = visible_texts(segment) if isinstance(segment, Tag) else []
        exact = source_texts == segment_texts
        if not exact:
            maximum = max(len(source_texts), len(segment_texts))
            for index in range(maximum):
                expected = source_texts[index] if index < len(source_texts) else "<missing>"
                actual = segment_texts[index] if index < len(segment_texts) else "<missing>"
                if expected != actual:
                    missing_text.append({"page": path, "expected": expected, "actual": actual})
                    break
        page_audits.append(
            {
                "path": path,
                "source_text_blocks": len(source_texts),
                "output_text_blocks": len(segment_texts),
                "exact_text_sequence": exact,
                "source_youtube_ids": source_youtube_by_page[path],
            }
        )
        cursor += len(source_texts)

    all_source_youtube = list(dict.fromkeys(
        video_id
        for path, _ in PAGE_ORDER
        for video_id in source_youtube_by_page[path]
    ))
    all_source_native = {record["system_data_id"] for record in native_records if record["system_data_id"]}

    footer_source = pages["/"][1].find("footer")
    footer_output = output_soup.find("footer")
    footer_preserved = bool(footer_source and footer_output and clean_text(footer_source.get_text(" ", strip=True)) == clean_text(footer_output.get_text(" ", strip=True)))

    required_literals = {
        "instagram": "https://www.instagram.com/carpiomedianyc/",
        "facebook": "https://www.facebook.com/carpiomedia",
        "fedlinks": "https://fedlinks.com/profile/Carpio-Media-LLC-Forest-Hills-NY",
        "mbe_asset": "minority-business-enterprise",
        "desktop_logo_asset": "Carpio+Media+initials+white+nb.png",
    }
    literal_checks = {name: literal.lower() in output_html.lower() for name, literal in required_literals.items()}

    passed = (
        all(item["exact_text_sequence"] for item in page_audits)
        and set(all_source_youtube).issubset(set(output_youtube))
        and all_source_native.issubset(output_native_ids)
        and footer_preserved
        and all(literal_checks.values())
    )

    audit = {
        "passed": passed,
        "template_source": str(template_path),
        "output": str(OUTPUT),
        "page_audits": page_audits,
        "first_text_mismatches": missing_text,
        "source_youtube_ids": all_source_youtube,
        "output_youtube_ids": output_youtube,
        "source_native_video_ids": sorted(all_source_native),
        "output_native_video_ids": sorted(output_native_ids),
        "footer_preserved": footer_preserved,
        "required_asset_and_link_checks": literal_checks,
        "native_video_records": native_records,
        "main_text_block_total": len(output_texts),
    }
    AUDIT.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")

    if not passed:
        print(json.dumps(audit, indent=2, ensure_ascii=False))
        return 1

    print(json.dumps({"passed": True, "output": str(OUTPUT), "audit": str(AUDIT)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
