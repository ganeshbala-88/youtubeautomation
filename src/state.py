"""Topic queue and upload history, persisted in data/ so runs never repeat themselves."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOPICS_PATH = ROOT / "data" / "topics.txt"
HISTORY_PATH = ROOT / "data" / "history.json"


@dataclass
class State:
    used_topics: list[str] = field(default_factory=list)
    uploads: list[dict] = field(default_factory=list)

    def save(self, path: Path = HISTORY_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"used_topics": self.used_topics, "uploads": self.uploads}
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def record(self, topic: str, **details) -> None:
        if topic not in self.used_topics:
            self.used_topics.append(topic)
        self.uploads.append(
            {"topic": topic, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **details}
        )


def load_state(path: Path = HISTORY_PATH) -> State:
    if not path.exists():
        return State()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return State()
    return State(
        used_topics=list(payload.get("used_topics", [])),
        uploads=list(payload.get("uploads", [])),
    )


def read_topics(path: Path = TOPICS_PATH) -> list[str]:
    if not path.exists():
        return []
    topics = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            topics.append(line)
    return topics


def next_topic(state: State, override: str | None = None, path: Path = TOPICS_PATH) -> str:
    """Explicit --topic wins; otherwise take the first queue entry we have not used."""
    if override:
        return override.strip()
    used = set(state.used_topics)
    for topic in read_topics(path):
        if topic not in used:
            return topic
    # Queue exhausted: recycle the oldest topic rather than failing the run.
    all_topics = read_topics(path)
    if all_topics:
        return all_topics[0]
    raise SystemExit(f"No topics available. Add some to {path} or pass --topic.")
