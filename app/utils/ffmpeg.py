from __future__ import annotations

import subprocess
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def merge_av_streams(video_path: str | Path, audio_path: str | Path, output_path: str | Path) -> None:
    video = str(video_path)
    audio = str(audio_path)
    output = str(output_path)
    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        video,
        "-i",
        audio,
        "-c",
        "copy",
        output,
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise FFmpegError(detail or "ffmpeg 合并音视频失败。")


def probe_media_duration_ms(media_path: str | Path) -> int:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(media_path),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise FFmpegError(detail or "ffprobe 读取时长失败。")
    try:
        return int(float((completed.stdout or "0").strip()) * 1000)
    except ValueError as exc:
        raise FFmpegError("无法解析媒体时长。") from exc


def extract_audio_track(
    source_path: str | Path,
    output_path: str | Path,
    *,
    start_ms: int | None = None,
    duration_ms: int | None = None,
    sample_rate: int = 16000,
    channels: int = 1,
    bitrate: str = "24k",
) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
    ]
    if start_ms is not None and start_ms > 0:
        command.extend(["-ss", f"{start_ms / 1000:.3f}"])
    command.extend(["-i", str(source_path)])
    if duration_ms is not None and duration_ms > 0:
        command.extend(["-t", f"{duration_ms / 1000:.3f}"])
    command.extend(
        [
            "-vn",
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            "-b:a",
            bitrate,
            "-codec:a",
            "libmp3lame",
            str(output_path),
        ]
    )
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise FFmpegError(detail or "ffmpeg 音频提取失败。")
