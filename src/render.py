"""ffmpeg rendering: Ken Burns segments, burned-in captions, voice track, and thumbnail."""

from __future__ import annotations

import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .tts import Word


def ffmpeg_exe() -> str:
    """Prefer the pip-installed static ffmpeg so no system package is required."""
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        found = shutil.which("ffmpeg")
        if not found:
            raise SystemExit("ffmpeg not found. `pip install imageio-ffmpeg` or install system ffmpeg.")
        return found


def run(args: list[str], cwd: Path | None = None) -> None:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-12:])
        raise RuntimeError(f"ffmpeg failed ({' '.join(args[:3])}...):\n{tail}")


def wav_duration(path: Path) -> float:
    """Exact duration from the wav header - the scene audio is always pcm."""
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


def normalize_audio(source: Path, out_wav: Path, padding: float) -> float:
    """Decode to 24 kHz mono pcm and pad with the same silence the video gets. Returns duration."""
    args = [ffmpeg_exe(), "-y", "-i", str(source)]
    if padding > 0:
        args += ["-af", f"apad=pad_dur={padding}"]
    args += ["-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)]
    run(args)
    return wav_duration(out_wav)


def fit_audio(source: Path, out_wav: Path, seconds: float) -> None:
    """Decode to 24 kHz mono pcm padded or trimmed to exactly `seconds`."""
    run(
        [
            ffmpeg_exe(), "-y",
            "-i", str(source),
            "-af", "apad",
            "-t", f"{seconds:.3f}",
            "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
            str(out_wav),
        ]
    )


def silence_wav(out_wav: Path, seconds: float) -> None:
    """Generate a silent pcm track so scenes without narration still keep the timeline."""
    run(
        [
            ffmpeg_exe(), "-y",
            "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", f"{seconds:.3f}",
            "-c:a", "pcm_s16le",
            str(out_wav),
        ]
    )


def _ken_burns_filter(index: int, frames: int, cfg: dict) -> str:
    video = cfg["video"]
    width, height, fps = video["width"], video["height"], video["fps"]
    source_width = width * video["zoom_source_scale"]
    zoom_in = index % 2 == 0
    expression = (
        f"min(1+0.0015*on\\,1.20)" if zoom_in else f"max(1.20-0.0015*on\\,1.0)"
    )
    return (
        f"scale={source_width}:-2:flags=lanczos,"
        f"zoompan=z='{expression}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s={width}x{height}:fps={fps},"
        "setsar=1,format=yuv420p"
    )


def render_segment(image: Path, duration: float, index: int, out_path: Path, cfg: dict) -> None:
    video = cfg["video"]
    frames = max(1, round(duration * video["fps"]))
    run(
        [
            ffmpeg_exe(), "-y",
            "-i", str(image),
            "-vf", _ken_burns_filter(index, frames, cfg),
            "-frames:v", str(frames),
            "-r", str(video["fps"]),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            str(out_path),
        ]
    )


def concat(parts: list[Path], out_path: Path, workdir: Path, audio: bool = False) -> None:
    """Concatenate identically-encoded parts via the concat demuxer."""
    if len(parts) == 1:
        shutil.copyfile(parts[0], out_path)
        return
    list_file = workdir / ("audio_list.txt" if audio else "video_list.txt")
    list_file.write_text(
        "".join(f"file '{part.resolve().as_posix()}'\n" for part in parts), encoding="utf-8"
    )
    args = [ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0", "-i", list_file]
    args += ["-c:a", "pcm_s16le", str(out_path)] if audio else ["-c", "copy", str(out_path)]
    run(args)


# ----------------------------------------------------------------- captions


def _sanitize(text: str) -> str:
    for bad in ("\\", "{", "}"):
        text = text.replace(bad, "")
    return " ".join(text.split())


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{int(hours)}:{int(minutes):02d}:{secs:05.2f}"


@dataclass
class Cue:
    start: float
    end: float
    text: str


def build_cues(scenes: list[dict], max_words: int) -> list[Cue]:
    """Turn per-scene word timings into short caption cues on the global timeline."""
    cues: list[Cue] = []
    offset = 0.0
    for scene in scenes:
        words: list[Word] = scene["words"]
        for index in range(0, len(words), max_words):
            group = words[index : index + max_words]
            cues.append(
                Cue(
                    start=offset + group[0].start,
                    end=offset + group[-1].end,
                    text=" ".join(word.text for word in group),
                )
            )
        offset += scene["duration"]
    return cues


def write_ass(cues: list[Cue], out_path: Path, cfg: dict) -> None:
    captions = cfg["captions"]
    video = cfg["video"]
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {video['width']}\n"
        f"PlayResY: {video['height']}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Caption,{captions['font']},{captions['font_size']},&H00FFFFFF,&H000000FF,"
        f"&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,{captions['outline']},1,2,"
        f"80,80,{captions['margin_v']},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = [
        f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},Caption,,0,0,0,,{_sanitize(cue.text)}\n"
        for cue in cues
    ]
    out_path.write_text(header + "".join(lines), encoding="utf-8")


# ----------------------------------------------------------------- thumbnail


def write_thumbnail(image: Path, title: str, out_path: Path, cfg: dict) -> None:
    width, height = 1280, 720
    with Image.open(image) as source:
        canvas = source.convert("RGB")
        scale = max(width / canvas.width, height / canvas.height)
        canvas = canvas.resize((int(canvas.width * scale), int(canvas.height * scale)), Image.LANCZOS)
        left = (canvas.width - width) // 2
        top = (canvas.height - height) // 2
        canvas = canvas.crop((left, top, left + width, top + height))

    scrim = Image.new("RGB", (width, height), "#000000")
    canvas = Image.blend(canvas, scrim, 0.35)
    draw = ImageDraw.Draw(canvas)

    font_size = 78
    while font_size > 34:
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()
        wrapped, current = [], ""
        for word in title.split():
            candidate = f"{current} {word}".strip()
            if draw.textlength(candidate, font=font) <= width - 100:
                current = candidate
            else:
                wrapped.append(current)
                current = word
        if current:
            wrapped.append(current)
        if len(wrapped) <= 3:
            break
        font_size -= 6

    line_height = int(font_size * 1.22)
    y = (height - len(wrapped) * line_height) // 2
    for line in wrapped:
        draw.text(
            (width / 2, y),
            line,
            font=font,
            fill="#FFFFFF",
            anchor="ma",
            stroke_width=6,
            stroke_fill="#000000",
        )
        y += line_height
    canvas.save(out_path, "JPEG", quality=90)


# ----------------------------------------------------------------- assembly


def assemble(scenes: list[dict], out_path: Path, workdir: Path, cfg: dict) -> Path:
    """Render every scene, stitch them, burn captions, and lay the voice track underneath."""
    segments, audio_parts = [], []
    for index, scene in enumerate(scenes):
        segment = workdir / f"segment_{index:03d}.mp4"
        render_segment(scene["image"], scene["duration"], index, segment, cfg)
        segments.append(segment)
        audio_parts.append(scene["audio"])

    silent = workdir / "silent.mp4"
    concat(segments, silent, workdir)

    voice = workdir / "voice.wav"
    concat(audio_parts, voice, workdir, audio=True)

    filter_parts, maps = [], []
    if cfg["captions"]["enabled"]:
        write_ass(build_cues(scenes, cfg["captions"]["max_words_per_cue"]), workdir / "captions.ass", cfg)
        filter_parts.append("[0:v]subtitles=filename=captions.ass[v]")
    else:
        filter_parts.append("[0:v]null[v]")
    maps += ["-map", "[v]"]

    music = cfg["video"].get("background_music")
    inputs = [ffmpeg_exe(), "-y", "-i", str(silent), "-i", str(voice)]
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        filter_parts.append(
            f"[2:a]volume={cfg['video']['music_volume']}[music];"
            "[1:a][music]amix=inputs=2:duration=first:dropout_transition=0[a]"
        )
    else:
        filter_parts.append("[1:a]anull[a]")
    maps += ["-map", "[a]"]

    run(
        inputs
        + [
            "-filter_complex", ";".join(filter_parts),
            *maps,
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-shortest",
            str(out_path),
        ],
        cwd=workdir,
    )
    return out_path
