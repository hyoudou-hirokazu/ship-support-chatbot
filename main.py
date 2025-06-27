import os
import logging
from flask import Flask, request, abort
from dotenv import load_dotenv
import datetime
# import time # 応答性向上のため、強制的な遅延処理は削除
import random

# LINE Bot SDK v3 のインポート
from linebot.v3.webhook import WebhookHandler
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest
from linebot.v3.messaging import TextMessage as LineReplyTextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError

# 署名検証のためのライブラリをインポート (LINE Bot SDKが内部で処理するため通常は不要だが、デバッグ用として残す)
import hmac
import hashlib
import base64

# Google Generative AI SDK のインポート
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# ロギング設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)

# .envファイルから環境変数を読み込む（Renderでは不要だが、ローカル実行時のために残しておく）
load_dotenv()

# 環境変数からLINEとGeminiのAPIキーを取得
CHANNEL_ACCESS_TOKEN = os.getenv('CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.getenv('CHANNEL_SECRET')
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
# PORT環境変数がない場合のエラーチェック。Gunicornがこれを必要とするため。
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
    # ユーザー指定のモデル名 'gemini-2.5-flash-lite-preview-06-17' を使用
    gemini_model = genai.GenerativeModel(
        'gemini-2.5-flash-lite-preview-06-17',
        safety_settings={
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
    )
    logging.info("Gemini API configured successfully using 'gemini-2.5-flash-lite-preview-06-17' model.")
except Exception as e:
    logging.critical(f"Failed to configure Gemini API: {e}. Please check GEMINI_API_KEY and 'google-generativeai' library version in requirements.txt. Also ensure 'gemini-2.5-flash-lite-preview-06-17' model is available for your API Key/Region.")
    raise Exception(f"Gemini API configuration failed: {e}")

# --- チャットボット関連の設定 ---
MAX_GEMINI_REQUESTS_PER_DAY = 20    # 1ユーザーあたり1日20回まで (無料枠考慮)

# プロンプトを社会福祉法人SHIPの支援者向けサポートAIに調整
SHIP_SUPPORT_SYSTEM_PROMPT = """
あなたは社会福祉法人SHIPの障害者福祉事業所（就労移行支援、就労定着支援、B型作業所、放課後等デイサービス、グループホーム（障害区分中～軽度、重度）の支援者向けサポートAIです。
支援者からの質問に対し、正確かつ丁寧に必要な情報を提供してください。

以下の情報を基に回答を生成してください。
* **各事業所の情報**:
    * 就労移行支援: 就職を目指す障害のある方への訓練やサポートを提供します。
    * 就労定着支援: 就職後、職場での定着をサポートします。
    * B型作業所: 働くことが困難な方へ、生産活動の機会提供と能力向上を支援します。
    * 放課後等デイサービス: 授業終了後や学校休業日に、障害のある児童の療育や居場所を提供します。
    * グループホーム（障害区分中～軽度、重度対応）: 地域で共同生活を送るための住居と日常生活上の支援を提供します。
* **提供する情報の種類**:
    * 各事業所のサービス内容、対象者、利用手続き、利用料金に関する一般的な説明。
    * よくある質問（例: 「利用開始までの流れは？」「体験利用は可能か？」「緊急時の対応は？」など）への回答。
    * 障害者福祉全般に関する基本的な専門知識や制度に関する情報。
    * 各種申請書類や手続きに関する案内（具体的な書式提供はできない旨を伝える）。
    * 法人内の各部署や事業所への一般的な連絡方法（具体的な担当者名はAIからは提示しない）。
* **AIの限界**:
    * あなたAIは、個別のケースに関する具体的な判断や、医療・法律に関する専門的なアドバイスは行いません。
    * 緊急を要する事柄や、詳細な個人情報に基づいた相談、専門的な判断が必要な場合は、**「この内容については、より詳細な情報が必要なため、各事業所の担当者または法人本部にお問い合わせください。」**と案内し、具体的な連絡先（例: 「法人本部 代表電話: XXXX-XX-XXXX」など、一般的な情報を伝えるか、担当部署への問い合わせを促す）を提示してください。ただし、AIが勝手に緊急連絡先を生成しないように注意し、事前に用意された情報がない場合は一般的な案内のみに留めてください。
* **回答のスタイル**:
    * 常に丁寧で、明確、かつ客観的な言葉遣いを心がけてください。
    * 簡潔に要点をまとめ、分かりやすく説明してください。
    * 専門用語を使用する場合は、簡単に補足説明を加えてください。
    * 返答は長すぎず、支援者がすぐに理解できる適切な長さに調整してください。
    * **各応答の最後に、支援者がさらに質問しやすくなるような、関連性のある問いかけや、次のアクションを促す言葉を必ず含めてください。** 例：「他に知りたい事業所の情報はありますか？」「この手続きについて、さらに詳しくお聞きになりたい点はございますか？」「障害福祉サービス全般に関して、何かご不明な点はありますか？」など。

**Gemini APIの無料枠を考慮し、無駄なトークン消費を避けるため、簡潔かつ的確な応答を心がけてください。また、同じような質問の繰り返しは避け、会話の進展を促してください。**
"""

# 初期メッセージ
INITIAL_MESSAGE = "社会福祉法人SHIPの支援者向けサポートAIです。\n障害者福祉事業に関するご質問や、お困りごとがございましたら、どんなことでもお気軽にお尋ねください。私が情報提供やサポートをさせていただきます。"

# Gemini API利用制限時のメッセージ
GEMINI_LIMIT_MESSAGE = (
    "申し訳ありません、本日のAIサポートのご利用回数の上限に達しました。\n"
    "明日またお話できますので、その時までお待ちください。\n\n"
    "もし緊急を要するご質問や、詳細な情報が必要な場合は、各事業所の担当者または法人本部にお問い合わせください。"
)

# 過去の会話履歴をGeminiに渡す最大ターン数
MAX_CONTEXT_TURNS = 6 # (ユーザーの発言 + AIの返答) の合計ターン数、トークン消費と相談して調整

# ユーザーごとのセッション情報を保持する辞書
# !!! 重要: 本番環境では、この方法は推奨されません。
# Flaskアプリケーションは、再起動（デプロイ、エラー、Renderのスピンダウンなど）のたびにメモリがリセットされ、
# user_sessions のデータが失われます。
# 会話履歴の永続化には、RenderのPostgreSQL, Redis, Google Cloud Firestore, AWS DynamoDBなどの
# 永続的なデータストアを利用することを強く推奨します。
user_sessions = {}

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)

    if not signature:
        app.logger.error("X-Line-Signature header is missing.")
        abort(400) # 署名がない場合は不正なリクエストとして処理

    app.logger.info("Received Webhook Request:")
    app.logger.info("  Request body (truncated to 500 chars): " + body[:500])
    app.logger.info(f"  X-Line-Signature: {signature}")

    # --- 署名検証のデバッグログ ---
    # ユーザーが提供したコードを保持し、デバッグの助けとなるように残す
    try:
        secret_bytes = CHANNEL_SECRET.encode('utf-8')
        body_bytes = body.encode('utf-8')
        hash_value = hmac.new(secret_bytes, body_bytes, hashlib.sha256).digest()
        calculated_signature = base64.b64encode(hash_value).decode('utf-8')

        app.logger.info(f"  Calculated signature (manual): {calculated_signature}")
        app.logger.info(f"  Channel Secret used for manual calc (first 5 chars): {CHANNEL_SECRET[:5]}...")

        if calculated_signature != signature:
            app.logger.error("!!! Manual Signature MISMATCH detected !!!")
            app.logger.error(f"    Calculated: {calculated_signature}")
            app.logger.error(f"    Received:    {signature}")
            # 手動計算で不一致が検出された場合は、SDK処理に入る前に終了
            abort(400)
        else:
            app.logger.info("  Manual signature check: Signatures match! Proceeding to SDK handler.")

    except Exception as e:
        app.logger.error(f"Error during manual signature calculation for debug: {e}", exc_info=True)
        # 手動計算でエラーが発生しても、SDKの処理は試みる
        pass

    # --- LINE Bot SDKによる署名検証とイベント処理 ---
    try:
        handler.handle(body, signature)
        app.logger.info("Webhook handled successfully by SDK.")
    except InvalidSignatureError:
        app.logger.error("!!! SDK detected Invalid signature !!!")
        app.logger.error("  This typically means CHANNEL_SECRET in Render does not match LINE Developers.")
        app.logger.error(f"  Body (truncated for error log): {body[:200]}...")
        app.logger.error(f"  Signature sent to SDK: {signature}")
        app.logger.error(f"  Channel Secret configured for SDK (first 5 chars): {CHANNEL_SECRET[:5]}...")
        abort(400) # 署名エラーの場合は400を返す
    except Exception as e:
        # その他の予期せぬエラー
        logging.critical(f"Unhandled error during webhook processing by SDK: {e}", exc_info=True)
        abort(500)

    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id # ユーザーIDを取得
    user_message = event.message.text
    app.logger.info(f"Received text message from user_id: '{user_id}', message: '{user_message}' (Reply Token: {event.reply_token})")

    response_text = "申し訳ありません、現在メッセージを処理できません。しばらくしてからもう一度お試しください。"

    # ユーザーセッションの初期化または取得
    current_date = datetime.date.today()

    # 新規ユーザーまたはセッションリセットのロジック
    # (注意: user_sessionsはサーバーの再起動でリセットされます)
    if user_id not in user_sessions or user_sessions[user_id]['last_request_date'] != current_date:
        # 日付が変わった場合、または新規ユーザーの場合、セッションをリセット
        user_sessions[user_id] = {
            'history': [], # 会話履歴は空で開始
            'request_count': 0,
            'last_request_date': current_date
        }
        app.logger.info(f"Initialized/Reset session for user_id: {user_id}. First message of the day or new user.")

        # 初回メッセージを送信し、このリクエストの処理を終了
        response_text = INITIAL_MESSAGE
        try:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[LineReplyTextMessage(text=response_text)]
                )
            )
            app.logger.info(f"Sent initial message/daily reset message to user {user_id}.")
        except Exception as e:
            logging.error(f"Error sending initial/reset reply to LINE for user {user_id}: {e}", exc_info=True)
        return 'OK' # 初回メッセージ送信後はここで処理を終了。この返信はGeminiを呼び出さない。

    # Gemini API利用回数制限のチェック
    if user_sessions[user_id]['request_count'] >= MAX_GEMINI_REQUESTS_PER_DAY:
        response_text = GEMINI_LIMIT_MESSAGE
        app.logger.warning(f"User {user_id} exceeded daily Gemini request limit ({MAX_GEMINI_REQUESTS_PER_DAY}).")
        try:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[LineReplyTextMessage(text=response_text)]
                )
            )
            app.logger.info(f"Sent limit message to LINE for user {user_id}.")
        except Exception as e:
            logging.error(f"Error sending limit reply to LINE for user {user_id}: {e}", exc_info=True)
        return 'OK'

    # 会話履歴を準備
    # システムプロンプトと初期応答を履歴の最初に含める
    chat_history_for_gemini = [
        {'role': 'user', 'parts': [{'text': SHIP_SUPPORT_SYSTEM_PROMPT}]},
        {'role': 'model', 'parts': [{'text': "はい、承知いたしました。社会福祉法人SHIPの支援者向けサポートAIとして、ご質問にお答えします。"}]}
    ]

    # MAX_CONTEXT_TURNS に基づいて過去の会話を結合
    # 各ターンはユーザーとモデルのペアなので、履歴から取得する要素数は MAX_CONTEXT_TURNS * 2
    start_index = max(0, len(user_sessions[user_id]['history']) - MAX_CONTEXT_TURNS * 2)

    app.logger.debug(f"Current history length for user {user_id}: {len(user_sessions[user_id]['history'])}. Taking from index {start_index}.")

    # 過去の会話履歴を追加
    for role, text_content in user_sessions[user_id]['history'][start_index:]:
        chat_history_for_gemini.append({'role': role, 'parts': [{'text': text_content}]})

    app.logger.debug(f"Gemini chat history prepared for user {user_id} (last message: '{user_message}'): {chat_history_for_gemini}")

    try:
        # Geminiとのチャットセッションを開始
        # historyにこれまでの会話履歴（システムプロンプト含む）を渡し、
        # 最新のユーザーメッセージのみをsend_messageで送る
        convo = gemini_model.start_chat(history=chat_history_for_gemini)
        gemini_response = convo.send_message(user_message)

        if gemini_response and hasattr(gemini_response, 'text'):
            response_text = gemini_response.text
        elif isinstance(gemini_response, list) and gemini_response and hasattr(gemini_response[0], 'text'):
            response_text = gemini_response[0].text
        else:
            logging.warning(f"Unexpected Gemini response format or no text content: {gemini_response}")
            response_text = "Geminiからの応答形式が予期せぬものでした。"

        app.logger.info(f"Gemini generated response for user {user_id}: '{response_text}'")

        # 会話履歴を更新 (user_sessionsに保存)
        user_sessions[user_id]['history'].append(['user', user_message])
        user_sessions[user_id]['history'].append(['model', response_text])

        # リクエスト数をインクリメント
        user_sessions[user_id]['request_count'] += 1
        user_sessions[user_id]['last_request_date'] = current_date # リクエスト日を更新
        app.logger.info(f"User {user_id} - Request count: {user_sessions[user_id]['request_count']}")

    except Exception as e:
        logging.error(f"Error interacting with Gemini API for user {user_id}: {e}", exc_info=True)
        response_text = "Geminiとの通信中にエラーが発生しました。時間を置いてお試しください。"

    finally:
        try:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[LineReplyTextMessage(text=response_text)]
                )
            )
            app.logger.info(f"Reply sent to LINE successfully for user {user_id}.")
        except Exception as e:
            logging.error(f"Error replying to LINE for user {user_id}: {e}", exc_info=True)

    return 'OK'

if __name__ == "__main__":
    # Render環境ではPORT環境変数が設定されるため、それを使用する
    # ローカル実行時にはデフォルトで8080を使用
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
