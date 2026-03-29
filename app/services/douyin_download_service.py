from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.config.settings import PROJECT_ROOT
from app.utils.paths import build_download_output_path

ProgressCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]

_CONFIG_PATH = PROJECT_ROOT / "runtime" / "downloader_config.json"
_DEFAULT_CONFIG = {
    "enabled": True,
    "parser_base_url": "https://douyin-vd.vercel.app/api/hello",
    "timeout_seconds": 45,
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
}
_URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)


class DouyinDownloadError(RuntimeError):
    pass


@dataclass
class DouyinDownloadResult:
    share_url: str
    parser_url: str
    video_url: str
    local_path: str
    title: str | None = None
    author: str | None = None


class DouyinDownloadService:
    def __init__(self) -> None:
        self._config: dict[str, Any] | None = None

    def download_from_text(
        self,
        text: str,
        progress_callback: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> DouyinDownloadResult:
        share_url = self.extract_share_url(text)
        if not share_url:
            raise DouyinDownloadError("没有识别到抖音分享链接，请先复制包含 https:// 的分享文本。")

        payload, parser_url = self._resolve_share_url(share_url, should_cancel=should_cancel)
        video_url = self._extract_video_url(payload)
        title = self._extract_text(payload, ("title", "desc", "aweme_id"))
        author = self._extract_text(payload, ("author", "nickname", "sec_uid"))
        suffix = self._guess_suffix(video_url)
        local_path = build_download_output_path(title or "douyin_video", suffix=suffix)
        self._download_file(
            video_url,
            local_path,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
        )

        return DouyinDownloadResult(
            share_url=share_url,
            parser_url=parser_url,
            video_url=video_url,
            local_path=str(local_path),
            title=title,
            author=author,
        )

    @staticmethod
    def extract_share_url(text: str) -> str | None:
        if not text:
            return None
        match = _URL_PATTERN.search(text)
        return match.group(0).rstrip(".,);") if match else None

    def load_config(self) -> dict[str, Any]:
        if self._config is not None:
            return self._config

        if not _CONFIG_PATH.exists():
            _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            _CONFIG_PATH.write_text(
                json.dumps(_DEFAULT_CONFIG, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._config = dict(_DEFAULT_CONFIG)
            return self._config

        with open(_CONFIG_PATH, encoding="utf-8") as handle:
            loaded = json.load(handle)

        merged = dict(_DEFAULT_CONFIG)
        merged.update(loaded)
        self._config = merged
        return self._config

    def _resolve_share_url(
        self,
        share_url: str,
        should_cancel: CancelCallback | None = None,
    ) -> tuple[Any, str]:
        config = self.load_config()
        if not config.get("enabled", True):
            raise DouyinDownloadError("链接下载功能已禁用，请检查 runtime/downloader_config.json。")

        self._check_cancelled(should_cancel)
        parser_url = self._build_parser_url(str(config.get("parser_base_url", "")), share_url)
        request = Request(parser_url, headers=self._default_headers())

        try:
            with urlopen(request, timeout=float(config.get("timeout_seconds", 45))) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                body = response.read().decode(charset, errors="replace").strip()
        except Exception as exc:  # noqa: BLE001
            raise DouyinDownloadError(f"解析抖音链接失败：{exc}") from exc

        self._check_cancelled(should_cancel)
        try:
            return json.loads(body), parser_url
        except json.JSONDecodeError:
            if body.startswith("http://") or body.startswith("https://"):
                return {"video_url": body}, parser_url
            raise DouyinDownloadError("解析服务没有返回可识别的 JSON 或视频地址。")

    @staticmethod
    def _build_parser_url(base_url: str, share_url: str) -> str:
        if not base_url:
            raise DouyinDownloadError("未配置解析服务地址，请检查 runtime/downloader_config.json。")

        parts = urlsplit(base_url)
        query = parse_qsl(parts.query, keep_blank_values=True)
        query = [(key, value) for key, value in query if key.lower() not in {"url", "data"}]
        query.append(("url", share_url))
        query.append(("data", ""))
        rebuilt_query = urlencode(query, doseq=True)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, rebuilt_query, parts.fragment))

    def _download_file(
        self,
        source_url: str,
        target_path: Path,
        progress_callback: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> None:
        request = Request(source_url, headers=self._default_headers(include_referer=True))
        timeout = float(self.load_config().get("timeout_seconds", 45))

        try:
            with urlopen(request, timeout=timeout) as response, open(target_path, "wb") as handle:
                total = int(response.headers.get("Content-Length") or 0)
                downloaded = 0
                while True:
                    self._check_cancelled(should_cancel)
                    chunk = response.read(1024 * 128)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback is not None:
                        progress_callback(downloaded, total)
        except Exception as exc:  # noqa: BLE001
            if target_path.exists():
                target_path.unlink(missing_ok=True)
            raise DouyinDownloadError(f"下载视频失败：{exc}") from exc

    def _default_headers(self, include_referer: bool = False) -> dict[str, str]:
        config = self.load_config()
        headers = {
            "User-Agent": str(config.get("user_agent", _DEFAULT_CONFIG["user_agent"])),
            "Accept": "application/json,text/plain,*/*",
        }
        if include_referer:
            headers["Referer"] = "https://www.douyin.com/"
        return headers

    def _extract_video_url(self, payload: Any) -> str:
        candidates: list[tuple[int, str]] = []
        for key_path, value in self._walk(payload):
            if not isinstance(value, str):
                continue
            url = value.strip()
            if not (url.startswith("http://") or url.startswith("https://")):
                continue
            score = self._score_candidate(key_path, url)
            if score > 0:
                candidates.append((score, url))

        if not candidates:
            raise DouyinDownloadError("解析结果里没有找到可下载的视频地址。")

        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def _extract_text(self, payload: Any, preferred_keys: tuple[str, ...]) -> str | None:
        for key_path, value in self._walk(payload):
            if not isinstance(value, str):
                continue
            lowered_path = ".".join(key_path).lower()
            if any(preferred in lowered_path for preferred in preferred_keys):
                candidate = value.strip()
                if candidate and not candidate.startswith("http"):
                    return candidate[:80]
        return None

    @staticmethod
    def _score_candidate(key_path: tuple[str, ...], url: str) -> int:
        lowered_path = ".".join(key_path).lower()
        lowered_url = url.lower()

        if any(token in lowered_path for token in ("cover", "music", "avatar", "image", "images")):
            return 0

        score = 0
        if "video" in lowered_path:
            score += 30
        if any(token in lowered_path for token in ("nwm", "no_water", "nowater", "play")):
            score += 25
        if lowered_url.endswith(".mp4"):
            score += 20
        if "video" in lowered_url:
            score += 10
        if "watermark" in lowered_url:
            score -= 10
        return score

    @staticmethod
    def _walk(payload: Any, key_path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
        items: list[tuple[tuple[str, ...], Any]] = []
        if isinstance(payload, dict):
            for key, value in payload.items():
                items.extend(DouyinDownloadService._walk(value, key_path + (str(key),)))
            return items
        if isinstance(payload, list):
            for index, value in enumerate(payload):
                items.extend(DouyinDownloadService._walk(value, key_path + (str(index),)))
            return items
        items.append((key_path, payload))
        return items

    @staticmethod
    def _guess_suffix(video_url: str) -> str:
        path = urlsplit(video_url).path
        suffix = Path(path).suffix.lower()
        if suffix in {".mp4", ".mov", ".m4v"}:
            return suffix
        return ".mp4"

    @staticmethod
    def _check_cancelled(should_cancel: CancelCallback | None) -> None:
        if should_cancel is not None and should_cancel():
            raise DouyinDownloadError("下载已取消")
