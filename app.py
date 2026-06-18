from flask import Flask, request, jsonify
import json
from datetime import datetime

app = Flask(__name__)
VERIFY_TOKEN = 'my_secret_token'

# ===== 1. GET 驗證 =====
@app.route('/webhook', methods=['GET'])
def verify_webhook():
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    print(f"\n🔍 收到驗證請求")
    print(f" mode={mode}")
    print(f" token={token}")
    print(f" challenge={challenge}")

    if mode == 'subscribe' and token == VERIFY_TOKEN:
        print("====== ✅ Webhook 驗證成功！ ======\n")
        return challenge, 200
    else:
        print("====== ❌ Webhook 驗證失敗！token不符 ======\n")
        return 'Forbidden', 403

# ===== 2. POST 收訊息 =====
@app.route('/webhook', methods=['POST'])
def receive_message():
    print("\n" + "="*60)
    print("📩 收到 WhatsApp POST")
    print("時間:", datetime.now().strftime("%H:%M:%S"))

    data = request.get_json(force=True, silent=True)
    print(json.dumps(data, indent=2, ensure_ascii=False))

    try:
        if data and data.get('object') == 'whatsapp_business_account':
            for entry in data.get('entry', []):
                for change in entry.get('changes', []):
                    value = change.get('value', {})
                    if 'messages' in value:
                        contacts = value.get('contacts', [])
                        name = contacts[0]['profile']['name'] if contacts else '未知'
                        for msg in value['messages']:
                            if msg.get('type') == 'text':
                                print(f"\n✅ {name} 說: {msg['text']['body']}")
    except Exception as e:
        print("解析錯誤:", e)

    print("="*60 + "\n")
    return jsonify({"status": "ok"}), 200

@app.route('/', methods=['GET'])
def home():
    return "WhatsApp Webhook is running!", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
