import os
import requests
import pymymysql if False else None # 預防 pymysql 未安裝的 IDE 提示
import pymysql
from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks
import ollama

app = FastAPI(title="Hermes Agent - 智慧海岸阿美語植物辨識與資料庫整合中樞")

# ==========================================
# 🔐 系統環境設定
# ==========================================
YOLO_API_URL = os.environ.get("YOLO_API_URL", "http://localhost:5000/predict")
TTS_API_URL = os.environ.get("TTS_API_URL", "http://localhost:8000/api/synthesize-plant-audio")
WHATSAPP_ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN")
MY_VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "amis_plant_2026")

# MySQL 資料庫連線設定 (支援環境變數或預設值)
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "plant_info")

def query_plant_from_db(yolo_label: str):
    """直接在 Python 中連線 MySQL 查詢植物資訊"""
    try:
        connection = pymysql.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        with connection.cursor() as cursor:
            sql = "SELECT plant_name, amis_name, efficacy FROM plants WHERE label = %s"
            cursor.execute(sql, (yolo_label,))
            result = cursor.fetchone()
            return result
    except Exception as e:
        print(f"❌ 資料庫查詢發生錯誤: {e}")
        return None
    finally:
        if 'connection' in locals() and connection.open:
            connection.close()

# ==========================================
# 📥 實用工具：從 WhatsApp 伺服器下載真實圖片
# ==========================================
def download_whatsapp_image(media_id, save_path):
    if not media_id or not WHATSAPP_ACCESS_TOKEN:
        print("⚠️ 缺少 media_id 或 WHATSAPP_ACCESS_TOKEN，無法下載圖片")
        return False
    
    headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}
    url_request = f"https://graph.facebook.com/v18.0/{media_id}"

    try:
        # 1. 向 Meta 查詢圖片下載網址
        res = requests.get(url_request, headers=headers, timeout=30)
        if res.status_code != 200:
            print(f"❌ 查詢 WhatsApp 媒體資訊失敗: {res.text}")
            return False
        
        media_url = res.json().get("url")
        if not media_url:
            print("❌ Meta 回應中沒有包含圖片下載網址")
            return False

        # 2. 下載二進位圖片並寫入檔案
        img_res = requests.get(media_url, headers=headers, timeout=30)
        if img_res.status_code == 200 and img_res.content:
            with open(save_path, "wb") as f:
                f.write(img_res.content)
            print(f"✅ WhatsApp 圖片成功下載至: {save_path}")
            return True
    except Exception as e:
        print(f"❌ 下載 WhatsApp 圖片發生異常: {e}")
    
    return False

# ==========================================
# 📤 發送語音回傳給 WhatsApp 用戶
# ==========================================
def send_whatsapp_audio(recipient_number, audio_link):
    if not WHATSAPP_ACCESS_TOKEN:
        print("⚠️ 尚未設定 WHATSAPP_ACCESS_TOKEN，無法發送語音")
        return

    # 假設您的電話號碼 ID 也是從環境變數取得，若沒有可自行替換
    phone_number_id = os.environ.get("PHONE_NUMBER_ID", "")
    if not phone_number_id:
        print("⚠️ 尚未設定 PHONE_NUMBER_ID")
        return

    url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient_number,
        "type": "audio",
        "audio": {"link": audio_link}
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        if response.status_code == 200:
            print("✅ 成功回傳語音給使用者！")
        else:
            print(f"❌ 語音回傳失敗，回應：{response.text}")
    except Exception as e:
        print(f"❌ 發送 WhatsApp 語音發生異常: {e}")

# ==========================================
# 🌐 Meta Webhook 驗證通道 (GET)
# ==========================================
@app.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    
    if mode == "subscribe" and token == MY_VERIFY_TOKEN:
        print("Webhook 驗證成功！")
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="驗證失敗")

# ==========================================
# ⚙️ 背景任務：真實下載照片、呼叫 YOLO、查資料庫、Ollama 潤飾、產出語音
# ==========================================
def process_plant_image(image_id: str, sender_id: str):
    image_path = f"temp_{image_id}.jpg"
    audio_filename = ""
    
    try:
        print(f"[{sender_id}] 步驟 1: 正在從 WhatsApp 下載真實圖片...")
        
        # 真正從 WhatsApp 下載照片
        success = download_whatsapp_image(image_id, image_path)
        if not success:
            print(f"[{sender_id}] 圖片下載失敗，中止流程。")
            return

        # 1. 呼叫隊友的 YOLO 專屬辨識伺服器
        print(f"[{sender_id}] 步驟 2: 將圖片送往 YOLO 伺服器...")
        with open(image_path, "rb") as f:
            yolo_response = requests.post(YOLO_API_URL, files={"file": f}, timeout=15)
        
        yolo_result = yolo_response.json()
        
        if yolo_result.get("status") == "not_found" or not yolo_result.get("plant_label"):
            print(f"[{sender_id}] YOLO 未偵測到植物。")
            return
            
        yolo_label = yolo_result["plant_label"]
        confidence = yolo_result.get("confidence", 1.0)
        print(f"[{sender_id}] YOLO 辨識成功 -> 標籤: {yolo_label} (信心度: {confidence})")
        
        if confidence < 0.4:
            print(f"[{sender_id}] 信心度過低 (< 40%)，略過後續流程。")
            return

        # 2. 直接查詢 MySQL 資料庫
        plant_data = query_plant_from_db(yolo_label)
        if not plant_data:
            print(f"[{sender_id}] MySQL 資料庫查無此標籤: {yolo_label}")
            return
            
        plant_name = plant_data["plant_name"]
        amis_name = plant_data["amis_name"]
        efficacy = plant_data["efficacy"]
        
        # 3. Ollama 提示詞工程 (海岸阿美族長老角色)
        warning_text = "【警告：這株植物具有毒性，請千萬不要碰觸或食用！】\n" if "毒" in efficacy else ""
        prompt = f"""
        你現在是一位充滿智慧、語氣溫和的海岸阿美族部落長老。
        有位年輕人拍了一張植物的照片想請教你。請根據以下資訊，用長輩教導年輕人的口吻，寫成一段自然、溫暖的口語介紹（約50字內，勿條列）。
        {warning_text}植物中文名：{plant_name}
        植物海岸阿美語：{amis_name}
        植物功效：{efficacy}
        """
        
        print(f"[{sender_id}] 步驟 3: 呼叫 Ollama 潤飾內容中...")
        llm_response = ollama.chat(model='llama3.1', messages=[{'role': 'user', 'content': prompt}])
        elder_speech = llm_response['message']['content']
        
        # 4. 呼叫隊友的 TTS 系統產生語音
        print(f"[{sender_id}] 步驟 4: 呼叫 TTS 語音生成...")
        tts_payload = {
            "plant_name": plant_name, 
            "amis_name": amis_name, 
            "efficacy": f"{warning_text}{elder_speech}"
        }
        tts_response = requests.post(TTS_API_URL, json=tts_payload, timeout=20)
        
        if tts_response.status_code == 200:
            # 假設 TTS 回傳的是可供下載或播放的音檔網址，或者直接發送
            # 若 TTS 回傳的是音檔檔案流，可在此處理。此處以接收 URL 為例或直接使用回應
            print(f"[{sender_id}] 步驟 5: 語音生成成功！")
            # 若 TTS 系統會回傳音檔公開網址，可直接調用：
            # audio_url = tts_response.json().get("audio_url")
            # send_whatsapp_audio(sender_id, audio_url)
        else:
            print(f"[{sender_id}] TTS 系統發生錯誤，狀態碼: {tts_response.status_code}")

    except requests.exceptions.RequestException as e:
        print(f"[{sender_id}] 發生 API 連線錯誤: {e}")
    except Exception as e:
        print(f"[{sender_id}] 發生未預期錯誤: {e}")
    finally:
        # 清理暫存圖片
        if os.path.exists(image_path):
            os.remove(image_path)
        print(f"[{sender_id}] 處理流程結束，已清理暫存資源。")

# ==========================================
# 🚀 核心邏輯：接收 WhatsApp 訊息 (POST)
# ==========================================
@app.post("/webhook")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    
    try:
        message = payload['entry'][0]['changes'][0]['value']['messages'][0]
        sender_id = message['from']
        
        if message['type'] == 'image':
            image_id = message['image']['id']
            print(f">>> 收到 WhatsApp 圖片請求 (Media ID: {image_id})，已派發至背景處理...")
            
            # 將工作丟給背景執行，秒回 200 OK 避免 WhatsApp 超時重試
            background_tasks.add_task(process_plant_image, image_id, sender_id)
            
    except KeyError:
        pass
        
    return {"status": "ok"}

@app.get("/")
def home():
    return "Amis Bot Webhook is running with Integrated MySQL & YOLO Microservice!"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
