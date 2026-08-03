#!/usr/bin/env python3
"""Remove Squarespace editor/runtime dependencies from the exact static snapshot.

The visible DOM, copy, assets, block order, captions, links, and media identifiers
are left untouched. This pass only promotes lazy image URLs and removes scripts
that expect the page to still be hosted inside Squarespace.
"""

from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup, Tag

FILE = Path("exact-preview.html")


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
        image.attrs.pop("loading", None) if image.get("loading") == "lazy" else None

    for source in soup.find_all("source"):
        if invalid_src(source.get("src")) and source.get("data-src"):
            source["src"] = source.get("data-src")
        if not source.get("srcset") and source.get("data-srcset"):
            source["srcset"] = source.get("data-srcset")


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

        # Static consolidated pages do not need analytics, editor context,
        # component bootstrapping, commerce, lazy-loader, or collection APIs.
        script.decompose()


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
    remove_squarespace_runtime(soup)
    replace_runtime(soup)
    FILE.write_text("<!doctype html>\n" + str(soup), encoding="utf-8")


if __name__ == "__main__":
    main()
