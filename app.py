from flask import Flask, request, jsonify
import os
import requests

app = Flask(__name__)

# ==========================================
# 🔐 環境變數設定區
# ==========================================
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "my_verify_token_123")
ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "請在這裡填寫你的Meta存取權杖")

# ==========================================
# 🧠 第二棒：呼叫 Hermes Agent (大腦區塊)
# ==========================================
def process_with_hermes(input_text):
    print(f"🧠 [Hermes Agent] 正在思考如何回覆: {input_text}")
    hermes_response = f"我是 Amis Bot，我已經收到你的訊息：「{input_text}」。這段是系統自動回覆測試！"
    return hermes_response

# ==========================================
# 📤 第三棒：發送訊息回 WhatsApp (嘴巴區塊)
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
        "text": {
            "body": reply_text
        }
    }
    
    print(f"📤 準備發送回覆給 {recipient_number}...")
    response = requests.post(url, headers=headers, json=payload)
    
    if response.status_code == 200:
        print("✅ 成功回傳訊息給使用者！")
    else:
        print(f"❌ 回傳失敗，錯誤碼：{response.status_code}")
        print(f"錯誤詳情：{response.text}")

# ==========================================
# 📥 第一棒：Webhook 接收端點 (耳朵區塊)
# ==========================================
@app.route('/')
def home():
    return "Amis Bot Webhook is running perfectly!"

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        
        if mode == 'subscribe' and token == VERIFY_TOKEN:
            print("✅ Webhook 驗證成功！")
            return challenge, 200
        else:
            print("❌ Webhook 驗證失敗")
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
                                
                                if msg_type == 'text':
                                    text = message['text']['body']
                                    print(f"💬 收到 {from_number} 的文字訊息: {text}")
                                    
                                    final_answer = process_with_hermes(text)
                                    
                                    if phone_number_id and from_number:
                                        send_whatsapp_reply(phone_number_id, from_number, final_answer)
                                        
                                elif msg_type == 'image':
                                    image_id = message['image']['id']
                                    print(f"📸 收到 {from_number} 的圖片，圖片 ID: {image_id}")
                                    
                                    reply = "收到圖片了！正在交給 Hermes 辨識植物中，請稍候..."
                                    if phone_number_id and from_number:
                                        send_whatsapp_reply(phone_number_id, from_number, reply)
                                        
        except Exception as e:
            print(f"❌ 處理訊息時發生錯誤: {e}")
        
        return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
