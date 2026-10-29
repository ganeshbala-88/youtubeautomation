"""Script generation. Groq writes the script; a local fallback keeps the pipeline testable."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

PROMPT_TEMPLATE = """You are the writer for a faceless YouTube channel.

Topic: {topic}
Language: {language}
Tone: {tone}
Number of scenes: exactly {scene_count}
Length of each scene's narration: {min_seconds} to {max_seconds} seconds when spoken at {wpm} words per minute.

Return ONLY a single JSON object, no markdown fences, no commentary, with this shape:
{{
  "title": "clickable but honest title, under 70 characters",
  "description": "2 short paragraphs, then 5 relevant hashtags on the last line",
  "tags": ["8 to 12 lowercase search tags"],
  "scenes": [
    {{"narration": "what the narrator says in this scene", "image_query": "3-6 word visual search query for this scene"}}
  ]
}}

Rules:
- Scene 1 must hook the viewer in the first sentence.
- The final scene must close the loop and invite a subscribe, without being pushy.
- Every narration must be speakable prose: no markdown, no emoji, no stage directions, no speaker labels.
- image_query describes a concrete photographic subject, not an abstract concept.
"""


@dataclass
class Scene:
    narration: str
    image_query: str


@dataclass
class VideoPlan:
    topic: str
    title: str
    description: str
    tags: list[str] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_plan(raw: str) -> dict[str, Any]:
    cleaned = _strip_fences(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


def _resolve_model(client, candidates: list[str]) -> str:
    """Groq retires model IDs regularly, so pick the first candidate that still exists."""
    try:
        available = {model.id for model in client.models.list().data}
    except Exception:
        return candidates[0]
    for candidate in candidates:
        if candidate in available:
            return candidate
    raise SystemExit(
        "None of the configured Groq models are available: "
        + ", ".join(candidates)
        + f"\nAvailable: {', '.join(sorted(available))}"
    )


def _scene_count(cfg: dict[str, Any]) -> int:
    video = cfg["video"]
    script = cfg["script"]
    average_scene = (script["scene_min_seconds"] + script["scene_max_seconds"]) / 2
    count = round(video["target_seconds"] / average_scene)
    return max(4, min(20, count))


def generate(topic: str, cfg: dict[str, Any], offline: bool = False) -> VideoPlan:
    if offline or not os.environ.get("GROQ_API_KEY"):
        reason = "requested" if offline else "GROQ_API_KEY is not set"
        print(f"[script] using built-in offline script ({reason})")
        return offline_plan(topic, cfg)

    from groq import Groq

    script_cfg = cfg["script"]
    scene_count = _scene_count(cfg)
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    model = _resolve_model(client, script_cfg["models"])
    print(f"[script] generating with Groq model {model}")

    prompt = PROMPT_TEMPLATE.format(
        topic=topic,
        language=script_cfg["language"],
        tone=script_cfg["tone"],
        scene_count=scene_count,
        min_seconds=script_cfg["scene_min_seconds"],
        max_seconds=script_cfg["scene_max_seconds"],
        wpm=script_cfg["words_per_minute"],
    )

    response = client.chat.completions.create(
        model=model,
        temperature=0.8,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "You respond with valid JSON only."},
            {"role": "user", "content": prompt},
        ],
    )
    payload = _parse_plan(response.choices[0].message.content or "")

    scenes = [
        Scene(narration=str(s["narration"]).strip(), image_query=str(s.get("image_query", topic)).strip())
        for s in payload.get("scenes", [])
        if str(s.get("narration", "")).strip()
    ]
    if not scenes:
        raise SystemExit("[script] Groq returned no usable scenes")

    return VideoPlan(
        topic=topic,
        title=str(payload.get("title") or topic).strip()[:100],
        description=str(payload.get("description") or topic).strip(),
        tags=[str(t).strip() for t in payload.get("tags", [])][:15],
        scenes=scenes,
    )


def offline_plan(topic: str, cfg: dict[str, Any]) -> VideoPlan:
    """Deterministic placeholder script so the render/upload path can be tested without an API key."""
    beats = [
        ("A wide establishing shot of the subject", f"{topic} wide overview"),
        ("The core mechanism behind it", f"{topic} diagram closeup"),
        ("The part most people get wrong", f"{topic} comparison detail"),
        ("What it means in practice", f"{topic} real world example"),
        ("The takeaway worth remembering", f"{topic} summary"),
    ]
    scenes = [
        Scene(
            narration=(
                f"{beat} is where we start with {topic.lower()}. "
                "This placeholder narration exists so the pipeline can be exercised end to end "
                "without spending an API call. Set GROQ_API_KEY to replace it with a real script."
            ),
            image_query=query,
        )
        for beat, query in beats
    ]
    return VideoPlan(
        topic=topic,
        title=f"{topic[:80]}",
        description=(
            f"A short explainer about {topic}.\n\n"
            "This description was produced by the offline placeholder generator.\n\n"
            "#automation #explainer #education #science #learning"
        ),
        tags=["automation", "explainer", "education", topic.lower()[:30]],
        scenes=scenes,
    )
