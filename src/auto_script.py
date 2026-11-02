"""Write a Shorts script from a live news headline with Groq (free tier).

The output is the same shape as the hand-written JSON in data/shorts, so the
render and upload path does not need to know where the script came from.
"""

from __future__ import annotations

import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path

from src import news
from src.script_gen import _create_json_completion, _parse_plan, _resolve_model

PROMPT = """You write 30-45 second YouTube Shorts for a facts-first news channel.

Story headline: {headline}
Story summary: {summary}
Story source: {source}
Language: {language}
Tone: {tone}

Return ONLY a single JSON object, no markdown fences, with this shape:
{{
  "title": "under 70 characters, factual, curiosity without clickbait",
  "description": "2 short sentences, then 5 relevant hashtags on the last line",
  "tags": ["8 to 12 lowercase search tags"],
  "scenes": [
    {{"narration": "what the narrator says", "image_query": "3 to 6 concrete photographic words"}}
  ]
}}

Rules:
- Exactly {scene_count} scenes, about {words_per_scene} words of narration each.
- Use only facts present in the headline and summary above. Never invent numbers,
  names, quotes or dates. If a detail is missing, leave it out.
- Stay neutral: no opinion, no outrage, no speculation about motives.
- Scene 1 hooks the viewer inside the first six words.
- The final scene states why it matters to an ordinary viewer.
- Narration must be plain spoken prose: no markdown, no emoji, no speaker labels.
- image_query describes a real photographic subject, never an abstract idea.
"""


def _used_keys(history: list[dict]) -> set[str]:
    keys: set[str] = set()
    for entry in history:
        for field in ("source_url", "headline"):
            value = str(entry.get(field) or "").strip().lower()
            if value:
                keys.add(value)
    return keys


def _pick_story(stories: list[dict], used: set[str]) -> dict | None:
    fresh = [
        story
        for story in stories
        if story["title"].strip().lower() not in used
        and (not story["url"] or story["url"].strip().lower() not in used)
    ]
    if not fresh:
        return None
    # Random pick among the newest few so two runs a day never repeat a topic.
    return random.choice(fresh[: max(1, min(5, len(fresh)))])


def generate(cfg: dict, history: list[dict]) -> dict | None:
    """Return a script dict ready to render, or None when auto mode cannot run."""
    if not os.environ.get("GROQ_API_KEY"):
        print("[auto] GROQ_API_KEY is not set")
        return None

    auto = cfg["auto"]
    stories = news.fetch_headlines(auto["feeds"], auto["headlines_per_run"])
    if not stories:
        print("[auto] no headlines available right now")
        return None

    story = _pick_story(stories, _used_keys(history))
    if story is None:
        print("[auto] every current headline has been used already")
        return None
    print(f"[auto] story: {story['title']} ({story['source']})")

    from groq import Groq

    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    model = _resolve_model(client, cfg["script"]["models"])
    print(f"[auto] writing with Groq model {model}")

    prompt = PROMPT.format(
        headline=story["title"],
        summary=story["summary"] or "(no summary provided)",
        source=story["source"],
        language=cfg["script"]["language"],
        tone=cfg["script"]["tone"],
        scene_count=auto["scene_count"],
        words_per_scene=auto["words_per_scene"],
    )
    response = _create_json_completion(client, model, prompt, temperature=0.6)
    try:
        payload = _parse_plan(response.choices[0].message.content or "")
    except json.JSONDecodeError:
        print("[auto] Groq returned unparseable JSON")
        return None

    scenes = [
        {
            "image_query": str(scene.get("image_query") or story["title"]).strip(),
            "duration": cfg["shorts"]["scene_seconds"],
            "text": str(scene.get("narration") or "").strip(),
        }
        for scene in payload.get("scenes", [])
        if str(scene.get("narration") or "").strip()
    ]
    if len(scenes) < 3:
        print(f"[auto] Groq returned only {len(scenes)} usable scenes")
        return None

    return {
        "title": str(payload.get("title") or story["title"]).strip()[:100],
        "description": str(payload.get("description") or "").strip(),
        "tags": [str(tag).strip() for tag in payload.get("tags", [])][:15],
        "source": {
            "kind": "news",
            "headline": story["title"],
            "url": story["url"],
            "publisher": story["source"],
        },
        "scenes": scenes,
    }


def save(script: dict, out_dir: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"auto-{stamp}.json"
    path.write_text(json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
