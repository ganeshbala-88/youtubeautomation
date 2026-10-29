"""Entrypoint: topic -> script -> voiceover -> visuals -> render -> upload.

    python run.py                     # full run using config.yaml
    python run.py --dry-run           # render locally, never touch YouTube
    python run.py --topic "..."       # skip the topic queue
    python run.py --privacy unlisted  # override upload privacy
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src import render, script_gen, state, tts, upload, visuals
from src.config import ROOT, load_config

OUTPUT_DIR = ROOT / "output"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and upload a YouTube video automatically.")
    parser.add_argument("--topic", help="Topic to use instead of the next one in the queue.")
    parser.add_argument("--config", help="Path to a config file (default: config.yaml).")
    parser.add_argument("--dry-run", action="store_true", help="Render the video but do not upload.")
    parser.add_argument("--offline-script", action="store_true", help="Use the built-in placeholder script.")
    parser.add_argument("--privacy", choices=["private", "unlisted", "public"], help="Override upload privacy.")
    parser.add_argument("--no-captions", action="store_true", help="Render without burned-in captions.")
    parser.add_argument("--keep-temp", action="store_true", help="Keep the intermediate segment files.")
    return parser.parse_args()


def slugify(text: str, limit: int = 50) -> str:
    cleaned = "".join(char if char.isalnum() or char in " -_" else "" for char in text.lower())
    return "-".join(cleaned.split())[:limit] or "video"


def build_scenes(plan, workdir: Path, cfg: dict) -> list[dict]:
    padding = cfg["video"]["scene_padding_seconds"]
    scenes: list[dict] = []

    for index, scene in enumerate(plan.scenes):
        print(f"[scene {index + 1}/{len(plan.scenes)}] {scene.image_query}")

        mp3 = workdir / f"audio_{index:03d}.mp3"
        words = tts.synthesize(scene.narration, mp3, cfg["voice"])

        wav = workdir / f"audio_{index:03d}.wav"
        duration = render.normalize_audio(mp3, wav, padding)
        if not words:
            words = tts.even_timings(scene.narration, duration)

        image = workdir / f"image_{index:03d}.png"
        source = visuals.resolve(scene.image_query, image, cfg)
        print(f"          {duration:.1f}s  visual={source}")

        scenes.append(
            {"image": image, "audio": wav, "duration": duration, "words": words, "visual_source": source}
        )
    return scenes


def main() -> int:
    args = parse_args()
    cfg = load_config(args.config)
    if args.no_captions:
        cfg["captions"]["enabled"] = False

    current = state.load_state()
    topic = state.next_topic(current, args.topic)
    print(f"[topic] {topic}")

    plan = script_gen.generate(topic, cfg, offline=args.offline_script)
    print(f"[script] {len(plan.scenes)} scenes -> {plan.title}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    workdir = OUTPUT_DIR / f"{stamp}-{slugify(topic)}"
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "plan.json").write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")

    started = time.time()
    scenes = build_scenes(plan, workdir, cfg)

    print("[render] assembling video")
    video = render.assemble(scenes, workdir / "final.mp4", workdir, cfg)

    thumbnail = workdir / "thumbnail.jpg"
    if cfg["thumbnail"]["enabled"]:
        render.write_thumbnail(scenes[0]["image"], plan.title, thumbnail, cfg)

    total = sum(scene["duration"] for scene in scenes)
    print(f"[render] {video} ({total:.1f}s, built in {time.time() - started:.0f}s)")

    if args.dry_run:
        # A dry run publishes nothing, so it must not consume the topic queue.
        print("[upload] skipped (--dry-run)")
    else:
        video_id = upload.upload(video, plan, cfg, privacy=args.privacy)
        if cfg["thumbnail"]["enabled"] and thumbnail.exists():
            upload.set_thumbnail(video_id, thumbnail)
        current.record(
            topic,
            title=plan.title,
            video_id=video_id,
            privacy=args.privacy or cfg["upload"]["privacy"],
            duration_seconds=round(total, 1),
            artifact=str(video.relative_to(ROOT)),
        )
        current.save()

    if not args.keep_temp:
        # Scene images/audio are kept so a bad render can be diagnosed afterwards.
        for pattern in ("segment_*.mp4", "silent.mp4", "*_list.txt"):
            for temp in workdir.glob(pattern):
                temp.unlink(missing_ok=True)

    print(f"[done] artifacts in {workdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
