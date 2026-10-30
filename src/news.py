"""Keyless news headlines from public RSS feeds (no API key, no paid service)."""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; shorts-pipeline/1.0)"}
TIMEOUT = 20


def _child(node: ET.Element, name: str) -> ET.Element | None:
    for child in node:
        if child.tag.split("}")[-1] == name:
            return child
    return None


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def _link(node: ET.Element) -> str:
    child = _child(node, "link")
    if child is None:
        return ""
    href = child.get("href")  # Atom feeds put the URL in an attribute
    return (href or child.text or "").strip()


def _clean(text: str, limit: int = 400) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(text))
    return " ".join(text.split())[:limit]


def _entries(url: str) -> list[dict]:
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    root = ET.fromstring(response.content)

    out: list[dict] = []
    for node in root.iter():
        if node.tag.split("}")[-1] not in ("item", "entry"):
            continue
        title = _clean(_text(_child(node, "title")), 200)
        if not title:
            continue
        summary = _clean(_text(_child(node, "description")) or _text(_child(node, "summary")))
        out.append(
            {
                "title": title,
                "url": _link(node),
                "summary": summary,
                "source": url.split("/")[2].replace("www.", ""),
            }
        )
    return out


def fetch_headlines(feeds: list[str], limit: int = 10) -> list[dict]:
    """Newest headlines across every feed, de-duplicated by title. Failures are skipped."""
    headlines: list[dict] = []
    seen: set[str] = set()
    for feed in feeds:
        try:
            entries = _entries(feed)
        except Exception as exc:  # a dead feed must never stop the run
            print(f"[news] skipped {feed}: {exc}")
            continue
        print(f"[news] {len(entries)} headlines from {feed}")
        for entry in entries:
            key = entry["title"].lower()
            if key in seen:
                continue
            seen.add(key)
            headlines.append(entry)
    return headlines[:limit]
