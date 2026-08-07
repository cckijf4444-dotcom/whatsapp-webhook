from flask import Flask, request, jsonify
import os
import requests
import base64
import threading
import hashlib
import builtins
import traceback

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
# 🔐 環境變數設定區
# ==========================================
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "my_verify_token_123")
ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN")
HERMES_API_URL = os.environ.get("HERMES_API_URL")
PLANT_ID_API_KEY = os.environ.get("PLANT_ID_API_KEY")

REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "30"))
PLANT_ID_TIMEOUT = int(os.environ.get("PLANT_ID_TIMEOUT", str(REQUEST_TIMEOUT)))


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
    }
    if chat_id:
        payload["chatId"] = chat_id

    try:
        print(f"🧾 [HERMES] POST {HERMES_API_URL}")
        print(f"🧾 [HERMES] payload={payload}")

        response = requests.post(
            HERMES_API_URL,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        
        # 取得 HERMES 回傳的原始文字
        raw_body = safe_response_text(response)
        print(f"🧾 [HERMES] status={response.status_code}")
        print(f"🧾 [HERMES] body={raw_body}")

        if response.status_code == 200:
            data = response.json()
            reply_text = data.get("reply_text") or data.get("message")
            
            # 如果 HERMES 雖然回傳 200，但內容是空白的，把原始結果印在 WhatsApp 方便除錯
            if not reply_text:
                error_msg = (
                    "⚠️ 收到 HERMES 空白回覆\n"
                    f"👉 HERMES 原始資料：\n{raw_body}"
                )
                return error_msg, None
                
            audio_url = data.get("audio_url")
            return reply_text, audio_url

        # 如果發生 500 等錯誤，直接把錯誤碼跟原始資料丟到 WhatsApp
        error_msg = (
            f"❌ HERMES 連線錯誤 (狀態碼: {response.status_code})\n"
            f"👉 錯誤內容：\n{raw_body}"
        )
        return error_msg, None

    except requests.exceptions.Timeout:
        print("❌ [HERMES] 請求逾時")
        return "❌ 呼叫 HERMES 逾時 (已超過等待時間)", None
    except Exception as e:
        print(f"❌ [HERMES] 呼叫異常: {e}")
        return f"❌ 呼叫 HERMES 發生異常: {e}", None


# ==========================================
# 🌿 視覺區塊：呼叫 Plant.id
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
        print("⚠️ 尚未設定 PLANT_ID_API_KEY")
        return {
            "ok": False,
            "error_code": "missing_api_key",
            "status_code": None,
            "user_message": "⚠️ Render 尚未設定 Plant.id API 金鑰，請先在環境變數加入 PLANT_ID_API_KEY。",
        }

    if not image_bytes:
        print("⚠️ Plant.id 收到空白圖片內容")
        return {
            "ok": False,
            "error_code": "empty_image_bytes",
            "status_code": None,
            "user_message": "⚠️ 系統沒有拿到有效圖片內容，請再傳一次。",
        }

    image_size = len(image_bytes)
    image_sha256 = hashlib.sha256(image_bytes).hexdigest()
    print(f"🌿 [Plant.id] image_size={image_size} sha256={image_sha256[:16]}...")

    base64_image = base64.b64encode(image_bytes).decode("ascii")
    last_status_code = None
    last_error_code = "unknown"

    # 優先走 v3 介面
    v3_url = "https://plant.id/api/v3/identification"
    v3_headers = {
        "Api-Key": PLANT_ID_API_KEY,
        "Content-Type": "application/json",
    }
    v3_payload = {
        "images": [base64_image],
    }

    try:
        response = requests.post(
            v3_url,
            headers=v3_headers,
            json=v3_payload,
            timeout=PLANT_ID_TIMEOUT,
        )
        last_status_code = response.status_code
        print(f"🌿 [Plant.id v3] status={response.status_code}")
        print(f"🌿 [Plant.id v3] body={safe_response_text(response)}")

        if response.status_code in (200, 201):
            data = response.json()
            suggestions = data.get("result", {}).get("classification", {}).get("suggestions", []) or []
            if suggestions:
                best_match = suggestions[0]
                plant_name = best_match.get("name")
                probability = best_match.get("probability")
                if plant_name:
                    return _plantid_success_result(plant_name, probability, "v3")
            last_error_code = "no_suggestions"
        else:
            last_error_code = f"http_{response.status_code}"
    except requests.exceptions.Timeout:
        print("❌ [Plant.id v3] 請求逾時")
        last_error_code = "timeout"
    except Exception as e:
        print(f"❌ [Plant.id v3] 辨識失敗: {e}")
        last_error_code = "exception"

    # v3 若失敗，再 fallback 舊版 v2
    v2_url = "https://api.plant.id/v2/identify"
    v2_headers = {
        "Api-Key": PLANT_ID_API_KEY,
        "Content-Type": "application/json",
    }
    v2_payload = {
        "images": [base64_image],
        "plant_details": ["common_names"],
        "language": "zh-tw",
    }

    try:
        response = requests.post(
            v2_url,
            headers=v2_headers,
            json=v2_payload,
            timeout=PLANT_ID_TIMEOUT,
        )
        last_status_code = response.status_code
        print(f"🌿 [Plant.id v2] status={response.status_code}")
        print(f"🌿 [Plant.id v2] body={safe_response_text(response)}")

        if response.status_code == 200:
            data = response.json()
            suggestions = data.get("suggestions") or []
            if suggestions:
                best_match = suggestions[0]
                names = best_match.get("plant_details", {}).get("common_names", []) or []
                plant_name = names[0] if names else best_match.get("plant_name")
                probability = best_match.get("probability")
                if plant_name:
                    return _plantid_success_result(plant_name, probability, "v2")
            last_error_code = "no_suggestions"
        else:
            last_error_code = f"http_{response.status_code}"
    except requests.exceptions.Timeout:
        print("❌ [Plant.id v2] 請求逾時")
        last_error_code = "timeout"
    except Exception as e:
        print(f"❌ [Plant.id v2] 辨識失敗: {e}")
        last_error_code = "exception"

    if last_error_code == "no_suggestions":
        user_message = "抱歉，Plant.id 視覺大腦暫時看不出這是什麼植物。"
    elif last_error_code == "timeout":
        user_message = "⚠️ Plant.id 辨識逾時，請稍後再試。"
    elif last_status_code:
        user_message = f"⚠️ Plant.id 辨識服務暫時異常（狀態碼 {last_status_code}），請稍後再試。"
    else:
        user_message = "⚠️ Plant.id 辨識時發生異常，請稍後再試。"

    return {
        "ok": False,
        "error_code": last_error_code,
        "status_code": last_status_code,
        "user_message": user_message,
    }


# ==========================================
# 📥 實用工具：從 WhatsApp 下載真實圖片
# ==========================================
def download_whatsapp_image(media_id):
    print(f"📥 [Meta] 準備下載圖片 ID: {media_id}")

    if not media_id:
        print("⚠️ webhook 沒有帶 image.id")
        return {
            "ok": False,
            "user_message": "⚠️ 這張圖片沒有拿到 media id，請再傳一次。",
            "bytes": None,
        }

    if not ACCESS_TOKEN:
        print("⚠️ 尚未設定 WHATSAPP_ACCESS_TOKEN")
        return {
            "ok": False,
            "user_message": "⚠️ 尚未設定 WHATSAPP_ACCESS_TOKEN，無法下載 WhatsApp 圖片。",
            "bytes": None,
        }

    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    url_request = f"https://graph.facebook.com/v18.0/{media_id}"

    try:
        res = requests.get(url_request, headers=headers, timeout=REQUEST_TIMEOUT)
        print(f"📥 [Meta media lookup] status={res.status_code}")
        print(f"📥 [Meta media lookup] body={safe_response_text(res)}")

        if res.status_code != 200:
            return {
                "ok": False,
                "user_message": f"⚠️ 圖片資訊查詢失敗（狀態碼 {res.status_code}），請再傳一次。",
                "bytes": None,
            }

        media_url = res.json().get("url")
        if not media_url:
            print("❌ [Meta] media lookup 成功，但沒有拿到 url")
            return {
                "ok": False,
                "user_message": "⚠️ 系統沒有拿到圖片下載網址，請再傳一次。",
                "bytes": None,
            }

        img_res = requests.get(media_url, headers=headers, timeout=REQUEST_TIMEOUT)
        content_type = img_res.headers.get("Content-Type")
        content_length = len(img_res.content) if img_res.content else 0
        print(f"📥 [Meta media download] status={img_res.status_code}")
        print(f"📥 [Meta media download] content_type={content_type}")
        print(f"📥 [Meta media download] content_length={content_length}")

        if img_res.status_code == 200 and img_res.content:
            image_sha256 = hashlib.sha256(img_res.content).hexdigest()
            print(f"✅ 圖片下載成功！sha256={image_sha256[:16]}...")
            return {
                "ok": True,
                "user_message": None,
                "bytes": img_res.content,
                "content_type": content_type,
                "content_length": content_length,
                "sha256": image_sha256,
            }

    except Exception as e:
        print(f"❌ [Meta] 圖片下載失敗: {e}")

    print("❌ 圖片下載失敗")
    return {
        "ok": False,
        "user_message": "⚠️ 從 WhatsApp 系統下載照片失敗，請再傳一次。",
        "bytes": None,
    }


# ==========================================
# 📤 發送訊息回 WhatsApp (文字與語音模組)
# ==========================================
def send_whatsapp_reply(phone_number_id, recipient_number, reply_text):
    if not reply_text:
        print("ℹ️ [WA text] reply_text 為空，略過發送")
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

    print(f"📤 [WA text] POST {url}")
    print(f"📤 [WA text] payload={payload}")

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        print(f"📤 [WA text] status={response.status_code}")
        print(f"📤 [WA text] body={safe_response_text(response)}")
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

    print(f"🎵 [WA audio] 準備發送語音給 {recipient_number}")
    print(f"🎵 [WA audio] audio_link={audio_link}")
    print(f"🎵 [WA audio] payload={payload}")

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        print(f"📤 [WA audio] status={response.status_code}")
        print(f"📤 [WA audio] body={safe_response_text(response)}")
        if response.status_code == 200:
            print("✅ 成功回傳語音給使用者！")
        else:
            print(f"❌ 語音回傳失敗，錯誤碼：{response.status_code}")
    except Exception as e:
        print(f"❌ [WA audio] 發送失敗: {e}")


# ==========================================
# ⚙️ 專門負責耗時工作的背景處理器
# ==========================================
def background_processor(value):
    phone_number_id = value.get("metadata", {}).get("phone_number_id")
    print(f"🧵 [background] phone_number_id={phone_number_id}")

    if "messages" in value:
        for message in value["messages"]:
            from_number = message.get("from")
            msg_type = message.get("type")
            print(f"🧵 [background] from={from_number}, type={msg_type}")

            try:
                # --------------------------
                # 💬 處理純文字
                # --------------------------
                if msg_type == "text":
                    text = message.get("text", {}).get("body", "")
                    print(f"💬 收到文字訊息: {text}")

                    reply_text, audio_url = process_with_hermes(text, from_number)

                    if phone_number_id and from_number:
                        send_whatsapp_reply(phone_number_id, from_number, reply_text)
                        if audio_url:
                            send_whatsapp_audio(phone_number_id, from_number, audio_url)

                # --------------------------
                # 📸 處理圖片
                # --------------------------
                elif msg_type == "image":
                    image_id = message.get("image", {}).get("id")
                    print(f"📸 收到圖片訊息 image_id={image_id}")

                    if phone_number_id and from_number:
                        send_whatsapp_reply(phone_number_id, from_number, "📸 收到照片了！正在辨識植物與查詢阿美語...")

                    image_result = download_whatsapp_image(image_id)
                    if not image_result.get("ok"):
                        if phone_number_id and from_number:
                            send_whatsapp_reply(phone_number_id, from_number, image_result.get("user_message") or "⚠️ 從系統下載照片失敗，請再傳一次。")
                        continue

                    plant_result = identify_plant_with_plantid(image_result.get("bytes"))
                    if not plant_result.get("ok"):
                        if phone_number_id and from_number:
                            send_whatsapp_reply(phone_number_id, from_number, plant_result.get("user_message") or "抱歉，Plant.id 視覺大腦暫時看不出這是什麼植物。")
                        continue

                    plant_name = plant_result.get("plant_name")
                    probability = plant_result.get("probability")
                    source = plant_result.get("source")
                    print(f"🌿 [Plant.id] 辨識成功 source={source} plant_name={plant_name} probability={probability}")

                    prompt = f"照片辨識結果為：{plant_name}"
                    reply_text, audio_url = process_with_hermes(prompt, from_number)

                    if phone_number_id and from_number:
                        send_whatsapp_reply(phone_number_id, from_number, reply_text)
                        if audio_url:
                            send_whatsapp_audio(phone_number_id, from_number, audio_url)
                else:
                    print(f"ℹ️ 尚未處理的訊息類型: {msg_type}")

            except Exception as e:
                print(f"❌ 背景處理發生錯誤: {e}")
                traceback.print_exc()


# ==========================================
# 🚀 Webhook 核心控制器
# ==========================================
@app.route("/")
def home():
    return "Amis Bot Webhook is running perfectly! (Async Mode Connected to HERMES)"


@app.route("/health")
def health():
    return jsonify(
        {
            "ok": True,
            "verify_token_configured": bool(VERIFY_TOKEN),
            "whatsapp_access_token_configured": bool(ACCESS_TOKEN),
            "hermes_api_url_configured": bool(HERMES_API_URL),
            "plant_id_api_key_configured": bool(PLANT_ID_API_KEY),
            "request_timeout": REQUEST_TIMEOUT,
            "plant_id_timeout": PLANT_ID_TIMEOUT,
        }
    ), 200


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
        print(f"📨 [webhook] 收到 POST: {data}")

        try:
            if data.get("object") == "whatsapp_business_account":
                for entry in data.get("entry", []):
                    for change in entry.get("changes", []):
                        value = change.get("value", {})

                        thread = threading.Thread(target=background_processor, args=(value,))
                        thread.start()
            else:
                print("ℹ️ [webhook] 非 whatsapp_business_account 事件，直接略過")

        except Exception as e:
            print(f"❌ 接收訊息錯誤: {e}")
            traceback.print_exc()

        return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
