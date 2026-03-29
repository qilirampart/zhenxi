from __future__ import annotations

import base64
import json
from typing import Any

import cv2
import numpy as np

from app.models.ocr import OCRLine
from app.services.api_config_service import APIConfigService

_DEFAULT_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


class APIEngineError(RuntimeError):
    pass


class APIEngine:
    """基于 OpenAI 兼容接口的视觉 OCR 通道，支持多 API 通道自动降级。"""

    def __init__(self) -> None:
        self._clients: dict[str, Any] = {}
        self._client_signatures: dict[str, tuple[str, str, str, float]] = {}
        self._service = APIConfigService()

    def load_config(self) -> dict[str, Any]:
        return self._service.load_config()

    def is_enabled(self) -> bool:
        providers = self._service.get_fallback_providers(self.load_config())
        return any(provider.get("api_key") for provider in providers)

    def recognize(self, frame_bgr: np.ndarray) -> tuple[str, list[OCRLine]]:
        providers = self._service.get_fallback_providers(self.load_config())
        if not providers:
            raise APIEngineError("云端 API 未启用，请先在 API 配置中启用至少一个通道。")

        b64 = self._encode_image(frame_bgr)
        failures: list[str] = []

        for provider in providers:
            try:
                return self._recognize_with_provider(provider, b64)
            except Exception as exc:  # noqa: BLE001
                message = self._format_api_error(exc)
                failures.append(f"{provider.get('name', '未命名通道')}: {message}")

        raise APIEngineError("所有 API 通道均失败：\n" + "\n".join(failures))

    def _recognize_with_provider(self, provider: dict[str, Any], image_b64: str) -> tuple[str, list[OCRLine]]:
        api_key = str(provider.get("api_key", "")).strip()
        if not api_key:
            raise APIEngineError("API Key 未配置。")

        prompt = str(provider.get("prompt", "")).strip() or (
            "请提取这张图片中所有可见的文字，按从上到下、从左到右的顺序输出，"
            "每行文字单独一行，只输出文字内容本身。"
        )
        model = str(provider.get("model", "")).strip() or "gpt-4o"
        max_tokens = int(provider.get("max_tokens", 1000))

        client = self._get_client(provider)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                        },
                    ],
                }
            ],
            max_tokens=max_tokens,
        )

        raw = self._extract_response_text(response)
        return self._parse_response(raw)

    def _get_client(self, provider: dict[str, Any]):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise APIEngineError("请先安装 openai 库：pip install openai") from exc

        provider_id = str(provider.get("id") or "default")
        signature = (
            provider_id,
            str(provider.get("api_key", "")),
            str(provider.get("base_url", "https://api.openai.com/v1")),
            float(provider.get("timeout_seconds", 30)),
        )

        if self._clients.get(provider_id) is None or self._client_signatures.get(provider_id) != signature:
            self._service.prepare_network_env()
            self._clients[provider_id] = OpenAI(
                api_key=provider.get("api_key", ""),
                base_url=provider.get("base_url", "https://api.openai.com/v1"),
                timeout=float(provider.get("timeout_seconds", 30)),
                default_headers=dict(_DEFAULT_BROWSER_HEADERS),
            )
            self._client_signatures[provider_id] = signature
        return self._clients[provider_id]

    @staticmethod
    def _encode_image(frame_bgr: np.ndarray) -> str:
        height, width = frame_bgr.shape[:2]
        max_side = 1280
        if max(height, width) > max_side:
            scale = max_side / max(height, width)
            frame_bgr = cv2.resize(frame_bgr, (int(width * scale), int(height * scale)))

        ok, buffer = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise APIEngineError("图片编码失败，无法发送到云端 OCR。")
        return base64.b64encode(buffer.tobytes()).decode()

    @staticmethod
    def _format_api_error(exc: Exception) -> str:
        error_name = type(exc).__name__
        detail = str(exc).strip()

        if error_name == "APIConnectionError":
            suffix = f"：{detail}" if detail else ""
            return f"API 连接失败，请检查网络或代理设置{suffix}"
        if error_name == "AuthenticationError":
            return "API Key 无效或已过期。"
        if "1010" in detail or "request was blocked" in detail.lower():
            return "服务端风控拦截了当前客户端请求（1010）。"
        if error_name == "BadRequestError":
            suffix = f"：{detail}" if detail else ""
            return f"请求格式错误（可能不支持 data URL 图片或模型不支持视觉输入）{suffix}"
        if error_name == "RateLimitError":
            return "API 请求频率超限，请稍后重试。"
        if isinstance(exc, APIEngineError):
            return detail or "OCR 识别失败"
        return detail or "OCR 识别失败"

    @staticmethod
    def _parse_response(raw: str) -> tuple[str, list[OCRLine]]:
        lines: list[OCRLine] = []
        text_parts: list[str] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            lines.append(OCRLine(text=stripped, confidence=1.0, box=[]))
            text_parts.append(stripped)
        merged = "\n".join(text_parts).strip()
        return merged, lines

    @classmethod
    def _extract_response_text(cls, payload: Any) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            return cls._extract_text_from_string(payload)

        choices = getattr(payload, "choices", None)
        if choices:
            first_choice = choices[0]
            message = getattr(first_choice, "message", None)
            if message is not None:
                return cls._extract_text_from_content(getattr(message, "content", ""))
            delta = getattr(first_choice, "delta", None)
            if delta is not None:
                return cls._extract_text_from_content(getattr(delta, "content", ""))

        if isinstance(payload, dict):
            choices = payload.get("choices") or []
            if choices:
                first_choice = choices[0] or {}
                if isinstance(first_choice, dict):
                    message = first_choice.get("message") or {}
                    if isinstance(message, dict) and message.get("content") is not None:
                        return cls._extract_text_from_content(message.get("content"))
                    delta = first_choice.get("delta") or {}
                    if isinstance(delta, dict) and delta.get("content") is not None:
                        return cls._extract_text_from_content(delta.get("content"))

        return cls._extract_text_from_string(str(payload))

    @classmethod
    def _extract_text_from_content(cls, content: Any) -> str:
        if isinstance(content, str):
            return cls._extract_text_from_string(content)
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                else:
                    text = getattr(item, "text", None)
                if text:
                    parts.append(str(text).strip())
            return "\n".join(part for part in parts if part).strip()
        return cls._extract_text_from_string(str(content))

    @staticmethod
    def _extract_text_from_string(raw: str) -> str:
        text = (raw or "").strip()
        if not text:
            return ""
        if "data:" not in text:
            return text

        chunks: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("data:"):
                continue
            data = stripped[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue

            for choice in payload.get("choices") or []:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta") or {}
                if isinstance(delta, dict) and delta.get("content"):
                    chunks.append(str(delta["content"]))
                message = choice.get("message") or {}
                if isinstance(message, dict) and message.get("content"):
                    chunks.append(str(message["content"]))
        return "".join(chunks).strip() or text
