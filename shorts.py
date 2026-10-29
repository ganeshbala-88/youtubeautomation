"""Shorts pipeline: your own script JSON -> vertical video with voice and music -> YouTube.

    python shorts.py data/shorts/fight-scene.json --dry-run
    python shorts.py data/shorts/fight-scene.json --privacy private
    python shorts.py --next --privacy public     # unattended: next script not yet uploaded

No script generation: the JSON is the whole input. Each scene's `text` is both
spoken by edge-tts and burned on screen. Music is a random pick from `assets/music`.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src import render, tts, upload, visuals
from src.config import ROOT, load_config
from src.script_gen import VideoPlan

OUTPUT_DIR = ROOT / "output"
SHORTS_DIR = ROOT / "data" / "shorts"
HISTORY_FILE = ROOT / "data" / "shorts_history.json"
MUSIC_SUFFIXES = {".mp3", ".m4a", ".wav", ".ogg", ".flac"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a vertical Short from your own script JSON.")
    parser.add_argument("script", nargs="?", help="Path to the script JSON.")
    parser.add_argument("--next", action="store_true",
                        help="Ignore `script` and pick the oldest script in data/shorts that has not been uploaded yet.")
    parser.add_argument("--config", help="Path to a config file (default: config.yaml).")
    parser.add_argument("--dry-run", action="store_true", help="Render only; never touch YouTube.")
    parser.add_argument("--privacy", choices=["private", "unlisted", "public"], help="Override upload privacy.")
    parser.add_argument("--keep-temp", action="store_true", help="Keep the intermediate segment files.")
    args = parser.parse_args()
    if not args.next and not args.script:
        parser.error("give a script path, or --next to pick one automatically")
    return args


def slugify(text: str, limit: int = 50) -> str:
    cleaned = "".join(char if char.isalnum() or char in " -_" else "" for char in text.lower())
    return "-".join(cleaned.split())[:limit] or "short"


def load_script(path: Path, default_scene_seconds: float) -> dict:
    if not path.exists():
        raise SystemExit(f"script file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}")

    scenes = data.get("scenes") or []
    if not scenes:
        raise SystemExit(f"{path} has no scenes")
    for index, scene in enumerate(scenes, start=1):
        query = str(scene.get("image_query", "")).strip()
        if not query:
            raise SystemExit(f"scene {index} is missing image_query")
        scene["image_query"] = query
        scene["duration"] = float(scene.get("duration") or default_scene_seconds)
        scene["text"] = str(scene.get("text", "")).strip()
    return data


def load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    try:
        entries = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return entries if isinstance(entries, list) else []


def next_script() -> Path | None:
    """Oldest script in data/shorts that has not been uploaded yet."""
    uploaded = {entry.get("file") for entry in load_history()}
    for path in sorted(SHORTS_DIR.glob("*.json")):
        if path.name not in uploaded:
            return path
    return None


def record_upload(script_path: Path, title: str, video_id: str, privacy: str) -> None:
    entries = load_history()
    entries.append(
        {
            "file": script_path.name,
            "title": title,
            "video_id": video_id,
            "privacy": privacy,
            "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    HISTORY_FILE.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")


def shorts_config(cfg: dict) -> dict:
    """Swap the vertical-format numbers into a copy of the main config."""
    out = copy.deepcopy(cfg)
    shorts = out["shorts"]
    out["video"]["width"] = shorts["width"]
    out["video"]["height"] = shorts["height"]
    out["video"]["fps"] = shorts["fps"]
    out["captions"]["font_size"] = shorts["caption_font_size"]
    out["captions"]["margin_v"] = shorts["caption_margin_v"]
    return out


def pick_music(cfg: dict) -> Path | None:
    """Random track from the music folder, or None when the folder is empty/missing."""
    music_dir = ROOT / cfg["shorts"]["music_dir"]
    if not music_dir.is_dir():
        return None
    tracks = sorted(
        path for path in music_dir.iterdir()
        if path.is_file() and path.suffix.lower() in MUSIC_SUFFIXES
    )
    return random.choice(tracks) if tracks else None


def build_voice(scenes: list[dict], workdir: Path, cfg: dict) -> list[Path]:
    """Speak each scene's text and return pcm tracks that exactly fill each scene.

    The real decoded length is measured rather than trusting edge-tts word
    boundaries, which are not always returned. A scene grows when its narration
    is longer than the declared duration, so the last words are never clipped.
    """
    if not cfg["shorts"]["voice"]:
        return []

    margin = 0.45  # a little room after the last word so it never sounds cut
    parts: list[Path] = []
    for index, scene in enumerate(scenes):
        out_wav = workdir / f"voice_{index:03d}.wav"
        text = scene["text"]
        if not text:
            render.silence_wav(out_wav, scene["duration"])
            parts.append(out_wav)
            continue

        mp3 = workdir / f"voice_{index:03d}.mp3"
        tts.synthesize(text, mp3, cfg["voice"])
        raw = workdir / f"voice_{index:03d}_raw.wav"
        natural = render.normalize_audio(mp3, raw, 0.0)
        if natural + margin > scene["duration"]:
            scene["duration"] = round(natural + margin, 2)
        render.fit_audio(raw, out_wav, scene["duration"])
        print(f"[voice {index + 1}/{len(scenes)}] {natural:.1f}s speech -> {scene['duration']:.1f}s scene")
        parts.append(out_wav)
    return parts


def build_segments(scenes: list[dict], workdir: Path, cfg: dict) -> list[Path]:
    segments = []
    for index, scene in enumerate(scenes):
        image = workdir / f"image_{index:03d}.png"
        source = visuals.resolve(scene["image_query"], image, cfg)
        segment = workdir / f"segment_{index:03d}.mp4"
        render.render_segment(image, scene["duration"], index, segment, cfg)
        print(f"[scene {index + 1}/{len(scenes)}] {scene['image_query']}"
              f"  {scene['duration']:.1f}s  visual={source}")
        segments.append(segment)
    return segments


def assemble(
    segments: list[Path],
    scenes: list[dict],
    audio_parts: list[Path],
    out_path: Path,
    workdir: Path,
    cfg: dict,
) -> Path:
    """Stitch the segments, burn per-scene text, then lay voice + music underneath."""
    silent = workdir / "silent.mp4"
    render.concat(segments, silent, workdir)

    vision = silent
    if any(scene["text"] for scene in scenes):
        cues, offset = [], 0.0
        for scene in scenes:
            if scene["text"]:
                cues.append(render.Cue(start=offset, end=offset + scene["duration"], text=scene["text"]))
            offset += scene["duration"]
        render.write_ass(cues, workdir / "text.ass", cfg)
        vision = workdir / "vision.mp4"
        render.run(
            [render.ffmpeg_exe(), "-y", "-i", str(silent),
             "-vf", "subtitles=filename=text.ass",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
             "-an", str(vision)],
            cwd=workdir,
        )

    if not audio_parts:
        render.run(
            [render.ffmpeg_exe(), "-y", "-i", str(vision),
             "-c", "copy", "-movflags", "+faststart", "-an", str(out_path)]
        )
        return out_path

    voice = workdir / "voice.wav"
    render.concat(audio_parts, voice, workdir, audio=True)

    music = pick_music(cfg)
    inputs = [render.ffmpeg_exe(), "-y", "-i", str(vision), "-i", str(voice)]
    filters = []
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        filters.append(f"[2:a]volume={cfg['shorts']['music_volume']}[music]")
        filters.append("[1:a][music]amix=inputs=2:duration=first:dropout_transition=0[a]")
        print(f"[music] {music.name} at volume {cfg['shorts']['music_volume']}")
    else:
        filters.append("[1:a]anull[a]")
        print("[music] none (add tracks to the music folder to enable)")

    render.run(
        inputs
        + [
            "-filter_complex", ";".join(filters),
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", "-shortest",
            str(out_path),
        ]
    )
    return out_path


def main() -> int:
    args = parse_args()
    cfg = shorts_config(load_config(args.config))

    if args.next:
        script_path = next_script()
        if script_path is None:
            print("[shorts] every script in data/shorts has been uploaded already - add more JSON files")
            return 0
    else:
        script_path = Path(args.script)
    print(f"[shorts] script {script_path.name}")

    script = load_script(script_path, cfg["shorts"]["scene_seconds"])

    scenes = script["scenes"]
    limit = cfg["shorts"]["max_seconds"]
    declared = round(sum(scene["duration"] for scene in scenes), 1)
    if declared > limit:
        raise SystemExit(f"[shorts] {declared}s exceeds the {limit}s Shorts limit; shorten scene durations.")

    title = str(script.get("title") or "Short").strip()
    print(f"[shorts] {len(scenes)} scenes, {declared:.1f}s, "
          f"{cfg['video']['width']}x{cfg['video']['height']} -> {title}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    workdir = OUTPUT_DIR / f"{stamp}-{slugify(title)}"
    workdir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    audio_parts = build_voice(scenes, workdir, cfg)

    # Narration can stretch a scene, so the limit is checked again on the real total.
    total = round(sum(scene["duration"] for scene in scenes), 1)
    if total > limit:
        raise SystemExit(f"[shorts] {total}s exceeds the {limit}s Shorts limit; shorten the narration.")

    segments = build_segments(scenes, workdir, cfg)
    video = assemble(segments, scenes, audio_parts, workdir / "final.mp4", workdir, cfg)
    print(f"[render] {video} ({total:.1f}s, built in {time.time() - started:.0f}s)")

    if args.dry_run:
        print("[upload] skipped (--dry-run)")
    else:
        plan = VideoPlan(
            topic=title,
            title=title,
            description=str(script.get("description") or "").strip(),
            tags=[str(tag).strip() for tag in script.get("tags", [])][:15],
            scenes=[],
        )
        privacy = args.privacy or cfg["upload"]["privacy"]
        video_id = upload.upload(video, plan, cfg, privacy=args.privacy)
        print(f"[upload] https://youtube.com/shorts/{video_id}")
        record_upload(script_path, title, video_id, privacy)

    if not args.keep_temp:
        for pattern in ("segment_*.mp4", "silent.mp4", "vision.mp4", "voice_*.mp3",
                        "voice_*_raw.wav", "video_list.txt", "audio_list.txt"):
            for temp in workdir.glob(pattern):
                temp.unlink(missing_ok=True)

    print(f"[done] artifacts in {workdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
