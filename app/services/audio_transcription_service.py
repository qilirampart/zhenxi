from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import time
import uuid
from pathlib import Path
from typing import Callable

import requests

from app.config.settings import (
    ASR_AUDIO_CHUNK_SECONDS,
    ASR_DIRECT_UPLOAD_LIMIT_BYTES,
    EXTRACTED_AUDIO_DIR,
)
from app.models.audio_transcription import (
    AudioTranscriptionResult,
    PreparedAudio,
    TranscriptSegment,
    TranscriptWord,
)
from app.services.tencent_asr_config_service import TencentASRConfigService
from app.utils.ffmpeg import extract_audio_track, probe_media_duration_ms

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]


class AudioTranscriptionError(RuntimeError):
    pass


class AudioTranscriptionService:
    host = "asr.tencentcloudapi.com"
    endpoint = "https://asr.tencentcloudapi.com"
    service = "asr"
    version = "2019-06-14"

    def __init__(self) -> None:
        self._config_service = TencentASRConfigService()

    def extract_audio(
        self,
        source_path: str,
        *,
        progress_callback: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> PreparedAudio:
        source = Path(source_path)
        if not source.exists():
            raise AudioTranscriptionError(f"素材不存在：{source}")

        self._check_cancel(should_cancel)
        self._emit_progress(progress_callback, 0, 3, "正在准备音频")

        EXTRACTED_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_stem = source.stem or "audio"
        audio_path = EXTRACTED_AUDIO_DIR / f"{safe_stem}_{stamp}.mp3"

        extract_audio_track(source, audio_path)
        self._check_cancel(should_cancel)
        self._emit_progress(progress_callback, 1, 3, "音频分离完成")

        duration_ms = probe_media_duration_ms(audio_path)
        size_bytes = audio_path.stat().st_size

        chunk_paths: list[str] = []
        chunk_offsets_ms: list[int] = []

        chunk_duration_ms = ASR_AUDIO_CHUNK_SECONDS * 1000
        if size_bytes <= ASR_DIRECT_UPLOAD_LIMIT_BYTES:
            chunk_paths.append(str(audio_path))
            chunk_offsets_ms.append(0)
        else:
            for index, start_ms in enumerate(range(0, max(duration_ms, 1), chunk_duration_ms), start=1):
                self._check_cancel(should_cancel)
                chunk_path = EXTRACTED_AUDIO_DIR / f"{safe_stem}_{stamp}_part{index:02d}.mp3"
                extract_audio_track(
                    audio_path,
                    chunk_path,
                    start_ms=start_ms,
                    duration_ms=min(chunk_duration_ms, duration_ms - start_ms),
                )
                chunk_paths.append(str(chunk_path))
                chunk_offsets_ms.append(start_ms)

        self._emit_progress(progress_callback, 3, 3, "音频已就绪")
        return PreparedAudio(
            source_path=str(source),
            audio_path=str(audio_path),
            duration_ms=duration_ms,
            size_bytes=size_bytes,
            chunk_paths=chunk_paths,
            chunk_offsets_ms=chunk_offsets_ms,
        )

    def transcribe_prepared_audio(
        self,
        prepared: PreparedAudio,
        *,
        progress_callback: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> AudioTranscriptionResult:
        config = self._config_service.validate_config(self._config_service.load_config(), require_secret=True)
        if not config.get("enabled", True):
            raise AudioTranscriptionError("语音转写未启用，请先在音频 API 配置中开启。")

        segments: list[TranscriptSegment] = []
        raw_tasks: list[dict] = []
        total_chunks = max(len(prepared.chunk_paths), 1)

        for index, chunk_path in enumerate(prepared.chunk_paths, start=1):
            self._check_cancel(should_cancel)
            self._emit_progress(progress_callback, index - 1, total_chunks, f"正在提交第 {index}/{total_chunks} 段音频")
            task_id = self._create_task(Path(chunk_path), config)
            self._emit_progress(progress_callback, index - 1, total_chunks, f"第 {index}/{total_chunks} 段已提交，正在等待结果")
            task_data = self._poll_task(task_id, config, should_cancel=should_cancel)
            raw_tasks.append(task_data)
            offset_ms = prepared.chunk_offsets_ms[index - 1]
            segments.extend(self._parse_segments(task_data, offset_ms=offset_ms))
            self._emit_progress(progress_callback, index, total_chunks, f"第 {index}/{total_chunks} 段识别完成")

        segments.sort(key=lambda item: (item.start_ms, item.end_ms))
        merged_text = "".join(segment.text for segment in segments).strip()
        srt_text = self._build_srt(segments)
        return AudioTranscriptionResult(
            source_path=prepared.source_path,
            audio_path=prepared.audio_path,
            text=merged_text,
            srt_text=srt_text,
            segments=segments,
            raw_tasks=raw_tasks,
        )

    def transcribe_source(
        self,
        source_path: str,
        *,
        progress_callback: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> tuple[PreparedAudio, AudioTranscriptionResult]:
        prepared = self.extract_audio(
            source_path,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
        )
        result = self.transcribe_prepared_audio(
            prepared,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
        )
        return prepared, result

    def _create_task(self, audio_path: Path, config: dict[str, object]) -> int:
        audio_bytes = audio_path.read_bytes()
        payload = {
            "EngineModelType": config["engine_model_type"],
            "ChannelNum": int(config["channel_num"]),
            "ResTextFormat": int(config["res_text_format"]),
            "SourceType": 1,
            "Data": base64.b64encode(audio_bytes).decode("ascii"),
            "DataLen": len(audio_bytes),
        }
        response = self._signed_request("CreateRecTask", payload, config)
        parsed = self._parse_response(response)
        data = parsed.get("Response", {}).get("Data") or {}
        task_id = data.get("TaskId")
        if task_id is None:
            raise AudioTranscriptionError("未获取到腾讯云任务 ID。")
        return int(task_id)

    def _poll_task(
        self,
        task_id: int,
        config: dict[str, object],
        *,
        should_cancel: CancelCallback | None = None,
        interval_seconds: float = 3.0,
        max_attempts: int = 120,
    ) -> dict:
        for _attempt in range(max_attempts):
            self._check_cancel(should_cancel)
            response = self._signed_request("DescribeTaskStatus", {"TaskId": task_id}, config)
            parsed = self._parse_response(response)
            data = parsed.get("Response", {}).get("Data") or {}
            status = int(data.get("Status", -1))
            if status == 2:
                return data
            if status == 3:
                raise AudioTranscriptionError(data.get("ErrorMsg") or "腾讯云语音识别任务失败。")
            time.sleep(interval_seconds)
        raise AudioTranscriptionError("语音识别轮询超时。")

    def _signed_request(self, action: str, body: dict[str, object], config: dict[str, object]) -> requests.Response:
        payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        timestamp = int(time.time())
        date = dt.datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")
        credential_scope = f"{date}/{self.service}/tc3_request"
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
        string_to_sign = "\n".join(
            [
                "TC3-HMAC-SHA256",
                str(timestamp),
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        secret_date = self._sign(("TC3" + str(config["secret_key"])).encode("utf-8"), date)
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
            "X-TC-Region": str(config["region"]),
        }
        session = requests.Session()
        session.trust_env = False
        try:
            return session.post(self.endpoint, headers=headers, data=payload.encode("utf-8"), timeout=60)
        except Exception as exc:  # noqa: BLE001
            raise AudioTranscriptionError(f"请求腾讯云失败：{exc}") from exc

    @staticmethod
    def _parse_response(response: requests.Response) -> dict:
        try:
            parsed = response.json()
        except Exception as exc:  # noqa: BLE001
            raise AudioTranscriptionError(f"腾讯云返回了非 JSON 内容：{response.text[:500]}") from exc
        error = parsed.get("Response", {}).get("Error")
        if error:
            code = str(error.get("Code") or "").strip()
            message = str(error.get("Message") or "").strip()
            raise AudioTranscriptionError(f"{code}: {message}" if code else message)
        return parsed

    @staticmethod
    def _parse_segments(task_data: dict, *, offset_ms: int) -> list[TranscriptSegment]:
        items = task_data.get("ResultDetail") or []
        segments: list[TranscriptSegment] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            words: list[TranscriptWord] = []
            for raw_word in item.get("Words") or []:
                if not isinstance(raw_word, dict):
                    continue
                words.append(
                    TranscriptWord(
                        text=str(raw_word.get("Word") or "").strip(),
                        start_ms=offset_ms + int(raw_word.get("OffsetStartMs") or 0),
                        end_ms=offset_ms + int(raw_word.get("OffsetEndMs") or 0),
                    )
                )
            text = str(item.get("FinalSentence") or item.get("SliceSentence") or "").strip()
            if not text:
                continue
            segments.append(
                TranscriptSegment(
                    text=text,
                    start_ms=offset_ms + int(item.get("StartMs") or 0),
                    end_ms=offset_ms + int(item.get("EndMs") or 0),
                    speaker_id=int(item.get("SpeakerId") or 0),
                    words=words,
                )
            )
        return segments

    @staticmethod
    def _build_srt(segments: list[TranscriptSegment]) -> str:
        lines: list[str] = []
        for index, segment in enumerate(segments, start=1):
            lines.append(str(index))
            lines.append(
                f"{AudioTranscriptionService._format_srt_ms(segment.start_ms)} --> "
                f"{AudioTranscriptionService._format_srt_ms(segment.end_ms)}"
            )
            lines.append(segment.text)
            lines.append("")
        return "\n".join(lines).strip()

    @staticmethod
    def _format_srt_ms(value: int) -> str:
        safe = max(0, int(value))
        total_seconds, milliseconds = divmod(safe, 1000)
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

    @staticmethod
    def _emit_progress(callback: ProgressCallback | None, current: int, total: int, message: str) -> None:
        if callback is not None:
            callback(current, total, message)

    @staticmethod
    def _check_cancel(should_cancel: CancelCallback | None) -> None:
        if should_cancel is not None and should_cancel():
            raise AudioTranscriptionError("用户已取消语音转写。")

    @staticmethod
    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()
