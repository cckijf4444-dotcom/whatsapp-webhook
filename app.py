from flask import Flask, request, jsonify
import os
import requests
import base64

app = Flask(__name__)

# ==========================================
# 🔐 環境變數設定區
# ==========================================
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "my_verify_token_123")
ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN")
AZURE_API_KEY = os.environ.get("AZURE_API_KEY")
HERMES_API_URL = os.environ.get("HERMES_API_URL")
PLANT_ID_API_KEY = os.environ.get("PLANT_ID_API_KEY") # 新增 Plant.id 鑰匙

# ==========================================
# 🧠 AI 區塊一：呼叫 Azure OpenAI (翻譯大腦)
# ==========================================
def process_with_hermes(input_text):
    print(f"🧠 [Azure大腦] 準備翻譯: {input_text}")
    try:
        headers = {
            "Content-Type": "application/json",
            "api-key": AZURE_API_KEY
        }
        payload = {
            "messages": [
                {"role": "system", "content": "你是一個專業的阿美族語小幫手。如果使用者詢問植物，請提供該植物的阿美族語名稱與羅馬拼音，並做簡單的一句介紹。"},
                {"role": "user", "content": input_text}
            ]
        }
        response = requests.post(HERMES_API_URL, headers=headers, json=payload)
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
        else:
            return "抱歉，微軟 AI 大腦暫時無法連線！"
    except Exception as e:
        return f"微軟神經網路異常: {e}"

# ==========================================
# 🌿 AI 區塊二：呼叫 Plant.id (視覺大腦)
# ==========================================
def identify_plant_with_plantid(image_bytes):
    print("🌿 [Plant.id] 正在辨識植物特徵...")
    url = "https://api.plant.id/v2/identify"
    headers = {
        "Api-Key": PLANT_ID_API_KEY,
        "Content-Type": "application/json"
    }
    # 將二進位圖片轉成 Plant.id 規定的 Base64 字串
    base64_image = base64.b64encode(image_bytes).decode('ascii')
    payload = {
        "images": [base64_image],
        "plant_details": ["common_names"]
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            data = response.json()
            if data.get("suggestions") and len(data["suggestions"]) > 0:
                best_match = data["suggestions"][0]
                # 嘗試抓取俗名，沒有的話就抓學名
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
    
    # 步驟 1：用 ID 換取真實網址
    url_request = f"https://graph.facebook.com/v18.0/{media_id}"
    res = requests.get(url_request, headers=headers)
    if res.status_code == 200:
        media_url = res.json().get('url')
        
        # 步驟 2：去真實網址下載圖片檔案
        img_res = requests.get(media_url, headers=headers)
        if img_res.status_code == 200:
            print("✅ 圖片下載成功！")
            return img_res.content
    print("❌ 圖片下載失敗")
    return None

# ==========================================
# 📤 發送訊息回 WhatsApp
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

# ==========================================
# 🚀 Webhook 核心控制器
# ==========================================
@app.route('/')
def home():
    return "Amis Bot Webhook with Vision is running!"

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
                                
                                # 處理純文字
                                if msg_type == 'text':
                                    text = message['text']['body']
                                    final_answer = process_with_hermes(text)
                                    send_whatsapp_reply(phone_number_id, from_number, final_answer)
                                        
                                # 處理圖片：進入完美流水線
                                elif msg_type == 'image':
                                    image_id = message['image']['id']
                                    
                                    # 1. 告訴使用者已收到
                                    send_whatsapp_reply(phone_number_id, from_number, "📸 收到照片了！正在請 Plant.id 辨識植物特徵...")
                                    
                                    # 2. 下載圖片
                                    image_bytes = download_whatsapp_image(image_id)
                                    
                                    if image_bytes:
                                        # 3. 交給 Plant.id 辨識
                                        plant_name = identify_plant_with_plantid(image_bytes)
                                        
                                        if plant_name:
                                            send_whatsapp_reply(phone_number_id, from_number, f"🌿 視覺辨識結果：這可能是「{plant_name}」。\n正在請微軟大腦翻譯成阿美語...")
                                            
                                            # 4. 辨識成功，把植物名稱丟給 Azure 大腦翻譯
                                            prompt = f"使用者上傳了一張植物照片，系統辨識出它是「{plant_name}」。請告訴我它的阿美語怎麼說，並做簡單介紹。"
                                            final_translation = process_with_hermes(prompt)
                                            send_whatsapp_reply(phone_number_id, from_number, final_translation)
                                        else:
                                            send_whatsapp_reply(phone_number_id, from_number, "抱歉，Plant.id 視覺大腦看不出這是什麼植物。")
                                    else:
                                        send_whatsapp_reply(phone_number_id, from_number, "⚠️ 從系統下載照片失敗，請再傳一次。")
                                        
        except Exception as e:
            print(f"❌ 嚴重錯誤: {e}")
        
        return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
