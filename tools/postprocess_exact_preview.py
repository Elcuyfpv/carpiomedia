#!/usr/bin/env python3
"""Make the exact Squarespace snapshot self-contained on static hosting.

Visible copy, order, captions, assets and media identities remain untouched. This
pass promotes lazy images, converts Squarespace-managed YouTube blocks into normal
iframes using their exact video IDs, and removes scripts that only work inside the
Squarespace runtime.
"""

from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup, Tag

FILE = Path("exact-preview.html")
YT_PATTERNS = [
    re.compile(r"(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=)|youtu\.be/)([A-Za-z0-9_-]{11})", re.I),
    re.compile(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"'),
    re.compile(r"data-video-id=[\"']([A-Za-z0-9_-]{11})[\"']", re.I),
]


def invalid_src(value: str | None) -> bool:
    if not value:
        return True
    stripped = value.strip().lower()
    return stripped in {"false", "about:blank"} or stripped.endswith("/false")


def promote_images(soup: BeautifulSoup) -> None:
    for image in soup.find_all("img"):
        current = image.get("src")
        candidate = image.get("data-src") or image.get("data-image") or image.get("data-original")
        if invalid_src(current) and candidate:
            image["src"] = candidate
        if not image.get("srcset") and image.get("data-srcset"):
            image["srcset"] = image.get("data-srcset")
        if image.get("loading") == "lazy":
            image.attrs.pop("loading", None)

    for source in soup.find_all("source"):
        if invalid_src(source.get("src")) and source.get("data-src"):
            source["src"] = source.get("data-src")
        if not source.get("srcset") and source.get("data-srcset"):
            source["srcset"] = source.get("data-srcset")


def youtube_id_for(block: Tag) -> str | None:
    if block.find("iframe", src=re.compile(r"youtube", re.I)):
        return None
    raw = str(block).replace("\\/", "/")
    for pattern in YT_PATTERNS:
        match = pattern.search(raw)
        if match:
            return match.group(1)
    return None


def convert_youtube_blocks(soup: BeautifulSoup) -> int:
    converted = 0
    for block in soup.select(".sqs-block-video"):
        video_id = youtube_id_for(block)
        if not video_id:
            continue

        caption_node = block.select_one(".video-caption")
        caption = " ".join(caption_node.get_text(" ", strip=True).split()) if caption_node else ""

        iframe = soup.new_tag("iframe")
        iframe["src"] = f"https://www.youtube.com/embed/{video_id}?rel=0&modestbranding=1"
        iframe["title"] = caption or "YouTube video player"
        iframe["loading"] = "lazy"
        iframe["allow"] = "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
        iframe["allowfullscreen"] = ""

        wrapper = block.select_one(".intrinsic, .sqs-video-wrapper, .video-block")
        if not isinstance(wrapper, Tag) or wrapper is block:
            wrapper = soup.new_tag("div")
            wrapper["class"] = ["exact-youtube-wrapper"]
            content = block.select_one(".sqs-block-content")
            if isinstance(content, Tag):
                content.insert(0, wrapper)
            else:
                block.insert(0, wrapper)
        else:
            # Keep the exact caption outside the player while removing only the
            # Squarespace-managed player/overlay content.
            for child in list(wrapper.contents):
                if isinstance(child, Tag) and child.select_one(".video-caption"):
                    continue
                child.extract()

        wrapper["class"] = list(dict.fromkeys((wrapper.get("class") or []) + ["exact-youtube-wrapper"]))
        wrapper.append(iframe)
        converted += 1
    return converted


def remove_squarespace_runtime(soup: BeautifulSoup) -> None:
    for element in soup.find_all(True):
        element.attrs.pop("data-block-scripts", None)

    for script in list(soup.find_all("script")):
        script_id = script.get("id", "")
        script_type = script.get("type", "")
        src = (script.get("src") or "").lower()
        parent_code_block = script.find_parent(class_="sqs-block-code")

        if script_id == "carpio-one-page-runtime" or "hls.js@" in src:
            continue
        if script_type == "application/ld+json":
            continue
        if parent_code_block and not any(host in src for host in ("squarespace", "sqspcdn")):
            continue
        script.decompose()


def add_player_styles(soup: BeautifulSoup) -> None:
    style = soup.find("style", id="carpio-one-page-bridge")
    if not isinstance(style, Tag):
        style = soup.new_tag("style", id="carpio-one-page-bridge")
        if soup.head:
            soup.head.append(style)
    existing = style.string or ""
    style.string = existing + """
.exact-youtube-wrapper{position:relative;width:100%;aspect-ratio:16/9;overflow:hidden;background:#000}
.exact-youtube-wrapper iframe{position:absolute;inset:0;width:100%;height:100%;border:0;display:block}
"""


def replace_runtime(soup: BeautifulSoup) -> None:
    runtime = soup.find("script", id="carpio-one-page-runtime")
    if not isinstance(runtime, Tag):
        return
    runtime.string = """
window.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('video[data-hls-src]').forEach(function (video) {
    var source = video.getAttribute('data-hls-src');
    if (!source) return;
    if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = source;
    } else if (window.Hls && window.Hls.isSupported()) {
      var hls = new window.Hls({ enableWorker: true });
      hls.loadSource(source);
      hls.attachMedia(video);
    }
  });

  var burger = document.querySelector('.header-burger-btn, .header-burger');
  if (burger) {
    burger.addEventListener('click', function () {
      var open = document.body.classList.toggle('header--menu-open');
      burger.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  }

  document.querySelectorAll('a[href^="#"]').forEach(function (link) {
    link.addEventListener('click', function () {
      document.body.classList.remove('header--menu-open');
      if (burger) burger.setAttribute('aria-expanded', 'false');
    });
  });
});
"""


def main() -> None:
    soup = BeautifulSoup(FILE.read_text(encoding="utf-8"), "lxml")
    promote_images(soup)
    converted = convert_youtube_blocks(soup)
    remove_squarespace_runtime(soup)
    add_player_styles(soup)
    replace_runtime(soup)
    soup.html["data-exact-youtube-blocks-converted"] = str(converted)
    FILE.write_text("<!doctype html>\n" + str(soup), encoding="utf-8")


if __name__ == "__main__":
    main()
