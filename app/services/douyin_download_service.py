from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import requests
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from app.config.settings import DOWNLOADER_CONFIG_PATH
from app.utils.paths import build_download_output_path

ProgressCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]

_CONFIG_PATH = DOWNLOADER_CONFIG_PATH
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
            raise DouyinDownloadError("未识别到有效的抖音分享链接，请粘贴包含 https:// 的完整文本。")

        payload, parser_url = self._resolve_share_url(share_url, should_cancel=should_cancel)
        video_urls = self._extract_video_urls(payload)
        title = self._extract_text(payload, ("title", "desc", "aweme_id"))
        author = self._extract_text(payload, ("author", "nickname", "sec_uid"))

        errors: list[str] = []
        for index, video_url in enumerate(video_urls, start=1):
            self._check_cancelled(should_cancel)
            suffix = self._guess_suffix(video_url)
            local_path = build_download_output_path(title or "douyin_video", suffix=suffix)
            try:
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
            except DouyinDownloadError as exc:
                if should_cancel is not None and should_cancel():
                    raise
                errors.append(f"候选链接 {index}: {exc}")

        detail = "；".join(errors[:3])
        if len(errors) > 3:
            detail += "；其余候选链接也已失败"
        raise DouyinDownloadError(f"下载视频失败，所有候选直链均不可用。{detail}")

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
            raise DouyinDownloadError("下载能力已被禁用，请检查 runtime/downloader_config.json。")

        self._check_cancelled(should_cancel)
        parser_url = self._build_parser_url(str(config.get("parser_base_url", "")), share_url)

        try:
            response = self._session().get(
                parser_url,
                headers=self._default_headers(),
                timeout=float(config.get("timeout_seconds", 45)),
                allow_redirects=True,
            )
            response.raise_for_status()
            body = response.text.strip()
        except Exception as exc:  # noqa: BLE001
            raise DouyinDownloadError(f"解析抖音分享链接失败: {exc}") from exc

        self._check_cancelled(should_cancel)
        try:
            return json.loads(body), parser_url
        except json.JSONDecodeError:
            if body.startswith("http://") or body.startswith("https://"):
                return {"video_url": body}, parser_url
            raise DouyinDownloadError("解析服务返回的内容不是可用的 JSON 或视频直链。")

    @staticmethod
    def _build_parser_url(base_url: str, share_url: str) -> str:
        if not base_url:
            raise DouyinDownloadError("解析服务地址为空，请检查 runtime/downloader_config.json。")

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
        timeout = float(self.load_config().get("timeout_seconds", 45))
        last_error: Exception | None = None

        for headers in self._download_header_variants(source_url):
            self._check_cancelled(should_cancel)
            try:
                with self._session().get(
                    source_url,
                    headers=headers,
                    timeout=timeout,
                    allow_redirects=True,
                    stream=True,
                ) as response, open(target_path, "wb") as handle:
                    response.raise_for_status()
                    total = int(response.headers.get("Content-Length") or 0)
                    downloaded = 0
                    for chunk in response.iter_content(chunk_size=1024 * 128):
                        self._check_cancelled(should_cancel)
                        if not chunk:
                            continue
                        handle.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback is not None:
                            progress_callback(downloaded, total)
                return
            except requests.HTTPError as exc:
                last_error = exc
                if target_path.exists():
                    target_path.unlink(missing_ok=True)
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code not in {401, 403, 429}:
                    break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if target_path.exists():
                    target_path.unlink(missing_ok=True)

        if target_path.exists():
            target_path.unlink(missing_ok=True)

        if isinstance(last_error, requests.HTTPError):
            status_code = last_error.response.status_code if last_error.response is not None else "unknown"
            raise DouyinDownloadError(f"直链请求被拒绝，HTTP {status_code}") from last_error
        if last_error is not None:
            raise DouyinDownloadError(f"下载直链失败: {last_error}") from last_error
        raise DouyinDownloadError("下载直链失败，未获取到可用响应。")

    @staticmethod
    def _session() -> requests.Session:
        session = requests.Session()
        session.trust_env = False
        return session

    @staticmethod
    def _open_url(request: Request, *, timeout: float):
        try:
            return urlopen(request, timeout=timeout)
        except (URLError, OSError) as exc:
            if not DouyinDownloadService._should_retry_without_proxy(exc):
                raise
            return DouyinDownloadService._open_url_without_proxy(request, timeout=timeout)

    def _default_headers(self) -> dict[str, str]:
        config = self.load_config()
        return {
            "User-Agent": str(config.get("user_agent", _DEFAULT_CONFIG["user_agent"])),
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Connection": "keep-alive",
        }

    def _download_header_variants(self, source_url: str) -> list[dict[str, str]]:
        host = urlsplit(source_url).netloc
        base = self._default_headers()
        variants = [
            {
                **base,
                "Referer": "https://www.douyin.com/",
                "Origin": "https://www.douyin.com",
            },
            {
                **base,
                "Referer": "https://www.douyin.com/",
                "Origin": "https://www.douyin.com",
                "Range": "bytes=0-",
            },
            {
                **base,
                "Referer": "https://www.douyin.com/",
                "Origin": "https://www.douyin.com",
                "Range": "bytes=0-",
                "Host": host,
            },
            {
                **base,
                "Range": "bytes=0-",
                "Host": host,
            },
            {
                **base,
                "Host": host,
            },
            base,
        ]

        unique_variants: list[dict[str, str]] = []
        seen: set[tuple[tuple[str, str], ...]] = set()
        for headers in variants:
            marker = tuple(sorted(headers.items()))
            if marker in seen:
                continue
            seen.add(marker)
            unique_variants.append(headers)
        return unique_variants

    def _extract_video_urls(self, payload: Any) -> list[str]:
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
            raise DouyinDownloadError("解析结果中没有找到可用的视频直链。")

        candidates.sort(key=lambda item: item[0], reverse=True)
        ordered_urls: list[str] = []
        seen: set[str] = set()
        for _, url in candidates:
            if url in seen:
                continue
            seen.add(url)
            ordered_urls.append(url)
        return ordered_urls

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
        if any(token in lowered_path for token in ("nwm", "no_water", "nowater", "play", "play_addr", "url_list")):
            score += 25
        if lowered_url.endswith(".mp4"):
            score += 20
        if any(token in lowered_url for token in ("video", "play", "aweme")):
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
            raise DouyinDownloadError("下载已取消。")

    @staticmethod
    def _should_retry_without_proxy(exc: BaseException) -> bool:
        return isinstance(exc, (URLError, OSError))

    @staticmethod
    def _open_url_without_proxy(request: Request, *, timeout: float):
        proxy_keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
        previous = {key: os.environ.pop(key, None) for key in proxy_keys}
        previous_no_proxy = os.environ.get("NO_PROXY")
        previous_no_proxy_lower = os.environ.get("no_proxy")
        os.environ["NO_PROXY"] = "*"
        os.environ["no_proxy"] = "*"
        try:
            opener = build_opener(ProxyHandler({}))
            return opener.open(request, timeout=timeout)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            if previous_no_proxy is None:
                os.environ.pop("NO_PROXY", None)
            else:
                os.environ["NO_PROXY"] = previous_no_proxy
            if previous_no_proxy_lower is None:
                os.environ.pop("no_proxy", None)
            else:
                os.environ["no_proxy"] = previous_no_proxy_lower
