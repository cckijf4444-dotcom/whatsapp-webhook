import os
import re
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import fcntl
import hashlib
import json
from html import escape
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
AUDIO_DIR = BASE_DIR / "static" / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


def _load_dotenv_file() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        os.environ.setdefault(key, value)


_load_dotenv_file()

DEFAULT_TIMEOUT = int(os.getenv("HERMES_TIMEOUT", "180"))
HERMES_BIN = os.getenv("HERMES_BIN", "hermes")
HERMES_MODEL = (os.getenv("HERMES_MODEL") or "").strip() or None
HERMES_PROVIDER = (os.getenv("HERMES_PROVIDER") or "").strip() or None
HERMES_PROFILE = (os.getenv("HERMES_PROFILE") or "").strip() or None
HERMES_TOOLSETS = (os.getenv("HERMES_TOOLSETS") or "").strip() or None
HERMES_EXTRA_ARGS = shlex.split(os.getenv("HERMES_EXTRA_ARGS", ""))
PUBLIC_BASE_URL = (
    os.getenv("PUBLIC_BASE_URL") or os.getenv("EXTERNAL_BASE_URL") or ""
).strip() or None
HERMES_WORKDIR = Path(
    os.getenv("HERMES_WORKDIR", str(BASE_DIR / ".hermes-bridge-cwd"))
).expanduser()
REQUEST_DEDUPE_TTL_SECONDS = int(os.getenv("REQUEST_DEDUPE_TTL_SECONDS", "180"))
REQUEST_DEDUPE_STORE = Path(
    os.getenv("REQUEST_DEDUPE_STORE", str(BASE_DIR / "request_dedupe_store.json"))
)

AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION")
TTS_VOICE = "id-ID-GadisNeural"
TTS_OUTPUT_FORMAT = "ogg-24khz-16bit-mono-opus"
TTS_TIMEOUT = int(os.getenv("TTS_TIMEOUT", "30"))
TTS_TEXT_MAX_CHARS = int(os.getenv("TTS_TEXT_MAX_CHARS", "300"))
AUDIO_TTL_SECONDS = int(os.getenv("AUDIO_TTL_SECONDS", "21600"))


def _extract_input_text(payload: dict) -> str:
    return (payload.get("text") or payload.get("message") or "").strip()



def _normalize_text_for_signature(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()



def _normalize_dedupe_part(value) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        return _normalize_text_for_signature(value)

    if isinstance(value, (int, float, bool)):
        return str(value).strip()

    try:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        serialized = str(value)

    return _normalize_text_for_signature(serialized)



def _collect_non_empty_values(payload: dict, keys: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for key in keys:
        normalized = _normalize_dedupe_part(payload.get(key))
        if normalized:
            values.append(normalized)
    return values



def _build_request_dedupe_candidates(payload: dict, input_text: str) -> list[dict[str, str]]:
    sender = ""
    sender_candidates = _collect_non_empty_values(
        payload,
        ("sender_id", "sender", "from", "From", "wa_id", "author"),
    )
    if sender_candidates:
        sender = sender_candidates[0]

    candidates: list[dict[str, str]] = []

    for request_id in _collect_non_empty_values(
        payload,
        ("request_id", "message_id", "MessageSid", "wamid", "message_sid"),
    ):
        candidates.append(
            {
                "kind": "request",
                "sender": sender,
                "value": request_id,
                "raw": f"request|{sender}|{request_id}",
            }
        )

    media_values = _collect_non_empty_values(
        payload,
        (
            "image_id",
            "image_url",
            "image",
            "media_id",
            "media_url",
            "media",
            "media_sha256",
            "sha256",
            "file_sha256",
            "file_hash",
            "attachment_id",
            "attachment_url",
            "attachments",
            "images",
            "files",
        ),
    )
    if media_values:
        media_fingerprint = "|".join(sorted(set(media_values)))
        candidates.append(
            {
                "kind": "media",
                "sender": sender,
                "value": media_fingerprint,
                "raw": f"media|{sender}|{media_fingerprint}",
            }
        )
    else:
        normalized_text = _normalize_text_for_signature(input_text)
        if normalized_text:
            candidates.append(
                {
                    "kind": "text",
                    "sender": sender,
                    "value": normalized_text,
                    "raw": f"text|{sender}|{normalized_text}",
                }
            )

    deduped_candidates: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    for candidate in candidates:
        digest = hashlib.sha256(candidate["raw"].encode("utf-8")).hexdigest()
        key = f"bridge_dedupe|{digest}"
        if key in seen_keys:
            continue
        deduped_candidates.append(
            {
                "key": key,
                "kind": candidate["kind"],
                "sender": candidate["sender"],
                "value": candidate["value"][:200],
            }
        )
        seen_keys.add(key)

    return deduped_candidates



def _is_recent_duplicate_request(
    candidates: list[dict[str, str]], ttl_seconds: int
) -> tuple[bool, Optional[dict[str, str]]]:
    if ttl_seconds <= 0 or not candidates:
        return False, None

    REQUEST_DEDUPE_STORE.parent.mkdir(parents=True, exist_ok=True)
    REQUEST_DEDUPE_STORE.touch(exist_ok=True)

    now = int(time.time())
    cutoff = now - ttl_seconds

    with REQUEST_DEDUPE_STORE.open("r+", encoding="utf-8") as store_file:
        fcntl.flock(store_file.fileno(), fcntl.LOCK_EX)
        try:
            store_file.seek(0)
            raw = store_file.read().strip()
            decoded = json.loads(raw) if raw else {}
            entries = decoded if isinstance(decoded, dict) else {}

            fresh_entries = {
                key: entry
                for key, entry in entries.items()
                if int((entry or {}).get("time", 0)) >= cutoff
            }

            duplicate_entry: Optional[dict[str, str]] = None
            for candidate in candidates:
                matched_entry = fresh_entries.get(candidate["key"])
                if matched_entry:
                    duplicate_entry = {
                        "key": candidate["key"],
                        "kind": str(matched_entry.get("kind") or candidate["kind"]),
                        "sender": str(matched_entry.get("sender") or candidate["sender"]),
                        "value": str(matched_entry.get("value") or candidate["value"]),
                    }
                    break

            if duplicate_entry is None:
                for candidate in candidates:
                    fresh_entries[candidate["key"]] = {
                        "time": now,
                        "kind": candidate["kind"],
                        "sender": candidate["sender"],
                        "value": candidate["value"],
                    }

            store_file.seek(0)
            store_file.truncate()
            json.dump(fresh_entries, store_file, ensure_ascii=False, indent=2)
            store_file.flush()
            os.fsync(store_file.fileno())
        finally:
            fcntl.flock(store_file.fileno(), fcntl.LOCK_UN)

    return duplicate_entry is not None, duplicate_entry



def _extract_plant_field(ai_reply: str, label: str) -> str:
    pattern = rf"^\s*-?\s*{re.escape(label)}\s*[：:]\s*(.+?)\s*$"
    match = re.search(pattern, ai_reply, flags=re.MULTILINE)
    if not match:
        return ""
    return match.group(1).strip()



def _extract_tts_text_from_ai_reply(ai_reply: str) -> str:
    preferred_labels = (
        "TTS 文字",
        "TTS拼音",
        "TTS 拼音",
        "TTS 空耳拼音",
        "阿美族語發音",
        "阿美語發音",
        "阿美族語唸法",
        "阿美語唸法",
    )
    for label in preferred_labels:
        value = _extract_plant_field(ai_reply, label)
        if value:
            return value

    fallback_name_labels = (
        "阿美族語名",
        "阿美語名",
        "阿美族語",
        "阿美語",
    )
    for label in fallback_name_labels:
        value = _extract_plant_field(ai_reply, label)
        if value:
            return value

    inline_match = re.search(
        r"(?:TTS\s*(?:文字|拼音|空耳拼音)|阿美(?:族)?語(?:發音|唸法|叫做|是))[:：]?\s*([A-Za-z'\- ,]+)",
        ai_reply,
    )
    if inline_match:
        return inline_match.group(1).strip()

    return ""



def _extract_tts_text(payload: dict, ai_reply: str) -> str:
    candidate = (
        payload.get("tts_text")
        or payload.get("audio_text")
        or payload.get("amis_tts_text")
        or payload.get("tts_phonetic")
        or payload.get("amis_pronunciation")
        or _extract_tts_text_from_ai_reply(ai_reply)
        or ""
    )
    return str(candidate).strip()[:TTS_TEXT_MAX_CHARS]



def _tts_requested(payload: dict) -> bool:
    value = payload.get("need_tts", True)
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    return str(value).strip().lower() not in {"0", "false", "no", "off"}



def _azure_tts_ready() -> bool:
    return bool(AZURE_SPEECH_KEY and AZURE_SPEECH_REGION)



def _clean_cli_value(value) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None



def _payload_cli_value(payload: dict, *keys: str) -> Optional[str]:
    for key in keys:
        cleaned = _clean_cli_value(payload.get(key))
        if cleaned:
            return cleaned
    return None



def _resolve_hermes_timeout(payload: dict) -> int:
    raw_value = _payload_cli_value(payload, "hermes_timeout", "timeout")
    if not raw_value:
        return DEFAULT_TIMEOUT

    try:
        timeout_seconds = int(raw_value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT

    return max(5, min(timeout_seconds, 600))



def _hermes_workdir() -> Path:
    try:
        HERMES_WORKDIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return BASE_DIR
    return HERMES_WORKDIR



def _looks_like_plant_identification_prompt(prompt: str) -> bool:
    text = str(prompt or "").strip()
    return text.startswith("照片辨識結果為：") or text.startswith("照片辨識結果為:")



def _prepare_hermes_prompt(prompt: str, payload: Optional[dict] = None) -> str:
    text = str(prompt or "").strip()
    if not _looks_like_plant_identification_prompt(text):
        return text

    instruction = (
        "\n\n"
        "請用固定植物辨識格式回覆，且一定要包含以下欄位各一行：\n"
        "辨識結果：\n"
        "中文名：\n"
        "英文名：\n"
        "阿美族語名：\n"
        "TTS 文字：\n"
        "補充：\n"
        "\n"
        "規則：\n"
        "1. TTS 文字只能填阿美族語發音的拉丁拼音，用空格或逗號分隔。\n"
        "2. TTS 文字不能填完整句子、說明、網址、路徑或檔名。\n"
        "3. 若查得到阿美族語名但沒有更適合的發音拼寫，可先填阿美族語名本身。\n"
        "4. 全部使用繁體中文。\n"
    )
    return text + instruction



def _build_hermes_command(prompt: str, payload: Optional[dict] = None) -> list[str]:
    payload = payload or {}
    prepared_prompt = _prepare_hermes_prompt(prompt, payload)
    command = [HERMES_BIN, "-z", prepared_prompt]

    model = _payload_cli_value(payload, "hermes_model", "model") or HERMES_MODEL
    provider = _payload_cli_value(payload, "hermes_provider", "provider") or HERMES_PROVIDER
    profile = _payload_cli_value(payload, "hermes_profile", "profile") or HERMES_PROFILE
    toolsets = _payload_cli_value(payload, "hermes_toolsets", "toolsets") or HERMES_TOOLSETS

    if model:
        command.extend(["-m", model])

    if provider:
        command.extend(["--provider", provider])

    if profile:
        command.extend(["-p", profile])

    if toolsets:
        command.extend(["-t", toolsets])

    if HERMES_EXTRA_ARGS:
        command.extend(HERMES_EXTRA_ARGS)

    return command



def _run_hermes(prompt: str, payload: Optional[dict] = None) -> str:
    if not shutil.which(HERMES_BIN):
        raise FileNotFoundError(f"找不到 Hermes 指令：{HERMES_BIN}")

    timeout_seconds = _resolve_hermes_timeout(payload or {})
    result = subprocess.run(
        _build_hermes_command(prompt, payload),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=True,
        cwd=str(_hermes_workdir()),
    )

    return (result.stdout or "").strip()



def _cleanup_old_audio_files() -> None:
    if AUDIO_TTL_SECONDS <= 0:
        return

    now = time.time()
    for file_path in AUDIO_DIR.glob("*.ogg"):
        try:
            if now - file_path.stat().st_mtime > AUDIO_TTL_SECONDS:
                file_path.unlink(missing_ok=True)
        except OSError as e:
            print(f"⚠️ 清理舊音檔失敗: {file_path} / {e}")



def _azure_tts_endpoint() -> str:
    return f"https://{AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"



def _build_ssml(text: str) -> str:
    return (
        "<speak version='1.0' xml:lang='id-ID'>"
        f"<voice name='{escape(TTS_VOICE)}'>"
        f"{escape(text)}"
        "</voice>"
        "</speak>"
    )



def _synthesize_tts_file(text: str) -> Optional[str]:
    if not _azure_tts_ready() or not text:
        return None

    _cleanup_old_audio_files()

    file_name = f"{uuid.uuid4().hex}.ogg"
    output_path = AUDIO_DIR / file_name

    body = _build_ssml(text).encode("utf-8")
    request_obj = urllib.request.Request(
        _azure_tts_endpoint(),
        data=body,
        headers={
            "Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": TTS_OUTPUT_FORMAT,
            "User-Agent": "hermes-bridge",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request_obj, timeout=TTS_TIMEOUT) as response:
            audio_bytes = response.read()

        if not audio_bytes:
            raise RuntimeError("Azure TTS 沒有回傳音訊資料")

        output_path.write_bytes(audio_bytes)
        return file_name

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="ignore")
        print(f"❌ Azure TTS HTTP 錯誤: {e.code} / {error_body}")
        raise RuntimeError("Azure TTS 呼叫失敗") from e
    except urllib.error.URLError as e:
        print(f"❌ Azure TTS 網路錯誤: {e}")
        raise RuntimeError("Azure TTS 網路連線失敗") from e



def _build_audio_url(file_name: Optional[str]) -> Optional[str]:
    if not file_name:
        return None

    base_url = (PUBLIC_BASE_URL or "").rstrip("/")
    if not base_url:
        request_base = request.host_url.rstrip("/")
        lowered = request_base.lower()
        if "localhost" in lowered or "127.0.0.1" in lowered:
            print(
                "⚠️ 偵測到本機網址，audio_url 不回傳 localhost/127.0.0.1；"
                "請設定 PUBLIC_BASE_URL 為外部可存取網址。"
            )
            return None
        base_url = request_base

    return f"{base_url}/audio/{file_name}"



def _rewrite_reply_audio_line(ai_reply: str, audio_url: Optional[str]) -> str:
    if not ai_reply:
        return ai_reply

    replacement_line = (
        f"- 音檔：Azure TTS 印尼語模型已生成 .ogg 語音，可直接播放：{audio_url}"
        if audio_url
        else "- 音檔：不再使用 mp3；改由 Azure TTS 產生 .ogg 語音"
    )

    updated_reply = re.sub(
        r"^\s*-\s*音檔[：:].*$",
        replacement_line,
        ai_reply,
        count=1,
        flags=re.MULTILINE,
    )

    updated_reply = re.sub(
        r"[A-Za-z0-9_./\\:-]+\.mp3",
        "Azure TTS .ogg 語音",
        updated_reply,
        flags=re.IGNORECASE,
    )
    updated_reply = re.sub(r"\bmp3\b", ".ogg", updated_reply, flags=re.IGNORECASE)

    if updated_reply == ai_reply and audio_url:
        updated_reply = (updated_reply.strip() + "\n\n" + replacement_line).strip()

    return updated_reply.strip()


@app.get("/")
@app.get("/health")
def health_check():
    hermes_available = shutil.which(HERMES_BIN) is not None
    return jsonify(
        {
            "ok": True,
            "service": "hermes-bridge",
            "hermes_bin": HERMES_BIN,
            "hermes_available": hermes_available,
            "hermes_model": HERMES_MODEL,
            "hermes_provider": HERMES_PROVIDER,
            "hermes_profile": HERMES_PROFILE,
            "hermes_toolsets": HERMES_TOOLSETS,
            "hermes_extra_args": HERMES_EXTRA_ARGS,
            "hermes_timeout": DEFAULT_TIMEOUT,
            "recommended_client_timeout": max(DEFAULT_TIMEOUT + TTS_TIMEOUT + 30, 120),
            "hermes_workdir": str(_hermes_workdir()),
            "request_dedupe_ttl_seconds": REQUEST_DEDUPE_TTL_SECONDS,
            "azure_tts_configured": _azure_tts_ready(),
            "tts_voice": TTS_VOICE,
            "tts_output_format": TTS_OUTPUT_FORMAT,
        }
    ), 200


@app.get("/audio/<path:filename>")
def serve_audio(filename: str):
    return send_from_directory(AUDIO_DIR, filename, mimetype="audio/ogg")


def _build_send_response(reply_text: str, audio_url: Optional[str]):
    return jsonify(
        {
            "reply_text": reply_text,
            "audio_url": audio_url,
        }
    ), 200


@app.post("/")
@app.post("/send")
def receive_and_send():
    data = request.get_json(silent=True) or {}
    print(f"DEBUG - 收到請求資料: {data}")

    input_text = _extract_input_text(data)
    if not input_text:
        return jsonify({"error": "缺少 text 或 message 欄位"}), 400

    dedupe_candidates = _build_request_dedupe_candidates(data, input_text)
    is_duplicate, duplicate_entry = _is_recent_duplicate_request(
        dedupe_candidates, REQUEST_DEDUPE_TTL_SECONDS
    )
    if is_duplicate:
        duplicate_kind = (duplicate_entry or {}).get("kind") or "unknown"
        duplicate_sender = (duplicate_entry or {}).get("sender") or "unknown"
        duplicate_value = (duplicate_entry or {}).get("value") or ""
        print(
            "♻️ 偵測到短時間重複圖片／請求，略過重複回覆 "
            f"kind={duplicate_kind} sender={duplicate_sender} value={duplicate_value}"
        )
        return _build_send_response("", None)

    print(f"🧠 [Hermes AI] 正在呼叫 Hermes: {input_text}")

    hermes_command = _build_hermes_command(input_text, data)
    hermes_timeout = _resolve_hermes_timeout(data)
    print(
        "🛠️ Hermes 執行參數: "
        f"cmd={shlex.join(hermes_command)} cwd={_hermes_workdir()} timeout={hermes_timeout}s"
    )

    try:
        ai_reply = _run_hermes(input_text, data)
        if not ai_reply:
            ai_reply = "抱歉，Hermes 沒有回傳內容。"

        audio_file_name = None
        tts_text = None
        if _tts_requested(data):
            tts_text = _extract_tts_text(data, ai_reply)
            audio_file_name = _synthesize_tts_file(tts_text)

        audio_url = _build_audio_url(audio_file_name)
        ai_reply = _rewrite_reply_audio_line(ai_reply, audio_url)

        print(f"✅ Hermes AI 思考完成:\n{ai_reply}")
        if audio_url:
            print(f"🔊 Azure TTS 音檔已生成: {audio_url} / tts_text={tts_text}")

        return _build_send_response(ai_reply, audio_url)

    except FileNotFoundError as e:
        print(f"❌ Hermes 指令不存在: {e}")
        return jsonify(
            {
                "reply_text": "抱歉，伺服器尚未安裝 Hermes CLI，請先確認部署環境。",
                "audio_url": None,
            }
        ), 503

    except subprocess.TimeoutExpired:
        print("❌ Hermes AI 思考超時")
        return jsonify(
            {
                "reply_text": "抱歉，Hermes AI 思考時間過長，請稍後再試。",
                "audio_url": None,
            }
        ), 504

    except subprocess.CalledProcessError as e:
        stderr_text: Optional[str] = (e.stderr or "").strip() or None
        print(f"❌ Hermes 指令執行失敗: {stderr_text}")
        return jsonify(
            {
                "reply_text": "抱歉，Hermes 系統執行失敗，請稍後再試。",
                "audio_url": None,
            }
        ), 500

    except Exception as e:
        print(f"❌ 發生未預期錯誤: {e}")
        return jsonify(
            {
                "reply_text": "抱歉，系統發生未預期錯誤。",
                "audio_url": None,
            }
        ), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "3001"))
    app.run(host="0.0.0.0", port=port)
