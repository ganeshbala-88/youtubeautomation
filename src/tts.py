"""Voiceover generation via edge-tts (free, no API key) plus word timings for captions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import edge_tts


@dataclass
class Word:
    start: float
    end: float
    text: str


async def _stream(text: str, out_mp3: Path, voice: dict) -> list[Word]:
    communicate = edge_tts.Communicate(
        text,
        voice["name"],
        rate=voice.get("rate", "+0%"),
        pitch=voice.get("pitch", "+0Hz"),
        volume=voice.get("volume", "+0%"),
    )
    words: list[Word] = []
    with out_mp3.open("wb") as handle:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                handle.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 10_000_000
                words.append(
                    Word(
                        start=start,
                        end=(chunk["offset"] + chunk["duration"]) / 10_000_000,
                        text=chunk["text"],
                    )
                )
    return words


def synthesize(text: str, out_mp3: Path, voice: dict) -> list[Word]:
    """Write speech to out_mp3 and return word-boundary timings (may be empty)."""
    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    words = asyncio.run(_stream(text, out_mp3, voice))
    if not out_mp3.exists() or out_mp3.stat().st_size == 0:
        raise RuntimeError(f"edge-tts produced no audio for: {text[:60]!r}")
    return words


def even_timings(text: str, duration: float) -> list[Word]:
    """Fallback when the voice returns no word boundaries: spread words evenly."""
    tokens = text.split()
    if not tokens or duration <= 0:
        return []
    step = duration / len(tokens)
    return [
        Word(start=index * step, end=(index + 1) * step, text=token)
        for index, token in enumerate(tokens)
    ]
