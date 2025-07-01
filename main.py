import os
import sys
from dotenv import load_dotenv

from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError

# 修正: PushMessage のインポートは完全に削除されています
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage

from linebot.v3.webhooks import MessageEvent, TextMessageContent

import google.generativeai as genai
# 修正: HarmCategory と HarmBlockThreshold を正しくインポートします
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# .env ファイルから環境変数をロード
load_dotenv()

app = Flask(__name__)

# 環境変数の設定
CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY') 

# 環境変数が設定されているか確認
if CHANNEL_SECRET is None:
    print('Specify LINE_CHANNEL_SECRET as environment variable.')
    sys.exit(1)
if CHANNEL_ACCESS_TOKEN is None:
    print('Specify LINE_CHANNEL_ACCESS_TOKEN as environment variable.')
    sys.exit(1)
# GEMINI_API_KEY は必須ではないため、アプリケーションの起動自体をブロックしないようにチェックを調整
# ただし、Gemini機能が有効になる場合は必須
# if GEMINI_API_KEY is None:
#     print('Specify GEMINI_API_KEY as environment variable.')
#     sys.exit(1)

handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

# Gemini APIの初期化
chat = None
try:
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        
        # 使用するモデル名 (ログから 'gemini-2.5-flash-lite-preview-06-17' が確認できたため使用)
        GEMINI_MODEL_NAME = 'gemini-2.5-flash-lite-preview-06-17' 
        
        # モデルが利用可能か確認（時間のかかる処理なので、デバッグ時以外はコメントアウトしても良い）
        model_exists = False
        try:
            for m in genai.list_models():
                if GEMINI_MODEL_NAME == m.name:
                    model_exists = True
                    break
            if not model_exists:
                raise Exception(f"The specified Gemini model '{GEMINI_MODEL_NAME}' is not available for your API key/region. Please check model list and regional availability.")
        except Exception as e:
            # list_models() 自体がAPIキーのエラーで失敗する可能性もあるため、ここでキャッチ
            raise Exception(f"Failed to list Gemini models. Check GEMINI_API_KEY or network connectivity. Original error: {e}")

        model = genai.GenerativeModel(
            GEMINI_MODEL_NAME,
            safety_settings={
                HarmCategory.HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            }
        )
        chat = model.start_chat(history=[])
        print(f"Gemini API configured successfully using '{GEMINI_MODEL_NAME}' model.")
    else:
        print("GEMINI_API_KEY is not set. Gemini API functionality will be disabled.")
except Exception as e:
    print(f"CRITICAL: Gemini API configuration failed: {e}. Please check GEMINI_API_KEY, google-generativeai library version in requirements.txt, and ensure the model is available for your API key/region.")
    chat = None # エラーが発生した場合は chat を None に設定

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.info("Invalid signature. Please check your channel access token/channel secret.")
        abort(400)
    except Exception as e:
        app.logger.error(f"Error handling webhook: {e}")
        abort(500)
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        user_message = event.message.text
        reply_token = event.reply_token

        response_text = ""

        if user_message == "相談開始":
            # AI機能が有効か無効かでメッセージを分岐
            if chat:
                response_text = "いつも利用者様支援に一生懸命取り組んでいただき、ありがとうございます。\n何でもご相談ください。"
            else:
                response_text = "いつも利用者様支援に一生懸命取り組んでいただき、ありがとうございます。\n現在、AI機能を停止してデバッグ中です。\nお困りでしたら、メッセージを送信してください。受信確認としてメッセージをオウム返しします。"
        else:
            if chat:
                try:
                    # Gemini APIで応答を生成
                    print(f"Sending message to Gemini: {user_message}")
                    gemini_response = chat.send_message(user_message)
                    response_text = gemini_response.text
                    print(f"Received response from Gemini: {response_text}")
                except Exception as e:
                    print(f"Error calling Gemini API: {e}")
                    response_text = f"『{user_message}』ですね。\n申し訳ありません、現在AIの応答に問題が発生しています。しばらくお待ちください。"
            else:
                # chat オブジェクトが None の場合はAI無効
                response_text = f"『{user_message}』ですね。\nメッセージを受け取りました。現在AI機能を停止してデバッグ中です。"
        
        try:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=response_text)]
                )
            )
            print(f"Replied to LINE with: {response_text}")
        except Exception as e:
            print(f"Error replying to LINE: {e}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
