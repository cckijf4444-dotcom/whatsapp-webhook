from flask import Flask, request, jsonify, send_from_directory
import os
import re
import requests
import base64
import threading
import hashlib
import builtins
import traceback
import asyncio
import uuid
from pathlib import Path
import edge_tts

# ==========================================
# 🔧 系統優化：強制讓所有 print 立刻推送到 Render 日誌
# ==========================================
_original_print = builtins.print
def print(*args, **kwargs):
    kwargs["flush"] = True
    _original_print(*args, **kwargs)
builtins.print = print

app = Flask(__name__)

# ==========================================
# 📁 語音檔案儲存路徑與靜態目錄設定
# ==========================================
BASE_DIR = Path(__file__).resolve().parent
AUDIO_DIR = BASE_DIR / "static" / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

@app.route('/static/audio/<path:filename>')
def serve_audio(filename):
    return send_from_directory(str(AUDIO_DIR), filename)

# ==========================================
# 🔐 環境變數設定區
# ==========================================
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "my_verify_token_123")
ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN")
HERMES_API_URL = os.environ.get("HERMES_API_URL")
PLANT_ID_API_KEY = os.environ.get("PLANT_ID_API_KEY")

REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "30"))
PLANT_ID_TIMEOUT = int(os.environ.get("PLANT_ID_TIMEOUT", str(REQUEST_TIMEOUT)))
FORWARDED_HERMES_TIMEOUT = int(os.environ.get("HERMES_TIMEOUT", "180"))
HERMES_API_TIMEOUT = int(
    os.environ.get(
        "HERMES_API_TIMEOUT",
        str(max(FORWARDED_HERMES_TIMEOUT + 60, 120)),
    )
)

# ==========================================
# 🧰 小工具：安全印出回應內容
# ==========================================
def safe_response_text(response, limit=2000):
    try:
        text = response.text
    except Exception:
        return "<無法讀取 response.text>"

    if text is None:
        return "<空白>"

    text = text.strip()
    if not text:
        return "<空白>"

    if len(text) > limit:
        return text[:limit] + " ...[truncated]"
    return text

def _extract_reply_field(reply_text, label):
    pattern = rf"^\s*-?\s*{re.escape(label)}\s*[：:]\s*(.+?)\s*$"
    match = re.search(pattern, str(reply_text or ""), flags=re.MULTILINE)
    if not match:
        return ""
    return match.group(1).strip()

def build_glasses_readout_text(reply_text):
    plant_name = (
        _extract_reply_field(reply_text, "辨識結果")
        or _extract_reply_field(reply_text, "中文名")
    )
    amis_name = (
        _extract_reply_field(reply_text, "阿美族語名")
        or _extract_reply_field(reply_text, "阿美語名")
        or _extract_reply_field(reply_text, "阿美族語")
        or _extract_reply_field(reply_text, "阿美語")
    )
    tts_text = (
        _extract_reply_field(reply_text, "TTS 文字")
        or _extract_reply_field(reply_text, "TTS 拼音")
        or _extract_reply_field(reply_text, "TTS拼音")
        or _extract_reply_field(reply_text, "阿美族語發音")
        or _extract_reply_field(reply_text, "阿美語發音")
    )
    spoken_tts = re.sub(r"\s*,\s*", " ", tts_text).strip()

    segments = []
    if plant_name:
        segments.append(f"這是{plant_name}。")
    if amis_name:
        segments.append(f"阿美族語是{amis_name}。")
    if spoken_tts:
        segments.append(f"發音是{spoken_tts}。")

    summary = "".join(segments).strip()
    if not summary:
        return ""

    if len(summary) > 120:
        summary = summary[:117].rstrip() + "..."
    return summary

# ==========================================
# 🧪 臨時測試路由：不需要植物辨識即可直接測試 TTS
# ==========================================
@app.route("/test-tts", methods=["GET"])
def test_tts():
    test_plant = "龍葵"
    test_amis = "tatukem"
    test_efficacy = "嫩葉可作為野菜煮湯，具有清熱解毒的功效。"
    
    audio_url = generate_bilingual_tts_audio(test_plant, test_amis, test_efficacy)
    
    if audio_url:
        return jsonify({
            "success": True,
            "message": "TTS 語音生成成功！請點擊 audio_url 聆聽",
            "audio_url": audio_url
        }), 200
    else:
        return jsonify({
            "success": False,
            "error": "TTS 語音生成失敗，請檢查 Render 日誌"
        }), 500

# ==========================================
# 🎵 雙語優化 TTS 語音生成模組 (Edge-TTS 雙引擎拼接)
# ==========================================
def _asyncio_run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return asyncio.run_coroutine_threadsafe(coro, loop).result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)

def generate_bilingual_tts_audio(plant_name, amis_name, efficacy):
    print(f"🎵 [TTS] 開始生成雙語語音: plant={plant_name}, amis={amis_name}")
    try:
        output_filename = f"audio_{amis_name}_{uuid.uuid4().hex[:6]}.mp3"
        output_path = AUDIO_DIR / output_filename
        
        part1_file = AUDIO_DIR / f"part1_{uuid.uuid4().hex[:4]}.mp3"
        part2_file = AUDIO_DIR / f"part2_{uuid.uuid4().hex[:4]}.mp3"
        part3_file = AUDIO_DIR / f"part3_{uuid.uuid4().hex[:4]}.mp3"

        async def synthesize_parts():
            text_part1 = f"辨識結果為：{plant_name}。海岸阿美族語稱為："
            comm1 = edge_tts.Communicate(text_part1, "zh-TW-HsiaoChenNeural", rate="-5%")
            await comm1.save(str(part1_file))

            comm2 = edge_tts.Communicate(amis_name, "id-ID-GadisNeural", rate="-15%")
            await comm2.save(str(part2_file))

            text_part3 = f"。功效與介紹為：{efficacy}。"
            comm3 = edge_tts.Communicate(text_part3, "zh-TW-HsiaoChenNeural", rate="-5%")
            await comm3.save(str(part3_file))

        _asyncio_run(synthesize_parts())

        with open(output_path, "wb") as f_out:
            for p_file in [part1_file, part2_file, part3_file]:
                if p_file.exists():
                    with open(p_file, "rb") as f_in:
                        f_out.write(f_in.read())
                    p_file.unlink()

        base_url = os.getenv("RENDER_EXTERNAL_URL", "http://127.0.0.1:10000")
        audio_url = f"{base_url}/static/audio/{output_filename}"
        print(f"✅ [TTS] 語音合成成功，公開網址: {audio_url}")
        return audio_url

    except Exception as e:
        print(f"❌ [TTS] 語音合成發生異常: {e}")
        traceback.print_exc()
        return None

# ==========================================
# 📤 發送訊息回 WhatsApp (文字與語音模組)
# ==========================================
def send_whatsapp_reply(phone_number_id, recipient_number, reply_text):
    if not reply_text:
        return
    if not ACCESS_TOKEN:
        print("⚠️ 尚未設定 WHATSAPP_ACCESS_TOKEN，無法發送文字")
        return

    url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient_number,
        "type": "text",
        "text": {"body": reply_text}
    }

    try:
        requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        print(f"❌ [WA text] 發送失敗: {e}")

def send_whatsapp_audio(phone_number_id, recipient_number, audio_link):
    if not ACCESS_TOKEN:
        print("⚠️ 尚未設定 WHATSAPP_ACCESS_TOKEN，無法發送語音")
        return

    url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient_number,
        "type": "audio",
        "audio": {"link": audio_link}
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            print("✅ 成功回傳語音給使用者！")
    except Exception as e:
        print(f"❌ [WA audio] 發送失敗: {e}")

def send_whatsapp_glasses_readout(phone_number_id, recipient_number, reply_text):
    short_text = build_glasses_readout_text(reply_text)
    if not short_text:
        return
    send_whatsapp_reply(phone_number_id, recipient_number, short_text)

# ==========================================
# 🧠 大腦區塊：呼叫朋友的 HERMES (Ngrok / Render)
# ==========================================
def process_with_hermes(input_text, chat_id=None):
    print(f"🧠 [HERMES] 準備將資料送往朋友的服務: {input_text}")

    if not HERMES_API_URL:
        return "⚠️ 尚未設定 HERMES_API_URL (朋友的服務網址)", None

    payload = {
        "message": input_text,
        "text": input_text,
        "hermes_timeout": FORWARDED_HERMES_TIMEOUT,
    }
    if chat_id:
        payload["chatId"] = chat_id

    try:
        response = requests.post(
            HERMES_API_URL,
            json=payload,
            timeout=HERMES_API_TIMEOUT,
        )
        raw_body = safe_response_text(response)

        if response.status_code == 200:
            data = response.json()
            reply_text = data.get("reply_text") or data.get("message")
            if not reply_text:
                return "⚠️ 收到 HERMES 空白回覆", None
            return reply_text, None

        return f"❌ HERMES 連線錯誤 (狀態碼: {response.status_code})", None

    except requests.exceptions.Timeout:
        return "❌ 呼叫 HERMES 逾時", None
    except Exception as e:
        return f"❌ 呼叫 HERMES 發生異常: {e}", None

# ==========================================
# 🌿 視覺區塊：呼叫 Plant.id (暫時替代 YOLOv8)
# ==========================================
def _plantid_success_result(plant_name, probability, source):
    return {
        "ok": True,
        "plant_name": plant_name,
        "probability": probability,
        "source": source,
        "user_message": None,
        "status_code": 200,
    }

def identify_plant_with_plantid(image_bytes):
    print("🌿 [Plant.id] 正在辨識植物特徵...")

    if not PLANT_ID_API_KEY:
        return {"ok": False, "user_message": "⚠️ Render 尚未設定 Plant.id API 金鑰。"}

    if not image_bytes:
        return {"ok": False, "user_message": "⚠️ 系統沒有拿到有效圖片內容，請再傳一次。"}

    base64_image = base64.b64encode(image_bytes).decode("ascii")
    v3_url = "https://plant.id/api/v3/identification"
    v3_headers = {"Api-Key": PLANT_ID_API_KEY, "Content-Type": "application/json"}
    v3_payload = {"images": [base64_image]}

    try:
        response = requests.post(v3_url, headers=v3_headers, json=v3_payload, timeout=PLANT_ID_TIMEOUT)
        if response.status_code in (200, 201):
            data = response.json()
            suggestions = data.get("result", {}).get("classification", {}).get("suggestions", []) or []
            if suggestions:
                best_match = suggestions[0]
                return _plantid_success_result(best_match.get("name"), best_match.get("probability"), "v3")
    except Exception:
        pass # 失敗則嘗試 v2

    v2_url = "https://api.plant.id/v2/identify"
    v2_headers = {"Api-Key": PLANT_ID_API_KEY, "Content-Type": "application/json"}
    v2_payload = {"images": [base64_image], "plant_details": ["common_names"], "language": "zh-tw"}

    try:
        response = requests.post(v2_url, headers=v2_headers, json=v2_payload, timeout=PLANT_ID_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            suggestions = data.get("suggestions") or []
            if suggestions:
                best_match = suggestions[0]
                names = best_match.get("plant_details", {}).get("common_names", []) or []
                plant_name = names[0] if names else best_match.get("plant_name")
                return _plantid_success_result(plant_name, best_match.get("probability"), "v2")
    except Exception:
        pass

    return {"ok": False, "user_message": "抱歉，視覺模型暫時看不出這是什麼植物。"}

# ==========================================
# 📥 實用工具：從 WhatsApp 下載真實圖片
# ==========================================
def download_whatsapp_image(media_id):
    if not media_id or not ACCESS_TOKEN:
        return {"ok": False, "user_message": "⚠️ 無法下載圖片，缺少 media id 或 Access Token。"}

    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    url_request = f"https://graph.facebook.com/v18.0/{media_id}"

    try:
        res = requests.get(url_request, headers=headers, timeout=REQUEST_TIMEOUT)
        if res.status_code != 200:
            return {"ok": False, "user_message": "⚠️ 圖片資訊查詢失敗，請再傳一次。"}

        media_url = res.json().get("url")
        img_res = requests.get(media_url, headers=headers, timeout=REQUEST_TIMEOUT)
        if img_res.status_code == 200 and img_res.content:
            return {"ok": True, "bytes": img_res.content}
    except Exception as e:
        print(f"❌ [Meta] 圖片下載失敗: {e}")

    return {"ok": False, "user_message": "⚠️ 從 WhatsApp 系統下載照片失敗，請再傳一次。"}

# ==========================================
# ⚙️ 專門負責耗時工作的背景處理器 (包含最新防呆邏輯)
# ==========================================
def background_processor(value):
    phone_number_id = value.get("metadata", {}).get("phone_number_id")

    if "messages" in value:
        for message in value["messages"]:
            from_number = message.get("from")
            msg_type = message.get("type")

            try:
                # --------------------------
                # 💬 處理純文字 (防呆機制)
                # --------------------------
                if msg_type == "text":
                    text = message.get("text", {}).get("body", "")
                    
                    if text.strip() == "測試語音":
                        test_plant = "龍葵"
                        test_amis = "tatukem"
                        test_efficacy = "嫩葉可作為野菜煮湯，具有清熱解毒的功效。"
                        if phone_number_id and from_number:
                            send_whatsapp_reply(phone_number_id, from_number, f"收到測試指令！正在為您生成「{test_plant}」的語音...")
                            custom_audio_url = generate_bilingual_tts_audio(test_plant, test_amis, test_efficacy)
                            if custom_audio_url:
                                send_whatsapp_audio(phone_number_id, from_number, custom_audio_url)
                        continue 
                    
                    # 【防呆 1】：如果使用者傳送非「測試語音」的其他純文字，直接提醒傳送圖片
                    if phone_number_id and from_number:
                        send_whatsapp_reply(phone_number_id, from_number, "請傳送「植物的照片」讓我為您辨識喔！🌿")
                    continue

                # --------------------------
                # 📸 處理圖片 (辨識失敗與信心度防呆)
                # --------------------------
                elif msg_type == "image":
                    image_id = message.get("image", {}).get("id")
                    if phone_number_id and from_number:
                        send_whatsapp_reply(phone_number_id, from_number, "📸 收到照片了！正在辨識植物與查詢海岸阿美語...")

                    image_result = download_whatsapp_image(image_id)
                    if not image_result.get("ok"):
                        if phone_number_id and from_number:
                            send_whatsapp_reply(phone_number_id, from_number, image_result.get("user_message"))
                        continue

                    plant_result = identify_plant_with_plantid(image_result.get("bytes"))
                    
                    # 【防呆 2】：辨識完全失敗或發生例外狀況
                    if not plant_result.get("ok"):
                        if phone_number_id and from_number:
                            fallback_msg = plant_result.get("user_message") or "⚠️ 辨識失敗，請確保植物佔據畫面主體，並重新拍攝一張清晰的照片喔！"
                            send_whatsapp_reply(phone_number_id, from_number, fallback_msg)
                        continue

                    plant_name = plant_result.get("plant_name")
                    probability = plant_result.get("probability")
                    
                    # 【防呆 3】：信心度過低 (未來可直接沿用至 YOLOv8)
                    # 假設信心度小於 0.4 (40%)，判定為不夠精準，請使用者重拍
                    if probability is not None and probability < 0.4:
                        if phone_number_id and from_number:
                            send_whatsapp_reply(phone_number_id, from_number, f"🤔 我覺得這有點像「{plant_name}」，但信心度不太夠。可以請您換個角度，再拍一張更清晰的照片讓我確認嗎？")
                        continue

                    # 辨識成功，往下交由 Hermes 撈取資料與生成內容
                    prompt = f"照片辨識結果為：{plant_name}"
                    reply_text, _ = process_with_hermes(prompt, from_number)

                    extracted_plant_name = _extract_reply_field(reply_text, "中文名") or _extract_reply_field(reply_text, "辨識結果") or plant_name
                    extracted_amis_name = _extract_reply_field(reply_text, "阿美族語名") or _extract_reply_field(reply_text, "阿美語名") or "tatukem"
                    extracted_efficacy = _extract_reply_field(reply_text, "補充") or _extract_reply_field(reply_text, "介紹") or "具有傳統民俗植物用途。"

                    # 語音合成
                    custom_audio_url = generate_bilingual_tts_audio(extracted_plant_name, extracted_amis_name, extracted_efficacy)

                    if phone_number_id and from_number:
                        send_whatsapp_glasses_readout(phone_number_id, from_number, reply_text)
                        send_whatsapp_reply(phone_number_id, from_number, reply_text)
                        if custom_audio_url:
                            send_whatsapp_audio(phone_number_id, from_number, custom_audio_url)
                
                # --------------------------
                # ⚠️ 處理貼圖、語音檔、文件等其他訊息 (防呆機制)
                # --------------------------
                else:
                    print(f"ℹ️ 收到其他類型的訊息: {msg_type}")
                    if phone_number_id and from_number:
                        send_whatsapp_reply(phone_number_id, from_number, "目前我只看得懂「植物的照片」喔！請傳送照片讓我為您辨識。📷")

            except Exception as e:
                print(f"❌ 背景處理發生錯誤: {e}")
                traceback.print_exc()

# ==========================================
# 🚀 Webhook 核心控制器
# ==========================================
@app.route("/")
def home():
    return "Amis Bot Webhook is running perfectly! (With Custom Bilingual TTS & Error Handling)"

@app.route("/health")
def health():
    return jsonify({"ok": True}), 200

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == VERIFY_TOKEN:
            return challenge, 200
        return "Verification failed", 403

    elif request.method == "POST":
        data = request.get_json(silent=True) or {}
        try:
            if data.get("object") == "whatsapp_business_account":
                for entry in data.get("entry", []):
                    for change in entry.get("changes", []):
                        value = change.get("value", {})
                        thread = threading.Thread(target=background_processor, args=(value,))
                        thread.start()
        except Exception as e:
            print(f"❌ 接收訊息錯誤: {e}")
            traceback.print_exc()
        return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
