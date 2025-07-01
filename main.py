import os
import sys
from dotenv import load_dotenv

from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError

# ★★★ 最重要確認ポイント: この行が完全に削除されていることを確認してください ★★★
# from linebot.v3.messaging.models import PushMessage 
# 上記の行がmain.pyファイルに存在しないことを再度、確認してください。

# 必要なモジュールのみをインポートします
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage

from linebot.v3.webhooks import MessageEvent, TextMessageContent

# ★★★ 修正: Gemini API関連のインポートを完全にコメントアウト ★★★
# import google.generativeai as genai
# from google.generativeai.types import HarmCategory, HarmBlockThreshold

# .env ファイルから環境変数をロード
load_dotenv()

app = Flask(__name__)

# 環境変数の設定
CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
# GEMINI_API_KEY はこの修正コードでは使用しませんが、環境変数としては保持しておきます。
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY') 

# 環境変数が設定されているか確認
if CHANNEL_SECRET is None:
    print('Specify LINE_CHANNEL_SECRET as environment variable.')
    sys.exit(1)
if CHANNEL_ACCESS_TOKEN is None:
    print('Specify LINE_CHANNEL_ACCESS_TOKEN as environment variable.')
    sys.exit(1)
# GEMINI_API_KEY はこのフェーズでは必須ではないため、チェックを一時的に緩和
# if GEMINI_API_KEY is None:
#     print('Specify GEMINI_API_KEY as environment variable.')
#     sys.exit(1)

handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

# ★★★ 修正: Gemini APIの初期化ブロックを完全にコメントアウトし、chat オブジェクトは使用しない ★★★
# chat = None を削除し、chat が使用される箇所もコメントアウトまたは削除します。
# try:
#     genai.configure(api_key=GEMINI_API_KEY)
#     
#     GEMINI_MODEL_NAME = 'gemini-2.5-flash-lite-preview-06-17' 
#     model_exists = False
#     for m in genai.list_models():
#         if GEMINI_MODEL_NAME == m.name:
#             model_exists = True
#             break
#     if not model_exists:
#         raise Exception(f"The specified Gemini model '{GEMINI_MODEL_NAME}' is not available for your API key/region.")
#     model = genai.GenerativeModel(
#         GEMINI_MODEL_NAME,
#         safety_settings={
#             HarmCategory.HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
#             HarmCategory.HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
#             HarmCategory.SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
#             HarmCategory.DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
#         }
#     )
#     chat = model.start_chat(history=[])
#     print(f"Gemini API configured successfully using '{GEMINI_MODEL_NAME}' model.")
# except Exception as e:
#     print(f"CRITICAL: Gemini API configuration failed: {e}. Please check GEMINI_API_KEY and google-generativeai library version in requirements.txt. Also ensure '{GEMINI_MODEL_NAME}' model is available for your API key/region.")
#     chat = None

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
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        user_message = event.message.text
        reply_token = event.reply_token

        # 「相談開始」メッセージに対する特別な応答
        if user_message == "相談開始":
            response_text = "いつも利用者様支援に一生懸命取り組んでいただき、ありがとうございます。\n現在、AI機能を停止してデバッグ中です。\nお困りでしたら、メッセージを送信してください。受信確認としてメッセージをオウム返しします。"
        else:
            # それ以外のメッセージはオウム返し
            response_text = f"『{user_message}』ですね。\nメッセージを受け取りました。現在AI機能を停止してデバッグ中です。"
        
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=response_text)]
            )
        )
        # Gemini API呼び出し関連のコードはすべて削除またはコメントアウトされているため、ここには存在しないはずです。

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
