from flask import Flask, request, jsonify
import os
import requests
import base64

app = Flask(__name__)

# ==========================================
# 🔐 環境變數設定區 (AZURE_API_KEY 已功成身退被移除了)
# ==========================================
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "my_verify_token_123")
ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN")
HERMES_API_URL = os.environ.get("HERMES_API_URL") # 這裡要填朋友給的 Ngrok 網址
PLANT_ID_API_KEY = os.environ.get("PLANT_ID_API_KEY")

# ==========================================
# 🧠 大腦區塊：呼叫朋友的 HERMES (Ngrok)
# ==========================================
def process_with_hermes(input_text):
    print(f"🧠 [HERMES] 準備將資料送往朋友的資料庫: {input_text}")
    
    if not HERMES_API_URL:
        return "⚠️ 尚未設定 HERMES_API_URL (朋友的 Ngrok 網址)", None

    # 依照與朋友約定的格式，打包要問的問題
    payload = {
        "text": input_text
    }
    
    try:
        response = requests.post(HERMES_API_URL, json=payload)
        
        if response.status_code == 200:
            data = response.json()
            
            # 接收朋友回傳的文字與語音網址
            reply_text = data.get("reply_text", "抱歉，無法解析 HERMES 回傳的文字。")
            audio_url = data.get("audio_url") # 如果他沒給語音，這裡就會是 None
            
            return reply_text, audio_url
            
        else:
            return f"❌ HERMES 連線錯誤 (狀態碼: {response.status_code})", None
            
    except Exception as e:
        return f"❌ 呼叫 HERMES 發生異常: {e}", None

# ==========================================
# 🌿 視覺區塊：呼叫 Plant.id
# ==========================================
def identify_plant_with_plantid(image_bytes):
    print("🌿 [Plant.id] 正在辨識植物特徵...")
    
    if not PLANT_ID_API_KEY:
        print("⚠️ 尚未設定 PLANT_ID_API_KEY")
        return None

    url = "https://api.plant.id/v2/identify"
    headers = {
        "Api-Key": PLANT_ID_API_KEY,
        "Content-Type": "application/json"
    }
    
    # 將圖片轉換成 Base64 格式
    base64_image = base64.b64encode(image_bytes).decode('ascii')
    payload = {
        "images": [base64_image],
        "plant_details": ["common_names"]
        "language": "zh"
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            data = response.json()
            if data.get("suggestions") and len(data["suggestions"]) > 0:
                best_match = data["suggestions"][0]
                names = best_match.get("plant_details", {}).get("common_names", [])
                plant_name = names[0] if names else best_match.get("plant_name", "未知植物")
                return plant_name
        return None
    except Exception as e:
        print(f"❌ Plant.id 辨識失敗: {e}")
        return None

# ==========================================
# 📥 實用工具：從 WhatsApp 下載真實圖片
# ==========================================
def download_whatsapp_image(media_id):
    print(f"📥 [Meta 伺服器] 準備下載圖片 ID: {media_id}")
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    
    url_request = f"https://graph.facebook.com/v18.0/{media_id}"
    res = requests.get(url_request, headers=headers)
    if res.status_code == 200:
        media_url = res.json().get('url')
        img_res = requests.get(media_url, headers=headers)
        if img_res.status_code == 200:
            print("✅ 圖片下載成功！")
            return img_res.content
    print("❌ 圖片下載失敗")
    return None

# ==========================================
# 📤 發送訊息回 WhatsApp (文字與語音模組)
# ==========================================
def send_whatsapp_reply(phone_number_id, recipient_number, reply_text):
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
    requests.post(url, headers=headers, json=payload)

def send_whatsapp_audio(phone_number_id, recipient_number, audio_link):
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
    print(f"🎵 準備發送語音訊息給 {recipient_number}...")
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        print("✅ 成功回傳語音給使用者！")
    else:
        print(f"❌ 語音回傳失敗，錯誤碼：{response.status_code}")

# ==========================================
# 🚀 Webhook 核心控制器
# ==========================================
@app.route('/')
def home():
    return "Amis Bot Webhook is running perfectly! (Connected to Ngrok HERMES)"

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        if mode == 'subscribe' and token == VERIFY_TOKEN:
            return challenge, 200
        return "Verification failed", 403
    
    elif request.method == 'POST':
        data = request.get_json()
        try:
            if data.get('object') == 'whatsapp_business_account':
                for entry in data.get('entry', []):
                    for change in entry.get('changes', []):
                        value = change.get('value', {})
                        phone_number_id = value.get('metadata', {}).get('phone_number_id')
                        
                        if 'messages' in value:
                            for message in value['messages']:
                                from_number = message.get('from')
                                msg_type = message.get('type')
                                
                                # --------------------------
                                # 💬 處理純文字
                                # --------------------------
                                if msg_type == 'text':
                                    text = message['text']['body']
                                    print(f"💬 收到文字訊息: {text}")
                                    
                                    # 丟給朋友的 HERMES 處理
                                    reply_text, audio_url = process_with_hermes(text)
                                    
                                    # 回傳文字
                                    if phone_number_id and from_number:
                                        send_whatsapp_reply(phone_number_id, from_number, reply_text)
                                        # 如果朋友有傳回語音網址，就加碼回傳語音
                                        if audio_url:
                                            send_whatsapp_audio(phone_number_id, from_number, audio_url)
                                        
                                # --------------------------
                                # 📸 處理圖片
                                # --------------------------
                                elif msg_type == 'image':
                                    image_id = message['image']['id']
                                    
                                    # 1. 告訴使用者已收到
                                    send_whatsapp_reply(phone_number_id, from_number, "📸 收到照片了！正在辨識植物與查詢阿美語...")
                                    
                                    # 2. 下載圖片
                                    image_bytes = download_whatsapp_image(image_id)
                                    
                                    if image_bytes:
                                        # 3. 交給 Plant.id 辨識
                                        plant_name = identify_plant_with_plantid(image_bytes)
                                        
                                        if plant_name:
                                            # 4. 把植物名稱丟給朋友的 HERMES 找資料庫與語音
                                            prompt = f"照片辨識結果為：{plant_name}"
                                            reply_text, audio_url = process_with_hermes(prompt)
                                            
                                            # 5. 回傳最終結果
                                            send_whatsapp_reply(phone_number_id, from_number, reply_text)
                                            if audio_url:
                                                send_whatsapp_audio(phone_number_id, from_number, audio_url)
                                        else:
                                            send_whatsapp_reply(phone_number_id, from_number, "抱歉，Plant.id 視覺大腦看不出這是什麼植物。")
                                    else:
                                        send_whatsapp_reply(phone_number_id, from_number, "⚠️ 從系統下載照片失敗，請再傳一次。")
                                        
        except Exception as e:
            print(f"❌ 處理訊息時發生嚴重錯誤: {e}")
        
        return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
