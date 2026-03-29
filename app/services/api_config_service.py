from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

CONFIG_PATH = Path(__file__).resolve().parents[2] / "runtime" / "api_config.json"
MAX_API_PROVIDERS = 5
DEFAULT_PROVIDER_TEMPLATE: dict[str, Any] = {
    "name": "默认通道",
    "base_url": "https://api.openai.com/v1",
    "api_key": "",
    "model": "gpt-4o",
    "prompt": "请提取这张图片中所有可见的文字，按从上到下、从左到右的顺序逐行输出，只返回文字内容。",
    "timeout_seconds": 30,
    "max_tokens": 1000,
    "enabled": True,
}
DEFAULT_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


class APIConfigValidationError(ValueError):
    pass


class APIConfigTestError(RuntimeError):
    pass


class APIConfigService:
    def load_config(self) -> dict[str, Any]:
        if not CONFIG_PATH.exists():
            config = self._build_default_config()
            self.save_config(config)
            return config

        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        normalized = self.normalize_config(raw)
        return normalized

    def save_config(self, config: dict[str, Any]) -> dict[str, Any]:
        normalized = self.validate_config(config, require_api_key=False)
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
        return normalized

    def normalize_config(self, raw: dict[str, Any] | None) -> dict[str, Any]:
        payload = raw or {}
        if isinstance(payload.get("providers"), list):
            providers = payload.get("providers") or []
            active_provider_id = str(payload.get("active_provider_id") or "").strip()
            normalized_providers = [
                self._normalize_provider(provider, fallback_name=f"通道 {index}")
                for index, provider in enumerate(providers, start=1)
                if isinstance(provider, dict)
            ][:MAX_API_PROVIDERS]
        else:
            legacy_provider = self._normalize_provider(payload, fallback_name="默认通道")
            normalized_providers = [legacy_provider]
            active_provider_id = legacy_provider["id"]

        if not normalized_providers:
            normalized_providers = [self.build_provider(name="默认通道")]

        provider_ids = {provider["id"] for provider in normalized_providers}
        if active_provider_id not in provider_ids:
            active_provider_id = normalized_providers[0]["id"]

        return {
            "active_provider_id": active_provider_id,
            "providers": normalized_providers,
        }

    def validate_config(self, config: dict[str, Any], *, require_api_key: bool) -> dict[str, Any]:
        payload = self.normalize_config(config)
        providers = payload["providers"]
        if not providers:
            raise APIConfigValidationError("至少需要保留一个 API 通道。")
        if len(providers) > MAX_API_PROVIDERS:
            raise APIConfigValidationError(f"最多只能配置 {MAX_API_PROVIDERS} 个 API 通道。")

        validated_providers = [
            self.validate_provider(provider, require_api_key=require_api_key, fallback_name=f"通道 {index}")
            for index, provider in enumerate(providers, start=1)
        ]

        provider_ids = {provider["id"] for provider in validated_providers}
        active_provider_id = str(payload.get("active_provider_id") or "").strip()
        if active_provider_id not in provider_ids:
            active_provider_id = validated_providers[0]["id"]

        return {
            "active_provider_id": active_provider_id,
            "providers": validated_providers,
        }

    def validate_provider(
        self,
        provider: dict[str, Any],
        *,
        require_api_key: bool,
        fallback_name: str,
    ) -> dict[str, Any]:
        normalized = self._normalize_provider(provider, fallback_name=fallback_name)

        base_url = normalized["base_url"]
        api_key = normalized["api_key"]
        model = normalized["model"]

        if not base_url:
            raise APIConfigValidationError(f"{normalized['name']} 的 Base URL 不能为空。")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise APIConfigValidationError(f"{normalized['name']} 的 Base URL 格式不正确。")
        if require_api_key and not api_key:
            raise APIConfigValidationError(f"{normalized['name']} 的 API Key 不能为空。")
        if not model:
            raise APIConfigValidationError(f"{normalized['name']} 的模型名称不能为空。")

        try:
            timeout_seconds = max(1.0, float(normalized["timeout_seconds"]))
        except (TypeError, ValueError) as exc:
            raise APIConfigValidationError(f"{normalized['name']} 的超时时间必须是数字。") from exc

        try:
            max_tokens = max(1, int(normalized["max_tokens"]))
        except (TypeError, ValueError) as exc:
            raise APIConfigValidationError(f"{normalized['name']} 的最大 Token 必须是整数。") from exc

        normalized["timeout_seconds"] = timeout_seconds
        normalized["max_tokens"] = max_tokens
        return normalized

    def build_provider(self, *, name: str | None = None) -> dict[str, Any]:
        provider = dict(DEFAULT_PROVIDER_TEMPLATE)
        provider["id"] = self._new_provider_id()
        provider["name"] = (name or provider["name"]).strip()
        return provider

    def get_active_provider(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = self.load_config() if config is None else self.normalize_config(config)
        active_provider_id = payload["active_provider_id"]
        for provider in payload["providers"]:
            if provider["id"] == active_provider_id:
                return provider
        return payload["providers"][0]

    def get_fallback_providers(self, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        payload = self.load_config() if config is None else self.normalize_config(config)
        active_provider = self.get_active_provider(payload)

        ordered: list[dict[str, Any]] = []
        if active_provider.get("enabled"):
            ordered.append(active_provider)

        for provider in payload["providers"]:
            if provider["id"] == active_provider["id"]:
                continue
            if provider.get("enabled"):
                ordered.append(provider)
        return ordered

    def test_connection(self, provider: dict[str, Any]) -> dict[str, Any]:
        normalized = self.validate_provider(provider, require_api_key=True, fallback_name="当前通道")
        self.prepare_network_env()

        timeout = float(normalized["timeout_seconds"])
        headers = {"Authorization": f"Bearer {normalized['api_key']}"}

        try:
            models_payload = self._request_json(
                f"{normalized['base_url']}/models",
                headers=headers,
                timeout=timeout,
            )
            model_ids = self._extract_model_ids(models_payload)
            configured_model = normalized["model"]
            return {
                "provider_name": normalized["name"],
                "base_url": normalized["base_url"],
                "model": configured_model,
                "test_method": "models",
                "model_count": len(model_ids),
                "model_found": configured_model in model_ids if model_ids else None,
                "message": "连接成功，已拿到模型列表。",
            }
        except APIConfigTestError as list_error:
            completion_payload = self._request_chat_probe(normalized, timeout)
            response_text = self._extract_probe_text(completion_payload)
            return {
                "provider_name": normalized["name"],
                "base_url": normalized["base_url"],
                "model": normalized["model"],
                "test_method": "chat",
                "model_count": None,
                "model_found": True,
                "message": response_text or "连接成功，文本请求已返回响应。",
                "note": f"/models 不可用，已回退到文本请求测试：{list_error}",
            }

    @staticmethod
    def prepare_network_env() -> None:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            os.environ.pop(key, None)
        os.environ["NO_PROXY"] = "*"
        os.environ["no_proxy"] = "*"

    def _request_chat_probe(self, provider: dict[str, Any], timeout: float) -> Any:
        headers = {
            "Authorization": f"Bearer {provider['api_key']}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": provider["model"],
            "messages": [{"role": "user", "content": "Reply with OK only."}],
            "max_tokens": min(int(provider["max_tokens"]), 8),
        }
        return self._request_json(
            f"{provider['base_url']}/chat/completions",
            headers=headers,
            timeout=timeout,
            data=payload,
        )

    def _request_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
        data: dict[str, Any] | None = None,
    ) -> Any:
        request_headers = dict(DEFAULT_BROWSER_HEADERS)
        request_headers.update(headers)
        request_data = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=request_data,
            headers=request_headers,
            method="POST" if data is not None else "GET",
        )

        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise APIConfigTestError(self._format_http_error(exc.code, detail)) from exc
        except URLError as exc:
            raise APIConfigTestError(f"连接失败：{exc.reason}") from exc
        except Exception as exc:  # noqa: BLE001
            raise APIConfigTestError(str(exc)) from exc

        parsed = self._try_parse_json(raw)
        return parsed if parsed is not None else raw

    def _build_default_config(self) -> dict[str, Any]:
        provider = self.build_provider(name="默认通道")
        return {
            "active_provider_id": provider["id"],
            "providers": [provider],
        }

    def _normalize_provider(self, provider: dict[str, Any], *, fallback_name: str) -> dict[str, Any]:
        payload = dict(DEFAULT_PROVIDER_TEMPLATE)
        payload.update(provider or {})
        provider_id = str(payload.get("id") or "").strip() or self._new_provider_id()
        payload["id"] = provider_id
        payload["name"] = str(payload.get("name") or fallback_name).strip() or fallback_name
        payload["base_url"] = str(payload.get("base_url", "")).strip().rstrip("/")
        payload["api_key"] = str(payload.get("api_key", "")).strip()
        payload["model"] = str(payload.get("model", "")).strip()
        payload["prompt"] = str(payload.get("prompt", "")).strip() or DEFAULT_PROVIDER_TEMPLATE["prompt"]
        payload["enabled"] = bool(payload.get("enabled", True))
        return payload

    @staticmethod
    def _extract_model_ids(payload: Any) -> list[str]:
        if isinstance(payload, dict):
            data = payload.get("data") or []
        else:
            data = []

        model_ids: list[str] = []
        for item in data:
            if isinstance(item, dict) and item.get("id"):
                model_ids.append(str(item["id"]))
        return model_ids

    @staticmethod
    def _extract_probe_text(payload: Any) -> str:
        if isinstance(payload, dict):
            choices = payload.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                content = message.get("content", "")
                if isinstance(content, str):
                    return content.strip()
        if isinstance(payload, str):
            return payload.strip()
        return ""

    @staticmethod
    def _try_parse_json(raw: str) -> Any | None:
        text = raw.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _format_http_error(status_code: int, detail: str) -> str:
        normalized_detail = detail.lower()
        if "error code: 1010" in normalized_detail or "request was blocked" in normalized_detail:
            return (
                f"服务端风控拦截（HTTP {status_code} / 1010）："
                "当前网关拒绝了这个客户端请求，通常需要浏览器风格请求头或放行客户端。"
            )
        if status_code in {401, 403}:
            return f"鉴权失败（HTTP {status_code}）：{detail or '请检查 API Key 是否有效。'}"
        if status_code == 404:
            return f"接口不存在（HTTP 404）：{detail or '请检查 Base URL 是否正确。'}"
        if status_code == 429:
            return f"请求过于频繁（HTTP 429）：{detail or '请稍后再试。'}"
        if status_code >= 500:
            return f"服务端错误（HTTP {status_code}）：{detail or '上游服务异常。'}"
        return f"请求失败（HTTP {status_code}）：{detail or '未知错误。'}"

    @staticmethod
    def _new_provider_id() -> str:
        return f"provider-{uuid.uuid4().hex[:8]}"
