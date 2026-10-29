"""Config loading plus the small amount of environment validation the pipeline needs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config.yaml"

DEFAULTS: dict[str, Any] = {
    "video": {
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "zoom_source_scale": 2,
        "scene_padding_seconds": 0.35,
        "background_music": None,
        "music_volume": 0.08,
        "target_seconds": 300,
    },
    "script": {
        "provider": "groq",
        "models": ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "llama-3.1-8b-instant"],
        "language": "English",
        "tone": "clear and informative",
        "words_per_minute": 150,
        "scene_min_seconds": 12,
        "scene_max_seconds": 30,
    },
    "voice": {"name": "en-US-GuyNeural", "rate": "+0%", "pitch": "+0Hz", "volume": "+0%"},
    "captions": {
        "enabled": True,
        "font": "DejaVu Sans",
        "font_size": 54,
        "margin_v": 90,
        "outline": 4,
        "max_words_per_cue": 5,
    },
    "visuals": {"sources": ["wikimedia", "picsum", "card"], "card_color": "#101828", "card_accent": "#3B82F6"},
    "thumbnail": {"enabled": True},
    "upload": {
        "privacy": "private",
        "category_id": "27",
        "tags": [],
        "made_for_kids": False,
    },
    "shorts": {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "max_seconds": 180,
        "scene_seconds": 3,
        "caption_font_size": 64,
        "caption_margin_v": 480,
        "voice": True,
        "music_dir": "assets/music",
        "music_volume": 0.12,
    },
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise SystemExit(f"config file not found: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return _merge(DEFAULTS, raw)


def require_env(*names: str) -> dict[str, str]:
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise SystemExit(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + "\nSet them in your shell or as GitHub Actions secrets."
        )
    return {name: os.environ[name] for name in names}
