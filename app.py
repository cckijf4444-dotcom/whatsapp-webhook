from flask import Flask, request, jsonify
import os

app = Flask(__name__)

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "my_verify_token_123")

@app.route('/')
def home():
    return "WhatsApp Webhook is running!"

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        # Meta 驗證 webhook
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        
        print(f"🔍 收到驗證請求: mode={mode}, token={token}")
        
        if mode == 'subscribe' and token == VERIFY_TOKEN:
            print("✅ Webhook 驗證成功！")
            return challenge, 200
        else:
            print("❌ 驗證失敗")
            return "Verification failed", 403
    
    elif request.method == 'POST':
        # 接收 WhatsApp 訊息
        data = request.get_json()
        print("📩 收到 WhatsApp POST")
        print(f"完整資料: {data}")
        
        try:
            if data.get('object') == 'whatsapp_business_account':
                for entry in data.get('entry', []):
                    for change in entry.get('changes', []):
                        value = change.get('value', {})
                        
                        # 處理訊息
                        if 'messages' in value:
                            for message in value['messages']:
                                from_number = message.get('from')
                                msg_type = message.get('type')
                                
                                if msg_type == 'text':
                                    text = message['text']['body']
                                    print(f"✅ {from_number} 說: {text}")
                                else:
                                    print(f"✅ {from_number} 傳送了 {msg_type}")
                        
                        # 處理狀態
                        if 'statuses' in value:
                            for status in value['statuses']:
                                print(f"📊 狀態更新: {status.get('status')}")
        except Exception as e:
            print(f"❌ 處理錯誤: {e}")
        
        return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
