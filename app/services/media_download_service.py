from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from app.config.settings import PROJECT_ROOT
from app.services.douyin_download_service import DouyinDownloadService
from app.utils.ffmpeg import FFmpegError, merge_av_streams
from app.utils.paths import build_article_session_dir, build_download_output_path

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


class MediaDownloadError(RuntimeError):
    pass


@dataclass
class MediaDownloadResult:
    platform: str
    share_url: str
    resolved_url: str
    local_path: str
    kind: str = "video"
    title: str | None = None
    author: str | None = None
    media_url: str | None = None
    article_text: str | None = None
    image_paths: list[str] = field(default_factory=list)


class MultiPlatformDownloadService:
    def __init__(self) -> None:
        self._config: dict | None = None
        self._douyin_service = DouyinDownloadService()

    @staticmethod
    def extract_share_url(text: str) -> str | None:
        if not text:
            return None
        match = _URL_PATTERN.search(text)
        return match.group(0).rstrip(".,);") if match else None

    def load_config(self) -> dict:
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

    def download_from_text(self, text: str, progress_callback=None, should_cancel=None) -> MediaDownloadResult:
        share_url = self.extract_share_url(text)
        if not share_url:
            raise MediaDownloadError("没有识别到分享链接，请粘贴包含 https:// 的分享文本。")

        platform = self.detect_platform(share_url)
        if platform == "douyin":
            return self._download_douyin(text, progress_callback, should_cancel)
        if platform == "kuaishou":
            return self._download_kuaishou(share_url, progress_callback, should_cancel)
        if platform == "xiaohongshu":
            return self._download_xiaohongshu(share_url, progress_callback, should_cancel)
        if platform == "bilibili":
            return self._download_bilibili(share_url, should_cancel)
        if platform == "wechat_article":
            return self._download_wechat_article(share_url, progress_callback, should_cancel)
        raise MediaDownloadError("暂不支持这个链接来源。当前支持抖音、快手、小红书、B 站和微信公众号文章。")

    def detect_platform(self, share_url: str) -> str:
        host = urlsplit(share_url).netloc.lower()
        if any(token in host for token in ("douyin.com", "iesdouyin.com")):
            return "douyin"
        if any(token in host for token in ("kuaishou.com", "chenzhongtech.com", "yximgs.com", "kwai")):
            return "kuaishou"
        if any(token in host for token in ("xiaohongshu.com", "xhslink.com", "xhscdn.com")):
            return "xiaohongshu"
        if any(token in host for token in ("b23.tv", "bilibili.com", "bilivideo.com")):
            return "bilibili"
        if "mp.weixin.qq.com" in host:
            return "wechat_article"
        return "unknown"

    def _download_douyin(self, text: str, progress_callback=None, should_cancel=None) -> MediaDownloadResult:
        try:
            result = self._douyin_service.download_from_text(
                text,
                progress_callback=progress_callback,
                should_cancel=should_cancel,
            )
        except Exception as exc:  # noqa: BLE001
            raise MediaDownloadError(str(exc)) from exc

        return MediaDownloadResult(
            platform="抖音",
            share_url=result.share_url,
            resolved_url=result.parser_url,
            local_path=result.local_path,
            title=result.title,
            author=result.author,
            media_url=result.video_url,
        )

    def _download_kuaishou(self, share_url: str, progress_callback=None, should_cancel=None) -> MediaDownloadResult:
        self._check_cancelled(should_cancel)
        final_url, html_text = self._fetch_html(share_url, referer="https://www.kuaishou.com/")
        if '"photoType":"VIDEO"' not in html_text and '"photoType":"IMAGE"' in html_text:
            raise MediaDownloadError("这个快手分享更像图文内容，当前下载器只处理视频。")

        normalized_html = self._normalize_html_url(html_text)
        media_urls = [self._normalize_html_url(url) for url in re.findall(r"https?://[^\"'\s<>]+", normalized_html)]
        apollo_state = self._extract_kuaishou_apollo_state(html_text)
        if apollo_state is not None:
            media_urls.extend(self._extract_kuaishou_video_urls_from_state(apollo_state))
        video_url = self._select_kuaishou_video_url(media_urls)
        title = self._extract_kuaishou_text(apollo_state, ("caption", "title")) or self._extract_json_text(
            normalized_html,
            ("caption",),
        )
        author = self._extract_kuaishou_text(apollo_state, ("userName", "authorName", "name")) or self._extract_json_text(
            normalized_html,
            ("userName", "authorName", "name"),
        )
        output_path = build_download_output_path(title or "kuaishou_video", suffix=self._guess_suffix(video_url))
        self._download_file(
            video_url,
            output_path,
            referer="https://www.kuaishou.com/",
            progress_callback=progress_callback,
            should_cancel=should_cancel,
        )

        return MediaDownloadResult(
            platform="快手",
            share_url=share_url,
            resolved_url=final_url,
            local_path=str(output_path),
            title=title,
            author=author,
            media_url=video_url,
        )

    def _download_xiaohongshu(self, share_url: str, progress_callback=None, should_cancel=None) -> MediaDownloadResult:
        self._check_cancelled(should_cancel)
        final_url, html_text = self._fetch_html(share_url, referer="https://www.xiaohongshu.com/")
        if "type=video" not in final_url and "og:video" not in html_text and '"video":{' not in html_text:
            raise MediaDownloadError("这个小红书链接没有识别到视频资源。")

        video_url = self._extract_xiaohongshu_video_url(html_text)
        title = self._extract_meta_content(html_text, "og:title") or self._extract_json_text(html_text, ("title", "noteTitle"))
        author = self._extract_json_text(html_text, ("nickname", "userNickname", "author"))
        output_path = build_download_output_path(title or "xiaohongshu_video", suffix=".mp4")
        self._download_file(
            video_url,
            output_path,
            referer="https://www.xiaohongshu.com/",
            progress_callback=progress_callback,
            should_cancel=should_cancel,
        )

        return MediaDownloadResult(
            platform="小红书",
            share_url=share_url,
            resolved_url=final_url,
            local_path=str(output_path),
            title=title,
            author=author,
            media_url=video_url,
        )

    def _download_bilibili(self, share_url: str, should_cancel=None) -> MediaDownloadResult:
        self._check_cancelled(should_cancel)
        final_url = self._resolve_final_url(share_url, referer="https://www.bilibili.com/")
        bvid_match = re.search(r"/video/(BV[0-9A-Za-z]+)", final_url)
        if not bvid_match:
            raise MediaDownloadError("没有从 B 站链接中识别到 BV 号。")
        bvid = bvid_match.group(1)

        view_api = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        view_payload = self._fetch_json(view_api, referer=f"https://www.bilibili.com/video/{bvid}/")
        if int(view_payload.get("code", -1)) != 0:
            raise MediaDownloadError("B 站视频信息接口返回异常。")

        view_data = view_payload.get("data") or {}
        cid = view_data.get("cid")
        title = view_data.get("title") or bvid
        owner = view_data.get("owner") or {}
        author = owner.get("name")
        if not cid:
            raise MediaDownloadError("B 站视频缺少 cid，无法继续获取播放流。")

        play_api = (
            f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}"
            "&qn=80&fnval=16&fnver=0&fourk=1"
        )
        play_payload = self._fetch_json(play_api, referer=f"https://www.bilibili.com/video/{bvid}/")
        if int(play_payload.get("code", -1)) != 0:
            raise MediaDownloadError("B 站播放接口返回异常。")

        dash = (play_payload.get("data") or {}).get("dash") or {}
        video_streams = dash.get("video") or []
        audio_streams = dash.get("audio") or []
        if not video_streams or not audio_streams:
            raise MediaDownloadError("B 站当前视频没有返回可合并的音视频流。")

        video_stream = max(video_streams, key=lambda item: int(item.get("bandwidth", 0)))
        audio_stream = max(audio_streams, key=lambda item: int(item.get("bandwidth", 0)))
        video_url = video_stream.get("baseUrl") or video_stream.get("base_url")
        audio_url = audio_stream.get("baseUrl") or audio_stream.get("base_url")
        if not video_url or not audio_url:
            raise MediaDownloadError("B 站播放流地址不完整。")

        output_path = build_download_output_path(title, suffix=".mp4")
        video_temp_path = output_path.parent / f"{output_path.stem}.video.m4s"
        audio_temp_path = output_path.parent / f"{output_path.stem}.audio.m4s"

        try:
            self._download_file(
                video_url,
                video_temp_path,
                referer=f"https://www.bilibili.com/video/{bvid}/",
                should_cancel=should_cancel,
            )
            self._download_file(
                audio_url,
                audio_temp_path,
                referer=f"https://www.bilibili.com/video/{bvid}/",
                should_cancel=should_cancel,
            )
            self._check_cancelled(should_cancel)
            merge_av_streams(video_temp_path, audio_temp_path, output_path)
        except FFmpegError as exc:
            raise MediaDownloadError(str(exc)) from exc
        finally:
            video_temp_path.unlink(missing_ok=True)
            audio_temp_path.unlink(missing_ok=True)

        return MediaDownloadResult(
            platform="B站",
            share_url=share_url,
            resolved_url=final_url,
            local_path=str(output_path),
            title=title,
            author=author,
            media_url=video_url,
        )

    def _download_wechat_article(self, share_url: str, progress_callback=None, should_cancel=None) -> MediaDownloadResult:
        self._check_cancelled(should_cancel)
        if progress_callback is not None:
            progress_callback(5, 100)
        final_url, html_text = self._fetch_html(share_url, referer="https://mp.weixin.qq.com/")
        self._check_cancelled(should_cancel)

        title = self._extract_wechat_title(html_text) or "wechat_article"
        author = self._extract_wechat_author(html_text)
        content_html = self._extract_wechat_content_html(html_text)
        article_text = self._extract_wechat_text(content_html)
        image_urls = self._extract_wechat_image_urls(content_html)

        article_dir = build_article_session_dir(title)
        image_paths: list[str] = []
        total_images = max(len(image_urls), 1)
        for index, image_url in enumerate(image_urls, start=1):
            self._check_cancelled(should_cancel)
            if progress_callback is not None:
                progress_callback(10 + int(index * 90 / total_images), 100)
            suffix = self._guess_image_suffix(image_url)
            image_path = article_dir / f"image_{index:03d}{suffix}"
            self._download_file(image_url, image_path, referer=final_url, should_cancel=should_cancel)
            image_paths.append(str(image_path))

        if progress_callback is not None:
            progress_callback(100, 100)

        if not article_text.strip() and not image_paths:
            raise MediaDownloadError("微信公众号文章里没有提取到可用正文或图片。")

        (article_dir / "article.txt").write_text(article_text, encoding="utf-8")

        return MediaDownloadResult(
            platform="微信公众号",
            share_url=share_url,
            resolved_url=final_url,
            local_path=str(article_dir),
            kind="article",
            title=title,
            author=author,
            article_text=article_text,
            image_paths=image_paths,
        )

    def _fetch_html(self, url: str, referer: str | None = None) -> tuple[str, str]:
        request = Request(url, headers=self._build_headers(referer=referer))
        try:
            with urlopen(request, timeout=float(self.load_config().get("timeout_seconds", 45))) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                text = response.read().decode(charset, errors="replace")
                return response.geturl(), text
        except Exception as exc:  # noqa: BLE001
            raise MediaDownloadError(f"访问分享页面失败：{exc}") from exc

    def _resolve_final_url(self, url: str, referer: str | None = None) -> str:
        request = Request(url, headers=self._build_headers(referer=referer))
        try:
            with urlopen(request, timeout=float(self.load_config().get("timeout_seconds", 45))) as response:
                return response.geturl()
        except Exception as exc:  # noqa: BLE001
            raise MediaDownloadError(f"展开短链失败：{exc}") from exc

    def _fetch_json(self, url: str, referer: str | None = None) -> dict:
        request = Request(url, headers=self._build_headers(referer=referer, accept="application/json,text/plain,*/*"))
        try:
            with urlopen(request, timeout=float(self.load_config().get("timeout_seconds", 45))) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return json.loads(response.read().decode(charset, errors="replace"))
        except Exception as exc:  # noqa: BLE001
            raise MediaDownloadError(f"请求接口失败：{exc}") from exc

    def _download_file(
        self,
        source_url: str,
        target_path: Path,
        *,
        referer: str | None = None,
        progress_callback=None,
        should_cancel=None,
    ) -> None:
        request = Request(source_url, headers=self._build_headers(referer=referer, accept="*/*"))
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
            target_path.unlink(missing_ok=True)
            raise MediaDownloadError(f"下载媒体文件失败：{exc}") from exc

    @staticmethod
    def _check_cancelled(should_cancel) -> None:
        if should_cancel is not None and should_cancel():
            raise MediaDownloadError("下载已取消")

    def _build_headers(self, *, referer: str | None = None, accept: str = "*/*") -> dict[str, str]:
        config = self.load_config()
        headers = {
            "User-Agent": str(config.get("user_agent", _DEFAULT_CONFIG["user_agent"])),
            "Accept": accept,
        }
        if referer:
            headers["Referer"] = referer
        return headers

    @staticmethod
    def _normalize_html_url(url: str) -> str:
        return html.unescape(url).replace("\\u002F", "/")

    @staticmethod
    def _extract_meta_content(html_text: str, meta_name: str) -> str | None:
        pattern = rf'<meta\s+(?:name|property)="{re.escape(meta_name)}"\s+content="([^"]+)"'
        match = re.search(pattern, html_text, re.IGNORECASE)
        return html.unescape(match.group(1)).strip() if match else None

    @staticmethod
    def _extract_json_text(html_text: str, field_names: tuple[str, ...]) -> str | None:
        for field_name in field_names:
            pattern = rf'"{re.escape(field_name)}":"([^"]+)"'
            match = re.search(pattern, html_text, re.IGNORECASE)
            if not match:
                continue
            value = MultiPlatformDownloadService._normalize_html_url(match.group(1)).strip()
            if value and not value.startswith("http"):
                return value[:120]
        return None

    def _extract_xiaohongshu_video_url(self, html_text: str) -> str:
        patterns = [
            r'<meta name="og:video" content="([^"]+)"',
            r'"masterUrl":"(http[^"]+?\.mp4[^"]*)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if match:
                return self._normalize_html_url(match.group(1))
        raise MediaDownloadError("小红书页面里没有找到视频地址。")

    def _select_kuaishou_video_url(self, urls: list[str]) -> str:
        candidates: list[tuple[int, str]] = []
        for url in urls:
            lowered = url.lower()
            if ".mp4" not in lowered:
                continue
            score = 0
            if "tt=b" in lowered or "_b." in lowered:
                score += 50
            if "kwai-not-alloc=0" in lowered:
                score += 10
            if "photo-video" in lowered:
                score -= 5
            score += len(url) // 50
            candidates.append((score, url))

        if not candidates:
            raise MediaDownloadError("快手页面里没有找到可下载的视频地址。")

        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    @staticmethod
    def _extract_kuaishou_apollo_state(html_text: str) -> dict | None:
        match = re.search(r"window\.__APOLLO_STATE__=(\{.*?\})</script>", html_text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(MultiPlatformDownloadService._normalize_html_url(match.group(1)))
        except json.JSONDecodeError:
            return None

    @classmethod
    def _extract_kuaishou_video_urls_from_state(cls, apollo_state: dict) -> list[str]:
        urls: list[str] = []
        for key_path, value in cls._walk_payload(apollo_state):
            if not isinstance(value, str):
                continue
            lowered_path = ".".join(key_path).lower()
            if ".mp4" not in value.lower():
                continue
            if not any(token in lowered_path for token in ("videoresource", "manifest", "representation", "backupurl", "url")):
                continue
            normalized = cls._normalize_html_url(value.strip())
            if normalized.startswith("http://") or normalized.startswith("https://"):
                urls.append(normalized)
        return urls

    @classmethod
    def _extract_kuaishou_text(cls, apollo_state: dict | None, field_names: tuple[str, ...]) -> str | None:
        if apollo_state is None:
            return None
        for key_path, value in cls._walk_payload(apollo_state):
            if not isinstance(value, str):
                continue
            lowered_path = ".".join(key_path).lower()
            if any(field_name.lower() in lowered_path for field_name in field_names):
                candidate = cls._normalize_html_url(value).strip()
                if candidate and not candidate.startswith("http"):
                    return candidate[:120]
        return None

    @staticmethod
    def _walk_payload(payload, key_path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], object]]:
        items: list[tuple[tuple[str, ...], object]] = []
        if isinstance(payload, dict):
            for key, value in payload.items():
                items.extend(MultiPlatformDownloadService._walk_payload(value, key_path + (str(key),)))
            return items
        if isinstance(payload, list):
            for index, value in enumerate(payload):
                items.extend(MultiPlatformDownloadService._walk_payload(value, key_path + (str(index),)))
            return items
        items.append((key_path, payload))
        return items

    def _extract_wechat_title(self, html_text: str) -> str | None:
        patterns = [
            r'<meta property="og:title" content="([^"]+)"',
            r'<h1[^>]*id="activity-name"[^>]*>(.*?)</h1>',
            r"var msg_title = '(.*?)';",
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text, re.IGNORECASE | re.DOTALL)
            if match:
                return self._clean_html_text(match.group(1))
        return None

    def _extract_wechat_author(self, html_text: str) -> str | None:
        patterns = [
            r'<meta name="author" content="([^"]+)"',
            r"var nickname = htmlDecode\(\"(.*?)\"\);",
            r"var user_name = \"(.*?)\";",
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text, re.IGNORECASE | re.DOTALL)
            if match:
                value = self._clean_html_text(match.group(1))
                if value:
                    return value
        return None

    def _extract_wechat_content_html(self, html_text: str) -> str:
        match = re.search(r'<div[^>]+id="js_content"[^>]*>(.*)</div>\s*<script', html_text, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1)
        match = re.search(r'<div[^>]+id="js_content"[^>]*>(.*)</div>', html_text, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1)
        raise MediaDownloadError("没有在公众号文章中找到正文区域。")

    def _extract_wechat_text(self, content_html: str) -> str:
        text = re.sub(r"<\s*br\s*/?\s*>", "\n", content_html, flags=re.IGNORECASE)
        text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<script.*?</script>", "", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<style.*?</style>", "", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<[^>]+>", "", text)
        text = self._clean_html_text(text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _extract_wechat_image_urls(self, content_html: str) -> list[str]:
        urls: list[str] = []
        for pattern in (r'data-src="([^"]+)"', r'src="([^"]+)"'):
            for url in re.findall(pattern, content_html, re.IGNORECASE):
                normalized = self._normalize_html_url(url.strip())
                if not normalized.startswith("http"):
                    continue
                if normalized in urls:
                    continue
                urls.append(normalized)
        return urls

    @staticmethod
    def _clean_html_text(text: str) -> str:
        normalized = html.unescape(text).replace("\xa0", " ")
        normalized = re.sub(r"\s+\n", "\n", normalized)
        normalized = re.sub(r"\n\s+", "\n", normalized)
        return normalized.strip()

    @staticmethod
    def _guess_suffix(media_url: str) -> str:
        path = urlsplit(media_url).path
        suffix = Path(path).suffix.lower()
        if suffix in {".mp4", ".mov", ".m4v", ".m4s"}:
            return suffix
        return ".mp4"

    @staticmethod
    def _guess_image_suffix(media_url: str) -> str:
        path = urlsplit(media_url).path
        suffix = Path(path).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            return suffix
        return ".jpg"
