import os
import logging
from flask import Flask, request, abort
import datetime
import time
import threading

# LINE Bot SDK v3 のインポート
from linebot.v3.webhook import WebhookHandler
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest
# from linebot.v3.messaging.models import GetProfileRequest # GetProfileRequestを除外
from linebot.v3.messaging import TextMessage as LineReplyTextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.exceptions import InvalidSignatureError, LineBotApiError

# Google Generative AI SDK のインポート
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold # ここを修正: HarmBlockBlockThreshold -> HarmBlockThreshold

# ロギング設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)

# 環境変数からLINEとGeminiのAPIキーを取得
CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# 環境変数が設定されているか確認
if not CHANNEL_ACCESS_TOKEN:
    logging.critical("CHANNEL_ACCESS_TOKEN is not set in environment variables.")
    raise ValueError("CHANNEL_ACCESS_TOKEN is not set. Please set it in Render Environment Variables.")
if not CHANNEL_SECRET:
    logging.critical("CHANNEL_SECRET is not set in environment variables.")
    raise ValueError("CHANNEL_SECRET is not set. Please set it in Render Environment Variables.")
if not GEMINI_API_KEY:
    logging.critical("GEMINI_API_KEY is not set in environment variables.")
    raise ValueError("GEMINI_API_KEY is not set. Please set it in Render Environment Variables.")
if not os.getenv('PORT'):
    logging.critical("PORT environment variable is not set by Render. This is unexpected for a Web Service.")
    raise ValueError("PORT environment variable is not set. Ensure this is deployed on a platform like Render.")


# LINE Messaging API v3 の設定
try:
    configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
    line_bot_api = MessagingApi(ApiClient(configuration))
    handler = WebhookHandler(CHANNEL_SECRET)
    logging.info("LINE Bot SDK configured successfully.")
except Exception as e:
    logging.critical(f"Failed to configure LINE Bot SDK: {e}. Please check CHANNEL_ACCESS_TOKEN and CHANNEL_SECRET.")
    raise Exception(f"LINE Bot SDK configuration failed: {e}")

# Gemini API の設定
try:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel(
        'gemini-2.5-flash-lite-preview-06-17',
        safety_settings={
            HarmCategory.HARMS_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARMS_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARMS_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARMS_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE, # ここも修正
        }
    )
    logging.info("Gemini API configured successfully using 'gemini-2.5-flash-lite-preview-06-17' model.")
except Exception as e:
    logging.critical(f"Failed to configure Gemini API: {e}. Please check GEMINI_API_KEY and 'google-generativeai' library version in requirements.txt. Also ensure 'gemini-2.5-flash-lite-preview-06-17' model is available for your API Key/Region.")
    raise Exception(f"Gemini API configuration failed: {e}")

# --- チャットボット関連の設定 ---
MAX_GEMINI_REQUESTS_PER_DAY = 20

# プロンプトを社会福祉法人SHIPの支援者向けサポートAIに調整
SHIP_SUPPORT_SYSTEM_PROMPT = """
あなたは障害福祉分野の専門相談員「支援メイトBot」です。
支援者が適切な判断と対応できるよう、心理的・制度的・現場実践的なアドバイスを簡潔に提供してください。

支援者の質問は以下の情報を含む可能性があります。提供された範囲で助言し、不足情報があれば質問を促してください。
【質問項目】
* **【事業所種別】**:{支援領域}。例: 就労移行支援、B型作業所など。
* **【障害種別】:{障害名}（特性）。例: 発達障害(ASD), 統合失調症など。
* **【利用者の状態】**:{状態・フェーズ}。例: 不安定, 職場トラブルなど。
* **【支援者の悩み・相談内容】**:{フリーテキスト}。例: 報連相が苦手など。

【回答条件】
* 不完全な情報でも仮アドバイスを提供。
* 不足情報があれば、ユーザーが答えやすいよう具体的に質問を促す。
* 支援者の心理に配慮し、寄り添うトーン。
* 具体的な対応策を2〜3案、簡潔に提示。
* 必要に応じて関連制度、研修、専門資格を紹介。
* 専門用語は避け、分かりやすい言葉。
* 返答は簡潔で適切な長さ。
* 各応答の最後に、次の質問やアクションを促す言葉を必ず含める。
* 個別判断、医療・法律アドバイスは行わない。緊急時や詳細な情報が必要な場合は、「この内容については、より詳細な情報が必要なため、各事業所の担当者または法人本部にお問い合わせください。」と案内し、担当部署への問い合わせを促す。
* 応答は簡潔に、トークン消費を抑え、会話の発展を促すこと。
"""

# ユーザー名を考慮しない汎用的な初期メッセージに変更
INITIAL_MESSAGE_SUPPORT_BOT = (
    "いつも利用者様支援に一生懸命取り組んでいただき、ありがとうございます。\n"
    "日々の業務や利用者支援でお困りでしたら、気軽にご相談ください。「支援メイトBot」が専門相談員としてサポートさせていただきます。\n\n"
    "より的確なアドバイスのため、例えば「事業所種別」や「障害の特性（例：統合失調症、知的障害3度、精神障害2級など）」など、分かる範囲でお知らせいただけますか？"
)

# Gemini API利用制限時のメッセージ
GEMINI_LIMIT_MESSAGE = (
    "申し訳ありません、本日のAIサポートのご利用回数の上限に達しました。\n"
    "明日またお話できますので、その時までお待ちください。\n\n"
    "もし緊急を要するご質問や、詳細な情報が必要な場合は、各事業所の担当者または法人本部にお問い合わせください。"
)

MAX_CONTEXT_TURNS = 6

user_sessions = {}

# LINEへの返信を非同期で行う関数
def deferred_reply(reply_token, messages_to_send, user_id, start_time):
    try:
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=messages_to_send
            )
        )
        app.logger.info(f"[{time.time() - start_time:.3f}s] Deferred reply sent to LINE successfully for user {user_id}.")
    except Exception as e:
        app.logger.error(f"Error sending deferred reply to LINE for user {user_id}: {e}", exc_info=True)

@app.route("/callback", methods=['POST'])
def callback():
    start_callback_time = time.time()
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)

    if not signature:
        app.logger.error(f"[{time.time() - start_callback_time:.3f}s] X-Line-Signature header is missing.")
        abort(400)

    app.logger.info(f"[{time.time() - start_callback_time:.3f}s] Received Webhook Request.")
    app.logger.info("  Request body (truncated to 500 chars): " + body[:500])
    app.logger.info(f"  X-Line-Signature: {signature}")

    try:
        handler.handle(body, signature)
        app.logger.info(f"[{time.time() - start_callback_time:.3f}s] Webhook handled successfully by SDK.")
    except InvalidSignatureError:
        app.logger.error(f"[{time.time() - start_callback_time:.3f}s] !!! SDK detected Invalid signature !!!")
        app.logger.error("  This typically means CHANNEL_SECRET in Render does not match LINE Developers.")
        abort(400)
    except Exception as e:
        logging.critical(f"[{time.time() - start_callback_time:.3f}s] Unhandled error during webhook processing by SDK: {e}", exc_info=True)
        abort(500)

    app.logger.info(f"[{time.time() - start_callback_time:.3f}s] Total callback processing time.")
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    start_handle_time = time.time()
    user_id = event.source.user_id
    user_message = event.message.text
    reply_token = event.reply_token
    app.logger.info(f"[{time.time() - start_handle_time:.3f}s] handle_message received for user_id: '{user_id}', message: '{user_message}' (Reply Token: {reply_token})")

    current_date = datetime.date.today()

    def process_and_reply_async():
        messages_to_send = []
        response_text = "申し訳ありません、現在メッセージを処理できません。しばらくしてからもう一度お試しください。"

        if user_id not in user_sessions or user_sessions[user_id]['last_request_date'] != current_date:
            app.logger.info(f"[{time.time() - start_handle_time:.3f}s] Initializing/Resetting session for user_id: {user_id}. First message of the day or new user.")
            user_sessions[user_id] = {
                'history': [],
                'request_count': 0,
                'last_request_date': current_date,
                'display_name': "職員" # GetProfileRequestを使用しないため、汎用名を設定
            }
            response_text = INITIAL_MESSAGE_SUPPORT_BOT
            messages_to_send.append(LineReplyTextMessage(text=response_text))
            deferred_reply(reply_token, messages_to_send, user_id, start_handle_time)
            app.logger.info(f"[{time.time() - start_handle_time:.3f}s] handle_message finished for initial/reset flow (deferred reply).")
            return

        if user_sessions[user_id]['request_count'] >= MAX_GEMINI_REQUESTS_PER_DAY:
            response_text = GEMINI_LIMIT_MESSAGE
            app.logger.warning(f"User {user_id} exceeded daily Gemini request limit ({MAX_GEMINI_REQUESTS_PER_DAY}).")
            messages_to_send.append(LineReplyTextMessage(text=response_text))
            deferred_reply(reply_token, messages_to_send, user_id, start_handle_time)
            app.logger.info(f"[{time.time() - start_handle_time:.3f}s] handle_message finished for limit exceeded flow (deferred reply).")
            return

        chat_history_for_gemini = [
            {'role': 'user', 'parts': [{'text': SHIP_SUPPORT_SYSTEM_PROMPT}]},
            {'role': 'model', 'parts': [{'text': "はい、承知いたしました。支援メイトBotとして、ご質問にお答えします。"}]}
        ]

        start_index = max(0, len(user_sessions[user_id]['history']) - MAX_CONTEXT_TURNS * 2)
        app.logger.debug(f"[{time.time() - start_handle_time:.3f}s] Current history length for user {user_id}: {len(user_sessions[user_id]['history'])}. Taking from index {start_index}.")

        for role, text_content in user_sessions[user_id]['history'][start_index:]:
            chat_history_for_gemini.append({'role': role, 'parts': [{'text': text_content}]})

        app.logger.debug(f"[{time.time() - start_handle_time:.3f}s] Gemini chat history prepared for user {user_id} (last message: '{user_message}'): {chat_history_for_gemini}")

        try:
            start_gemini_call = time.time()
            convo = gemini_model.start_chat(history=chat_history_for_gemini)
            gemini_response = convo.send_message(user_message)
            end_gemini_call = time.time()
            app.logger.info(f"[{end_gemini_call - start_gemini_call:.3f}s] Gemini API call completed for user {user_id}.")

            if gemini_response and hasattr(gemini_response, 'text'):
                response_text = gemini_response.text
            elif isinstance(gemini_response, list) and gemini_response and hasattr(gemini_response[0], 'text'):
                response_text = gemini_response[0].text
            else:
                logging.warning(f"[{time.time() - start_handle_time:.3f}s] Unexpected Gemini response format or no text content: {gemini_response}")
                response_text = "Geminiからの応答形式が予期せぬものでした。"

            app.logger.info(f"[{time.time() - start_handle_time:.3f}s] Gemini generated response for user {user_id}: '{response_text}'")

            user_sessions[user_id]['history'].append(['user', user_message])
            user_sessions[user_id]['history'].append(['model', response_text])
            user_sessions[user_id]['request_count'] += 1
            user_sessions[user_id]['last_request_date'] = current_date
            app.logger.info(f"[{time.time() - start_handle_time:.3f}s] User {user_id} - Request count: {user_sessions[user_id]['request_count']}")

        except Exception as e:
            logging.error(f"[{time.time() - start_handle_time:.3f}s] Error interacting with Gemini API for user {user_id}: {e}", exc_info=True)
            response_text = "Geminiとの通信中にエラーが発生しました。時間を置いてお試しください。"

        finally:
            messages_to_send.append(LineReplyTextMessage(text=response_text))
            deferred_reply(reply_token, messages_to_send, user_id, start_handle_time)

        app.logger.info(f"[{time.time() - start_handle_time:.3f}s] Total process_and_reply_async processing time.")

    threading.Thread(target=process_and_reply_async).start()
    app.logger.info(f"[{time.time() - start_handle_time:.3f}s] handle_message immediately returned OK for user {user_id}.")
    return 'OK'

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
