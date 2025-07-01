import os
import sys
from dotenv import load_dotenv

from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
# ここをさらに修正: PushMessage は linebot.v3.messaging.models.message モジュールからインポートします
from linebot.v3.messaging.models.message import PushMessage # このように修正

from linebot.v3.webhooks import MessageEvent, TextMessageContent

import google.generativeai as genai
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
if GEMINI_API_KEY is None:
    print('Specify GEMINI_API_KEY as environment variable.')
    sys.exit(1)

handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

# Gemini APIの初期化
try:
    genai.configure(api_key=GEMINI_API_KEY)
    list_models_response = genai.list_models()
    model_exists = False
    for m in list_models_response:
        if "gemini-2.5-flash-lite-preview-06-17" == m.name:
            model_exists = True
            break
    if not model_exists:
        raise Exception("The specified Gemini model 'gemini-2.5-flash-lite-preview-06-17' is not available.")

    model = genai.GenerativeModel(
        'gemini-2.5-flash-lite-preview-06-17',
        safety_settings={
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
    )
    chat = model.start_chat(history=[])
    print("Gemini API configured successfully using 'gemini-2.5-flash-lite-preview-06-17' model.")
except Exception as e:
    print(f"Exception: Gemini API configuration failed: {e}")
    chat = None

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
        user_id = event.source.user_id

        if user_message == "相談開始":
            first_message = "いつも利用者様支援に一生懸命取り組んでいただき、ありがとうございます。\n日々の業務や利用者支援でお困りでしたら、お気軽にご相談ください。\n「支援メイトBot」が専門相談員としてサポートさせていただきます。"
            first_message += "\nより具体的なアドバイスのため、例えば「事業所種別」や「障害の特性（例：統合失調症、知的障害３度、精神障害２級など）」など、分かる範囲でお知らせいただけますか？"

            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[
                        TextMessage(text="※その日の最初のメッセージでは、起動のため数分、返答遅延が生じる場合があります。"),
                        TextMessage(text=first_message)
                    ]
                )
            )
            return

        if chat is None:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[
                        TextMessage(text="現在、システムに問題が発生しており、AIによる応答ができません。しばらくお待ちください。")
                    ]
                )
            )
            return

        try:
            response = chat.send_message(user_message)
            gemini_response_text = response.text

            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=gemini_response_text)]
                )
            )
        except Exception as e:
            app.logger.error(f"Error communicating with Gemini API: {e}")
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text="現在、AIが応答できません。もう一度お試しいただくか、しばらくお待ちください。")]
                )
            )

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
