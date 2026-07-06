from flask import Flask, request, jsonify
import os
import requests

app = Flask(__name__)

# ==========================================
# 🔐 環境變數設定區
# ==========================================
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "my_verify_token_123")
ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN")
AZURE_API_KEY = os.environ.get("AZURE_API_KEY")
HERMES_API_URL = os.environ.get("HERMES_API_URL")

# ==========================================
# 🧠 第二棒：呼叫 Hermes Agent (Azure OpenAI 版)
# ==========================================
def process_with_hermes(input_text):
    print(f"🧠 [Hermes Agent] 準備將資料送往 Azure OpenAI: {input_text}")
    
    try:
        # 1. 準備 Azure 專用的 Headers (放鑰匙的地方)
        headers = {
            "Content-Type": "application/json",
            "api-key": AZURE_API_KEY
        }

        # 2. 準備 OpenAI 專屬的對話格式
        payload = {
            "messages": [
                {"role": "system", "content": "你是一個專業的阿美族語翻譯小幫手，請將使用者的話精準翻譯成阿美族語，並提供羅馬拼音。"},
                {"role": "user", "content": input_text}
            ]
        }
        
        # 3. 發送請求給 Azure
        response = requests.post(HERMES_API_URL, headers=headers, json=payload)
        
        # 4. 拆解微軟回傳的複雜包裹
        if response.status_code == 200:
            data = response.json()
            # 從 OpenAI 複雜的 JSON 結構中精準挖出回覆內容
            hermes_reply = data["choices"][0]["message"]["content"]
            return hermes_reply
            
        else:
            print(f"❌ Azure API 錯誤: {response.status_code} - {response.text}")
            return "抱歉，微軟 AI 大腦暫時無法連線，請稍後再試！"
            
    except Exception as e:
        print(f"❌ 呼叫 Azure 時發生異常: {e}")
        return "抱歉，神經網路發生異常，請檢查連線！"

# ==========================================
# 📤 第三棒：發送訊息回 WhatsApp
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
# 📥 第一棒：Webhook 接收端點
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
            return challenge, 200
        else:
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
                                    
                                    # 觸發 Azure 大腦
                                    final_answer = process_with_hermes(text)
                                    
                                    if phone_number_id and from_number:
                                        send_whatsapp_reply(phone_number_id, from_number, final_answer)
                                        
                                elif msg_type == 'image':
                                    reply = "收到圖片了！目前仍在開發影像辨識功能中..."
                                    if phone_number_id and from_number:
                                        send_whatsapp_reply(phone_number_id, from_number, reply)
                                        
        except Exception as e:
            print(f"❌ 處理訊息時發生錯誤: {e}")
        
        return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
