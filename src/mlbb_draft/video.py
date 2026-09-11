from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, unquote, urlparse

import cv2
import numpy as np

from .models import VideoSource


class VideoAcquisitionError(RuntimeError):
    pass


class FrameExtractionRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoLocator:
    provider: str
    canonical_url: str
    video_id: str


@dataclass(frozen=True)
class VideoAsset:
    source: VideoSource
    local_path: Path


@dataclass(frozen=True)
class DecodedFrame:
    requested_timestamp_seconds: float
    actual_timestamp_seconds: float
    pixels: np.ndarray


class VideoProvider(Protocol):
    def resolve(self, url: str) -> VideoLocator: ...

    def probe(self, locator: VideoLocator) -> VideoSource: ...

    def materialize(self, locator: VideoLocator, workspace: Path) -> VideoAsset: ...


class YouTubeProvider:
    _VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
    _HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}

    def resolve(self, url: str) -> VideoLocator:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in self._HOSTS:
            raise ValueError("URL must be a supported YouTube HTTP(S) URL")
        if parsed.hostname == "youtu.be":
            video_id = parsed.path.strip("/").split("/")[0]
        else:
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        if not self._VIDEO_ID.fullmatch(video_id):
            raise ValueError("YouTube URL does not contain a valid video ID")
        return VideoLocator(
            provider="youtube",
            canonical_url=f"https://www.youtube.com/watch?v={video_id}",
            video_id=video_id,
        )

    @staticmethod
    def _extract(locator: VideoLocator, options: dict) -> dict:
        try:
            import yt_dlp
        except ImportError as exc:
            raise VideoAcquisitionError("yt-dlp is not installed") from exc
        try:
            with yt_dlp.YoutubeDL(options) as client:
                result = client.extract_info(locator.canonical_url, download=options.get("download", False))
        except Exception as exc:
            raise VideoAcquisitionError(f"YouTube acquisition failed: {exc}") from exc
        if not isinstance(result, dict):
            raise VideoAcquisitionError("yt-dlp returned no video metadata")
        return result

    @staticmethod
    def _source(locator: VideoLocator, info: dict) -> VideoSource:
        duration = info.get("duration")
        if not duration:
            raise VideoAcquisitionError("video duration is unavailable")
        fps = info.get("fps")
        return VideoSource(
            provider="youtube",
            url=locator.canonical_url,
            video_id=locator.video_id,
            title=info.get("title"),
            duration_seconds=float(duration),
            width=int(info["width"]) if info.get("width") else None,
            height=int(info["height"]) if info.get("height") else None,
            fps=float(fps) if fps else None,
        )

    def probe(self, locator: VideoLocator) -> VideoSource:
        node = shutil.which("node")
        info = self._extract(
            locator,
            {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "skip_download": True,
                "download": False,
                "js_runtimes": {"node": {"path": node}} if node else {},
                "remote_components": {"ejs:npm"},
            },
        )
        return self._source(locator, info)

    def materialize(self, locator: VideoLocator, workspace: Path) -> VideoAsset:
        workspace.mkdir(parents=True, exist_ok=True)
        output_template = str(workspace / "source.%(ext)s")
        info = self._extract(
            locator,
            {
                "quiet": True,
                "no_warnings": False,
                "noplaylist": True,
                "format": "bestvideo[height<=720]/best[height<=720]",
                "outtmpl": output_template,
                "restrictfilenames": True,
                "download": True,
                "js_runtimes": (
                    {"node": {"path": shutil.which("node")}}
                    if shutil.which("node")
                    else {}
                ),
                "remote_components": {"ejs:npm"},
            },
        )
        source = self._source(locator, info)
        requested_path = info.get("requested_downloads", [{}])[0].get("filepath")
        candidates = [Path(requested_path)] if requested_path else list(workspace.glob("source.*"))
        local_path = next((path for path in candidates if path.exists() and path.is_file()), None)
        if local_path is None:
            raise VideoAcquisitionError("download completed without a materialized video file")
        return VideoAsset(source=source, local_path=local_path)


class LocalFileProvider:
    """Provider adapter for fixture runs; the public API remains YouTube-only."""

    def resolve(self, url: str) -> VideoLocator:
        parsed = urlparse(url)
        if parsed.scheme == "file":
            path = Path(unquote(parsed.path))
        elif parsed.scheme == "":
            path = Path(url)
        else:
            raise ValueError("local source must be a filesystem path or file URI")
        path = path.expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"local video does not exist: {path}")
        return VideoLocator(provider="local", canonical_url=path.as_uri(), video_id=path.stem)

    @staticmethod
    def _path(locator: VideoLocator) -> Path:
        if locator.provider != "local":
            raise ValueError("locator is not a local video")
        return Path(unquote(urlparse(locator.canonical_url).path)).resolve()

    def probe(self, locator: VideoLocator) -> VideoSource:
        source = probe_local_video(self._path(locator))
        return source.model_copy(
            update={"video_id": locator.video_id, "url": locator.canonical_url}
        )

    def materialize(self, locator: VideoLocator, workspace: Path) -> VideoAsset:
        source = self.probe(locator)
        return VideoAsset(source=source, local_path=self._path(locator))


class FFmpegFrameExtractor:
    def __init__(self, ffmpeg_binary: str = "ffmpeg", ffprobe_binary: str = "ffprobe"):
        self.ffmpeg_binary = self._resolve_binary(ffmpeg_binary)
        self.ffprobe_binary = self._resolve_binary(ffprobe_binary)

    @staticmethod
    def _resolve_binary(binary: str) -> str:
        resolved = shutil.which(binary)
        if resolved is None:
            raise FrameExtractionRuntimeError(f"required binary is not available: {binary}")
        return resolved

    def _actual_frame_timestamp(self, path: Path, requested: float) -> float:
        interval = f"{requested:.6f}%+2.0"
        command = [
            self.ffprobe_binary,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-read_intervals",
            interval,
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "json",
            str(path),
        ]
        process = subprocess.run(command, capture_output=True, text=True, check=False)
        if process.returncode != 0:
            raise FrameExtractionRuntimeError(process.stderr.strip() or "ffprobe failed")
        payload = json.loads(process.stdout)
        timestamps = [
            float(frame["best_effort_timestamp_time"])
            for frame in payload.get("frames", [])
            if frame.get("best_effort_timestamp_time") is not None
        ]
        if not timestamps:
            raise FrameExtractionRuntimeError(f"no decodable frame at {requested:.3f} seconds")
        return min(timestamps, key=lambda value: (value < requested - 1e-3, abs(value - requested)))

    def extract_at(self, asset: VideoAsset, requested_timestamp_seconds: float) -> DecodedFrame:
        if requested_timestamp_seconds < 0 or requested_timestamp_seconds >= asset.source.duration_seconds:
            raise ValueError("requested timestamp is outside the video")
        actual = self._actual_frame_timestamp(asset.local_path, requested_timestamp_seconds)
        command = [
            self.ffmpeg_binary,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{actual:.6f}",
            "-i",
            str(asset.local_path),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "pipe:1",
        ]
        process = subprocess.run(command, capture_output=True, check=False)
        if process.returncode != 0:
            raise FrameExtractionRuntimeError(process.stderr.decode(errors="replace").strip() or "ffmpeg failed")
        encoded = np.frombuffer(process.stdout, dtype=np.uint8)
        pixels = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if pixels is None:
            raise FrameExtractionRuntimeError("ffmpeg returned an undecodable image")
        return DecodedFrame(requested_timestamp_seconds, actual, pixels)

    def extract_many(self, asset: VideoAsset, timestamps: list[float]) -> list[DecodedFrame]:
        """Decode dense timestamp groups sequentially and seek only across large gaps."""
        ordered = sorted(set(timestamps))
        if not ordered:
            return []
        for requested in ordered:
            if requested < 0 or requested >= asset.source.duration_seconds:
                raise ValueError("requested timestamp is outside the video")

        groups: list[list[float]] = [[ordered[0]]]
        for requested in ordered[1:]:
            if requested - groups[-1][-1] <= 3.0:
                groups[-1].append(requested)
            else:
                groups.append([requested])

        capture = cv2.VideoCapture(str(asset.local_path))
        if not capture.isOpened():
            raise FrameExtractionRuntimeError(f"cannot open video: {asset.local_path}")
        decoded: list[DecodedFrame] = []
        try:
            for group in groups:
                capture.set(cv2.CAP_PROP_POS_MSEC, group[0] * 1000.0)
                target_index = 0
                previous_pixels: np.ndarray | None = None
                previous_time: float | None = None
                while target_index < len(group):
                    succeeded, pixels = capture.read()
                    if not succeeded or pixels is None:
                        raise FrameExtractionRuntimeError(
                            f"no decodable frame at {group[target_index]:.3f} seconds"
                        )
                    actual = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                    if actual <= 0:
                        actual = group[target_index]
                    while target_index < len(group) and actual >= group[target_index] - 1e-3:
                        requested = group[target_index]
                        if (
                            previous_pixels is not None
                            and previous_time is not None
                            and abs(previous_time - requested) < abs(actual - requested)
                        ):
                            chosen_pixels = previous_pixels
                            chosen_time = previous_time
                        else:
                            chosen_pixels = pixels
                            chosen_time = actual
                        decoded.append(
                            DecodedFrame(requested, chosen_time, chosen_pixels.copy())
                        )
                        target_index += 1
                    previous_pixels = pixels
                    previous_time = actual
        finally:
            capture.release()
        return sorted(decoded, key=lambda item: item.requested_timestamp_seconds)


def probe_local_video(path: Path, ffprobe_binary: str = "ffprobe") -> VideoSource:
    resolved = shutil.which(ffprobe_binary)
    if resolved is None:
        raise FrameExtractionRuntimeError("ffprobe is not available")
    command = [
        resolved,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate:format=duration",
        "-of",
        "json",
        str(path),
    ]
    process = subprocess.run(command, capture_output=True, text=True, check=False)
    if process.returncode != 0:
        raise FrameExtractionRuntimeError(process.stderr.strip() or "ffprobe failed")
    payload = json.loads(process.stdout)
    stream = payload["streams"][0]
    numerator, denominator = stream.get("avg_frame_rate", "0/1").split("/")
    fps = float(numerator) / float(denominator) if float(denominator) else None
    return VideoSource(
        provider="local",
        url=path.resolve().as_uri(),
        video_id=path.stem,
        duration_seconds=float(payload["format"]["duration"]),
        width=int(stream["width"]),
        height=int(stream["height"]),
        fps=fps,
    )
