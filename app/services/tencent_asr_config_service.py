from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any

import requests

from app.config.settings import TENCENT_ASR_CONFIG_PATH

DEFAULT_TENCENT_ASR_CONFIG: dict[str, Any] = {
    "secret_id": "",
    "secret_key": "",
    "region": "ap-shanghai",
    "engine_model_type": "16k_zh",
    "res_text_format": 3,
    "channel_num": 1,
    "enabled": True,
}


class TencentASRConfigValidationError(ValueError):
    pass


class TencentASRConfigTestError(RuntimeError):
    pass


class TencentASRConfigService:
    host = "asr.tencentcloudapi.com"
    endpoint = "https://asr.tencentcloudapi.com"
    service = "asr"
    version = "2019-06-14"

    def load_config(self) -> dict[str, Any]:
        if not TENCENT_ASR_CONFIG_PATH.exists():
            config = dict(DEFAULT_TENCENT_ASR_CONFIG)
            self.save_config(config)
            return config
        raw = json.loads(TENCENT_ASR_CONFIG_PATH.read_text(encoding="utf-8"))
        return self.normalize_config(raw)

    def save_config(self, config: dict[str, Any]) -> dict[str, Any]:
        normalized = self.validate_config(config, require_secret=False)
        TENCENT_ASR_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        TENCENT_ASR_CONFIG_PATH.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return normalized

    def normalize_config(self, raw: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(DEFAULT_TENCENT_ASR_CONFIG)
        payload.update(raw or {})
        payload["secret_id"] = str(payload.get("secret_id") or "").strip()
        payload["secret_key"] = str(payload.get("secret_key") or "").strip()
        payload["region"] = str(payload.get("region") or DEFAULT_TENCENT_ASR_CONFIG["region"]).strip()
        payload["engine_model_type"] = str(
            payload.get("engine_model_type") or DEFAULT_TENCENT_ASR_CONFIG["engine_model_type"]
        ).strip()
        payload["enabled"] = bool(payload.get("enabled", True))
        payload["channel_num"] = 1 if int(payload.get("channel_num", 1) or 1) != 2 else 2
        payload["res_text_format"] = int(payload.get("res_text_format", 3) or 3)
        return payload

    def validate_config(self, config: dict[str, Any], *, require_secret: bool) -> dict[str, Any]:
        payload = self.normalize_config(config)
        if require_secret and not payload["secret_id"]:
            raise TencentASRConfigValidationError("SecretId 不能为空。")
        if require_secret and not payload["secret_key"]:
            raise TencentASRConfigValidationError("SecretKey 不能为空。")
        if not payload["region"]:
            raise TencentASRConfigValidationError("Region 不能为空。")
        if not payload["engine_model_type"]:
            raise TencentASRConfigValidationError("引擎模型不能为空。")
        if payload["res_text_format"] not in {0, 1, 2, 3, 4, 5}:
            raise TencentASRConfigValidationError("返回格式参数不合法。")
        if payload["channel_num"] not in {1, 2}:
            raise TencentASRConfigValidationError("声道数仅支持 1 或 2。")
        return payload

    def test_connection(self, config: dict[str, Any]) -> dict[str, Any]:
        payload = self.validate_config(config, require_secret=True)
        response = self._signed_request("DescribeTaskStatus", {"TaskId": 0}, payload)
        parsed = response.json()
        error = parsed.get("Response", {}).get("Error")
        if error:
            code = str(error.get("Code") or "").strip()
            message = str(error.get("Message") or "").strip()
            if code in {"FailedOperation.NoSuchTask", "InvalidParameter", "InvalidParameterValue"}:
                return {
                    "ok": True,
                    "message": "密钥校验通过，服务可访问。",
                    "detail": f"{code}: {message}" if code else message,
                }
            if code == "FailedOperation.UserNotRegistered":
                return {
                    "ok": True,
                    "message": "密钥校验通过，但语音识别服务尚未开通或对应能力未开通。",
                    "detail": f"{code}: {message}",
                }
            if code.startswith("AuthFailure") or code.startswith("FailedOperation.CheckAuthInfoFailed"):
                raise TencentASRConfigTestError(f"{code}: {message}")
            return {
                "ok": True,
                "message": "接口已响应，请检查具体业务状态。",
                "detail": f"{code}: {message}" if code else message,
            }
        return {"ok": True, "message": "密钥校验通过。", "detail": json.dumps(parsed, ensure_ascii=False)}

    def _signed_request(self, action: str, body: dict[str, Any], config: dict[str, Any]) -> requests.Response:
        payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        timestamp = int(time.time())
        date = dt.datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")
        hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        canonical_request = "\n".join(
            [
                "POST",
                "/",
                "",
                f"content-type:application/json; charset=utf-8\nhost:{self.host}\n",
                "content-type;host",
                hashed_payload,
            ]
        )
        credential_scope = f"{date}/{self.service}/tc3_request"
        string_to_sign = "\n".join(
            [
                "TC3-HMAC-SHA256",
                str(timestamp),
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        secret_date = self._sign(("TC3" + config["secret_key"]).encode("utf-8"), date)
        secret_service = self._sign(secret_date, self.service)
        secret_signing = self._sign(secret_service, "tc3_request")
        signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        authorization = (
            "TC3-HMAC-SHA256 "
            f"Credential={config['secret_id']}/{credential_scope}, "
            "SignedHeaders=content-type;host, "
            f"Signature={signature}"
        )
        headers = {
            "Authorization": authorization,
            "Content-Type": "application/json; charset=utf-8",
            "Host": self.host,
            "X-TC-Action": action,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": self.version,
            "X-TC-Region": config["region"],
        }
        session = requests.Session()
        session.trust_env = False
        try:
            return session.post(self.endpoint, headers=headers, data=payload.encode("utf-8"), timeout=30)
        except Exception as exc:  # noqa: BLE001
            raise TencentASRConfigTestError(f"连接失败：{exc}") from exc

    @staticmethod
    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()
