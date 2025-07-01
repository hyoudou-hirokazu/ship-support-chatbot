import os
import logging
from flask import Flask, request, abort
import datetime
import time
import threading
from dotenv import load_dotenv # .env ファイルから環境変数をロードするため追加

# LINE Bot SDK v3 のインポート
from linebot.v3.webhook import WebhookHandler
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest
from linebot.v3.messaging import TextMessage as LineReplyTextMessage # LINEへの返信用テキストメッセージ
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.exceptions import InvalidSignatureError, LineBotApiError # LineBotApiErrorもインポート

# Google Generative AI SDK のインポート
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# .env ファイルから環境変数をロード（開発環境用）
load_dotenv()

# ロギング設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)

# 環境変数からLINEとGeminiのAPIキーを取得
CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
# Renderが設定するPORT環境変数
PORT = os.getenv('PORT', 8080)

# 環境変数が設定されているか確認
if not CHANNEL_ACCESS_TOKEN:
    logging.critical("LINE_CHANNEL_ACCESS_TOKEN is not set in environment variables.")
    raise ValueError("LINE_CHANNEL_ACCESS_TOKEN is not set. Please set it in Render Environment Variables.")
if not CHANNEL_SECRET:
    logging.critical("LINE_CHANNEL_SECRET is not set in environment variables.")
    raise ValueError("LINE_CHANNEL_SECRET is not set. Please set it in Render Environment Variables.")
# Gemini APIキーがない場合は、Gemini機能は無効になるが、アプリは起動させる
if not GEMINI_API_KEY:
    logging.warning("GEMINI_API_KEY is not set in environment variables. Gemini API functionality will be disabled.")
if not PORT:
    logging.critical("PORT environment variable is not set. Ensure this is deployed on a platform like Render.")
    raise ValueError("PORT environment variable is not set.")

# LINE Messaging API v3 の設定
try:
    configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
    # ApiClient は with ステートメント内で使用するため、ここではインスタンス化しない
    # line_bot_api = MessagingApi(ApiClient(configuration))
    handler = WebhookHandler(CHANNEL_SECRET)
    logging.info("LINE Bot SDK handler configured successfully.")
except Exception as e:
    logging.critical(f"Failed to configure LINE Bot SDK handler: {e}. Please check LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET.")
    raise Exception(f"LINE Bot SDK handler configuration failed: {e}")

# Gemini API の設定
gemini_model = None
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        # 以前のログで gemini-2.5-flash-lite-preview-06-17 が利用できないエラーが出ているため、
        # より安定している gemini-1.0-pro を初期値として設定
        # 動作確認後、必要であれば gemini-2.5-flash-lite-preview-06-17 に戻す
        GEMINI_MODEL_NAME = 'gemini-1.0-pro' 
        
        # モデルが利用可能か確認（起動時間削減のため、コメントアウトも検討）
        model_exists = False
        try:
            for m in genai.list_models():
                if GEMINI_MODEL_NAME == m.name:
                    model_exists = True
                    break
            if not model_exists:
                raise Exception(f"The specified Gemini model '{GEMINI_MODEL_NAME}' is not available for your API key/region. Please check model list and regional availability.")
        except Exception as e:
            raise Exception(f"Failed to list Gemini models. Check GEMINI_API_KEY or network connectivity. Original error: {e}")

        gemini_model = genai.GenerativeModel(
            GEMINI_MODEL_NAME,
            safety_settings={
                HarmCategory.HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            }
        )
        logging.info(f"Gemini API configured successfully using '{GEMINI_MODEL_NAME}' model.")
    except Exception as e:
        logging.critical(f"Failed to configure Gemini API: {e}. Please check GEMINI_API_KEY and 'google-generativeai' library version in requirements.txt. Also ensure '{GEMINI_MODEL_NAME}' model is available for your API Key/Region.")
        gemini_model = None # Gemini APIの設定失敗時はモデルをNoneにする

# --- チャットボット関連の設定 ---
MAX_GEMINI_REQUESTS_PER_DAY = 20

# 支援メイトbot システムプロンプト（プロンプトの内容は変更していません）
SHIEN_MATE_SYSTEM_PROMPT = """
あなたは、利用者様支援に携わる方々のための専門相談AI「支援メイトBot」です。
以下の5つの心理療法の要素を統合し、日々の業務や利用者支援でお困りの方々をサポートします。

1.  **来談者中心療法 (Client-Centered Therapy) の要素:**
    * 無条件の肯定的配慮、共感的理解、自己一致（純粋性）を重視し、相談者の話を傾聴し、その感情を深く理解しようと努めます。
    * 相談者自身が解決策を見出す力を信じ、自己成長を促します。
2.  **解決志向ブリーフセラピー (Solution-Focused Brief Therapy) の要素:**
    * 問題そのものよりも、相談者の「なりたい状態」や「解決」に焦点を当てます。
    * 「うまくいっていること」「できたこと」に注目し、相談者の強みやリソースを引き出し、具体的な行動目標の設定をサポートします。
    * ミラクルクエスチョンやスケーリングクエスチョンを用いて、未来志向の対話を促します。
3.  **認知行動療法 (Cognitive Behavioral Therapy - CBT) の要素:**
    * 相談者自身の思考パターン（認知）や行動が感情に与える影響について、客観的に気づきを促します。
    * 非合理的な思考や望ましくない行動パターンを特定し、より建設的な思考や行動に転換できるよう、具体的な練習や振り返りを促す示唆を与えます。
4.  **アクセプタンス＆コミットメント・セラピー (Acceptance and Commitment Therapy - ACT) の要素:**
    * 不快な感情や思考を無理に排除しようとするのではなく、「あるがままに受け入れる（アクセプタンス）」ことを促します。
    * 自分の「本当に大切にしたいこと（価値）」を明確にし、それに沿った行動（コミットメント）を促すことに焦点を当てます。
    * 「思考と距離を置く（脱フュージョン）」などの概念を取り入れ、心の柔軟性を高めるヒントを提供します。
5.  **ポジティブ心理学 (Positive Psychology) の要素:**
    * 問題解決だけでなく、幸福感、強み、レジリエンス（精神的回復力）、ウェルビーイングといった人間のポジティブな側面に焦点を当てます。
    * 感謝、楽観主義、希望、マインドフルネスの実践などを促し、相談者の強みを認識し、活用することで、より充実した支援業務を送るサポートをします。

**重要な注意点:**
* **医療行為、精神科医による診断、専門的なカウンセリング、具体的な治療法や薬剤の提案は一切行いません。**
* あくまで情報提供と、相談者自身の内省を促す対話を目的とします。
* 必要に応じて、信頼できる心理カウンセリング機関や専門家、公的相談窓口への相談を促してください。

**応答の原則:**
* 傾聴と共感を持ち、温かく、安心感を与えるトーンで応答してください。
* 具体的な解決策の提示よりも、相談者が自身の感情や思考に気づき、主体的に行動できるようなオープンな質問を重視してください。
* 応答は、簡潔で分かりやすい言葉で、親しみやすい表現を心がけてください。
* 回答の最後に、相談者の心の健康をサポートするような励ましの言葉や、次の質問、あるいはリラックスできるような言葉を必ず含めてください。
* 応答は簡潔に、トークン消費を抑え、会話の発展を促すこと。
"""

# ユーザー名を考慮しない汎用的な初期メッセージ
INITIAL_MESSAGE_SHIEN_MATE = (
    "「支援メイトBot」へようこそ。\n"
    "いつも利用者様支援に一生懸命取り組んでいただき、ありがとうございます。\n"
    "日々の業務や利用者支援でお困りでしたら、お気軽にご相談ください。\n\n"
    "より具体的なアドバイスのため、例えば「事業所種別」や「障害の特性（例：統合失調症、知的障害３度、精神障害２級など）」など、分かる範囲でお知らせいただけますか？"
)

# Gemini API利用制限時のメッセージ
GEMINI_LIMIT_MESSAGE = (
    "申し訳ありません、本日の「支援メイトBot」のご利用回数の上限に達しました。\n"
    "ご自身の業務改善のために、積極的にご活用いただきありがとうございます。\n"
    "明日またお話しできますので、それまでは、ご自身の心と体をゆっくり休める時間を作ってくださいね。\n\n"
    "もし緊急を要するご相談や、専門的なサポートが必要だと感じられた場合は、地域の専門機関や、公的な相談窓口へご連絡ください。"
    "皆様の業務が穏やかでありますように。"
)

MAX_CONTEXT_TURNS = 6 # 保持する会話ターン数

# ユーザーセッション情報を保持するための辞書
# グローバル変数として定義
user_sessions = {}

# LINEへの返信を非同期で行う関数
def deferred_reply(reply_token, messages_to_send, user_id, start_time):
    # ApiClient はスレッドセーフではないため、DeferredReply 内でインスタンスを作成
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api_local = MessagingApi(api_client)
            line_bot_api_local.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=messages_to_send
                )
            )
            logging.info(f"[{time.time() - start_time:.3f}s] Deferred reply sent to LINE successfully for user {user_id}.")
    except LineBotApiError as e:
        logging.error(f"Error sending deferred reply to LINE (LineBotApiError) for user {user_id}: {e}", exc_info=True)
    except Exception as e:
        logging.error(f"Error sending deferred reply to LINE (General Exception) for user {user_id}: {e}", exc_info=True)

@app.route("/callback", methods=['POST'])
def callback():
    start_callback_time = time.time()
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)

    if not signature:
        logging.error(f"[{time.time() - start_callback_time:.3f}s] X-Line-Signature header is missing.")
        abort(400)

    logging.info(f"[{time.time() - start_callback_time:.3f}s] Received Webhook Request.")
    logging.info("  Request body (truncated to 500 chars): " + body[:500])
    logging.info(f"  X-Line-Signature: {signature}")

    try:
        handler.handle(body, signature)
        logging.info(f"[{time.time() - start_callback_time:.3f}s] Webhook handled successfully by SDK.")
    except InvalidSignatureError:
        logging.error(f"[{time.time() - start_callback_time:.3f}s] !!! SDK detected Invalid signature !!!")
        logging.error("  This typically means CHANNEL_SECRET in Render does not match LINE Developers.")
        abort(400)
    except Exception as e:
        logging.critical(f"[{time.time() - start_callback_time:.3f}s] Unhandled error during webhook processing by SDK: {e}", exc_info=True)
        abort(500)

    logging.info(f"[{time.time() - start_callback_time:.3f}s] Total callback processing time.")
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    start_handle_time = time.time()
    user_id = event.source.user_id
    user_message = event.message.text
    reply_token = event.reply_token
    logging.info(f"[{time.time() - start_handle_time:.3f}s] handle_message received for user_id: '{user_id}', message: '{user_message}' (Reply Token: {reply_token})")

    current_date = datetime.date.today()

    def process_and_reply_async():
        messages_to_send = []
        response_text = "申し訳ありません、現在メッセージを処理できません。しばらくしてからもう一度お試しください。"

        # セッションの初期化またはリセット
        # 「相談開始」が来た場合はセッションをリセットし、初期メッセージを返す
        if user_id not in user_sessions or user_sessions[user_id]['last_request_date'] != current_date or user_message == "相談開始":
            logging.info(f"[{time.time() - start_handle_time:.3f}s] Initializing/Resetting session for user_id: {user_id}. First message of the day or '相談開始'.")
            user_sessions[user_id] = {
                'history': [],
                'request_count': 0,
                'last_request_date': current_date,
                'display_name': "相談者様" # 汎用名を設定
            }
            response_text = INITIAL_MESSAGE_SHIEN_MATE
            messages_to_send.append(LineReplyTextMessage(text="※その日の最初のメッセージでは、起動のため数分、返答遅延が生じる場合があります。"))
            messages_to_send.append(LineReplyTextMessage(text=response_text))
            deferred_reply(reply_token, messages_to_send, user_id, start_handle_time)
            logging.info(f"[{time.time() - start_handle_time:.3f}s] handle_message finished for initial/reset flow (deferred reply).")
            return

        # Gemini APIの利用回数制限チェック
        if user_sessions[user_id]['request_count'] >= MAX_GEMINI_REQUESTS_PER_DAY:
            response_text = GEMINI_LIMIT_MESSAGE
            logging.warning(f"User {user_id} exceeded daily Gemini request limit ({MAX_GEMINI_REQUESTS_PER_DAY}).")
            messages_to_send.append(LineReplyTextMessage(text=response_text))
            deferred_reply(reply_token, messages_to_send, user_id, start_handle_time)
            logging.info(f"[{time.time() - start_handle_time:.3f}s] handle_message finished for limit exceeded flow (deferred reply).")
            return

        # Gemini APIが利用可能かチェック
        if not gemini_model:
            response_text = "申し訳ありません、現在AIの機能が利用できません。システム設定を確認中です。しばらくお待ちください。"
            logging.error(f"[{time.time() - start_handle_time:.3f}s] Gemini model is not initialized for user {user_id}.")
            messages_to_send.append(LineReplyTextMessage(text=response_text))
            deferred_reply(reply_token, messages_to_send, user_id, start_handle_time)
            return

        # Geminiへの会話履歴を準備
        # プロンプトは初回メッセージでのみ追加し、以降は会話履歴のみ追加
        chat_history_for_gemini = [
            {'role': 'user', 'parts': [{'text': SHIEN_MATE_SYSTEM_PROMPT}]},
            {'role': 'model', 'parts': [{'text': "はい、承知いたしました。支援メイトBotとして、皆様の業務や利用者支援をサポートさせていただきます。"}]}
        ]

        # 過去の会話履歴をMAX_CONTEXT_TURNS分追加
        # Geminiはロールの厳密な順序を要求するため、必ずuser -> model のペアを保つ
        start_index = max(0, len(user_sessions[user_id]['history']) - MAX_CONTEXT_TURNS * 2)
        logging.debug(f"[{time.time() - start_handle_time:.3f}s] Current history length for user {user_id}: {len(user_sessions[user_id]['history'])}. Taking from index {start_index}.")

        for i in range(start_index, len(user_sessions[user_id]['history'])):
            role, text_content = user_sessions[user_id]['history'][i]
            chat_history_for_gemini.append({'role': role, 'parts': [{'text': text_content}]})

        logging.debug(f"[{time.time() - start_handle_time:.3f}s] Gemini chat history prepared for user {user_id} (last message: '{user_message}'): {chat_history_for_gemini}")

        try:
            start_gemini_call = time.time()
            convo = gemini_model.start_chat(history=chat_history_for_gemini)
            gemini_response = convo.send_message(user_message)
            end_gemini_call = time.time()
            logging.info(f"[{end_gemini_call - start_gemini_call:.3f}s] Gemini API call completed for user {user_id}.")

            if gemini_response and hasattr(gemini_response, 'text'):
                response_text = gemini_response.text
            else:
                logging.warning(f"[{time.time() - start_handle_time:.3f}s] Unexpected Gemini response format or no text content: {gemini_response}")
                response_text = "Geminiからの応答形式が予期せぬものでした。申し訳ありません。"

            logging.info(f"[{time.time() - start_handle_time:.3f}s] Gemini generated response for user {user_id}: '{response_text}'")

            # 会話履歴を更新
            user_sessions[user_id]['history'].append(['user', user_message])
            user_sessions[user_id]['history'].append(['model', response_text])
            # リクエスト回数をインクリメント
            user_sessions[user_id]['request_count'] += 1
            user_sessions[user_id]['last_request_date'] = current_date
            logging.info(f"[{time.time() - start_handle_time:.3f}s] User {user_id} - Request count: {user_sessions[user_id]['request_count']}")

        except Exception as e:
            logging.error(f"[{time.time() - start_handle_time:.3f}s] Error interacting with Gemini API for user {user_id}: {e}", exc_info=True)
            response_text = "申し訳ありません、現在AIの応答に問題が発生しています。時間を置いてお試しください。"

        finally:
            messages_to_send.append(LineReplyTextMessage(text=response_text))
            deferred_reply(reply_token, messages_to_send, user_id, start_handle_time)
            logging.info(f"[{time.time() - start_handle_time:.3f}s] Total process_and_reply_async processing time.")

    # 返信処理を別スレッドで実行し、LINEのタイムアウトを防ぐ
    threading.Thread(target=process_and_reply_async).start()
    logging.info(f"[{time.time() - start_handle_time:.3f}s] handle_message immediately returned OK for user {user_id}.")
    return 'OK'

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(PORT))
