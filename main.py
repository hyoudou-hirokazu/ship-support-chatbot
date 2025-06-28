import os
import logging
from flask import Flask, request, abort
# from dotenv import load_dotenv # Renderでは環境変数が自動的に設定されるため、この行はコメントアウト
import datetime
import time # 時間計測のために再追加
import random

# LINE Bot SDK v3 のインポート
from linebot.v3.webhook import WebhookHandler
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest
# !!! 修正: GetProfileRequest のインポートパスを linebot.v3.messaging.models に変更 !!!
from linebot.v3.messaging.models import GetProfileRequest # GetProfileRequest は models サブモジュールにあります
from linebot.v3.messaging import TextMessage as LineReplyTextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.exceptions import InvalidSignatureError, LineBotApiError # LineBotApiErrorのパスは既に正しい

# 署名検証のためのライブラリをインポート (LINE Bot SDKが内部で処理するため通常は不要だが、デバッグ用として残す)
# 本番運用ではパフォーマンスのため削除またはコメントアウトを推奨
import hmac
import hashlib
import base64

# Google Generative AI SDK のインポート
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# ロギング設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)

# .envファイルから環境変数を読み込む（Renderでは不要だが、ローカル実行時のためにコメントアウト）
# load_dotenv()

# 環境変数からLINEとGeminiのAPIキーを取得
# Renderに設定されている環境変数名に合わせて修正
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
# ボット名を「支援メイトBot」に変更し、提供された構造と条件を反映
SHIP_SUPPORT_SYSTEM_PROMPT = """
あなたは障害福祉分野に詳しい専門相談員であり、「支援メイトBot」という名前のAIです。
支援者が適切な判断と対応ができるように、心理的・制度的・現場実践的なアドバイスを端的に提供してください。

支援者からの質問は、以下の形式のいずれかの情報を含んでいる可能性があります。
全ての項目が一度に提供されなくても構いません。提供された情報に基づいて、現時点で可能な範囲でのアドバイスを提供してください。
より詳細な情報をご提供いただけると、回答の精度が高まります。

【質問形式の項目】
* **【事業所種別】**：{支援領域}（例：就労移行支援、就労定着支援、B型作業所、放課後等デイサービス、グループホームなど）
    どのような事業所の支援についてお困りですか？ (例: 「就労移行について相談です」)
* **【障害種別】**：{障害名}（障害の特性）（例：発達障害（ASD）、統合失調症、知的障害3度、精神障害2級、身体障害、高次脳、難病など）
    対象となる方の障害の特性（例：統合失調症、知的障害3度、精神障害2級など）を教えていただけますか？ (例: 「発達障害（ASD）の方についてです」)
* **【利用者の状態】**：{状態・フェーズ}（例：初回面談、不安定、暴言、職場トラブル、家庭問題、就職後定着、体調の波が激しい、他利用者とのトラブル、作業拒否が多いなど）
    利用者の現在の具体的な状態やフェーズはどのような状況でしょうか？ (例: 「最近、情緒が不安定で…」)
* **【支援者の悩み・相談内容】**：{フリーテキスト入力}（例：報連相が苦手で、実習先との連携に悩んでいます。など）
    具体的にどのような点でお悩みでしょうか？ (例: 「日中活動への参加が難しい利用者への対応について」)

【回答条件】
* 提供された情報が一部でも、その情報に基づいた**仮のアドバイスや一般的な情報を提供**してください。
* 情報が不足している場合は、**具体的にどのような情報（上記の例のような問いかけ）があればより的確なアドバイスができるか**を、ユーザーが次の質問で答えやすいように促してください。
* 支援者の心理的な安心感にも配慮し、寄り添うトーンで回答してください。
* 具体的な対応方法を2〜3案、簡潔に提示してください。
* 必要に応じて、関連する制度、推奨される研修、または専門資格などを紹介してください。
* 専門用語は避け、分かりやすい言葉で説明してください。
* 返答は長すぎず、支援者がすぐに理解できる適切な長さに調整してください。
* **各応答の最後に、支援者がさらに質問しやすくなるような、関連性のある問いかけや、次のアクションを促す言葉を必ず含めてください。**
* AIは個別のケースに関する具体的な判断や、医療・法律に関する専門的なアドバイスは行いません。緊急を要する事柄や、詳細な個人情報に基づいた相談、専門的な判断が必要な場合は、**「この内容については、より詳細な情報が必要なため、各事業所の担当者または法人本部にお問い合わせください。」**と案内し、担当部署への問い合わせを促してください。

**Gemini APIの無料枠を考慮し、無駄なトークン消費を避けるため、簡潔かつ的確な応答を心がけてください。また、同じような質問の繰り返しは避け、会話の進展を促してください。**
"""

# 初期メッセージは動的に生成するため、ここではテンプレートの例として残します
# INITIAL_MESSAGE = "いつも利用者様支援に一生懸命取り組んでいただき、ありがとうございます。\n日々の業務や利用者支援でお困りでしたら、気軽にご相談ください。「支援メイトBot」が専門相談員としてサポートさせていただきます。\n\nより的確なアドバイスのため、例えば「事業所種別」や「障害の特性（例：統合失調症、知的障害3度、精神障害2級など）」など、分かる範囲でお知らせいただけますか？"

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
    start_callback_time = time.time() # コールバック処理全体の開始時刻
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)

    if not signature:
        app.logger.error(f"[{time.time() - start_callback_time:.3f}s] X-Line-Signature header is missing.")
        abort(400) # 署名がない場合は不正なリクエストとして処理

    app.logger.info(f"[{time.time() - start_callback_time:.3f}s] Received Webhook Request.")
    app.logger.info("  Request body (truncated to 500 chars): " + body[:500])
    app.logger.info(f"  X-Line-Signature: {signature}")

    # --- 署名検証のデバッグログ ---
    # ユーザーが提供したコードを保持し、デバッグの助けとなるように残す
    try:
        secret_bytes = CHANNEL_SECRET.encode('utf-8')
        body_bytes = body.encode('utf-8')
        hash_value = hmac.new(secret_bytes, body_bytes, hashlib.sha256).digest()
        calculated_signature = base64.b64encode(hash_value).decode('utf-8')

        app.logger.info(f"[{time.time() - start_callback_time:.3f}s]   Calculated signature (manual): {calculated_signature}")
        app.logger.info(f"[{time.time() - start_callback_time:.3f}s]   Channel Secret used for manual calc (first 5 chars): {CHANNEL_SECRET[:5]}...")

        if calculated_signature != signature:
            app.logger.error(f"[{time.time() - start_callback_time:.3f}s] !!! Manual Signature MISMATCH detected !!!")
            app.logger.error(f"[{time.time() - start_callback_time:.3f}s]     Calculated: {calculated_signature}")
            app.logger.error(f"[{time.time() - start_callback_time:.3f}s]     Received:    {signature}")
            # 手動計算で不一致が検出された場合は、SDK処理に入る前に終了
            abort(400)
        else:
            app.logger.info(f"[{time.time() - start_callback_time:.3f}s]   Manual signature check: Signatures match! Proceeding to SDK handler.")

    except Exception as e:
        app.logger.error(f"[{time.time() - start_callback_time:.3f}s] Error during manual signature calculation for debug: {e}", exc_info=True)
        # 手動計算でエラーが発生しても、SDKの処理は試みる
        pass

    # --- LINE Bot SDKによる署名検証とイベント処理 ---
    try:
        handler.handle(body, signature)
        app.logger.info(f"[{time.time() - start_callback_time:.3f}s] Webhook handled successfully by SDK.")
    except InvalidSignatureError:
        app.logger.error(f"[{time.time() - start_callback_time:.3f}s] !!! SDK detected Invalid signature !!!")
        app.logger.error("  This typically means CHANNEL_SECRET in Render does not match LINE Developers.")
        app.logger.error(f"  Body (truncated for error log): {body[:200]}...")
        app.logger.error(f"  Signature sent to SDK: {signature}")
        app.logger.error(f"  Channel Secret configured for SDK (first 5 chars): {CHANNEL_SECRET[:5]}...")
        abort(400) # 署名エラーの場合は400を返す
    except Exception as e:
        # その他の予期せぬエラー
        logging.critical(f"[{time.time() - start_callback_time:.3f}s] Unhandled error during webhook processing by SDK: {e}", exc_info=True)
        abort(500)

    app.logger.info(f"[{time.time() - start_callback_time:.3f}s] Total callback processing time.")
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    start_handle_time = time.time() # handle_message 処理開始時刻を記録
    user_id = event.source.user_id # ユーザーIDを取得
    user_message = event.message.text
    app.logger.info(f"[{time.time() - start_handle_time:.3f}s] Received text message from user_id: '{user_id}', message: '{user_message}' (Reply Token: {event.reply_token})")

    response_text = "申し訳ありません、現在メッセージを処理できません。しばらくしてからもう一度お試しください。"

    # ユーザーセッションの初期化または取得
    current_date = datetime.date.today()

    # 新規ユーザーまたはセッションリセットのロジック
    # (注意: user_sessionsはサーバーの再起動でリセットされます)
    if user_id not in user_sessions or user_sessions[user_id]['last_request_date'] != current_date:
        # 日付が変わった場合、または新規ユーザーの場合、セッションをリセット
        app.logger.info(f"[{time.time() - start_handle_time:.3f}s] Initializing/Resetting session for user_id: {user_id}. First message of the day or new user.")
        user_sessions[user_id] = {
            'history': [], # 会話履歴は空で開始
            'request_count': 0,
            'last_request_date': current_date,
            'display_name': "SHIP職員" # デフォルト値を設定
        }

        # ユーザー名を取得し、初回メッセージをパーソナライズ
        # 永続化されたセッションにdisplay_nameを保存すれば、次回以降はAPI呼び出し不要
        # 現状ではアプリ再起動でリセットされるため、毎回初回はAPIを叩く
        start_get_profile = time.time()
        try:
            # !!! 修正: get_profileにGetProfileRequestオブジェクトを渡すように変更 !!!
            profile_response = line_bot_api.get_profile(GetProfileRequest(user_id=user_id))
            if profile_response and hasattr(profile_response, 'display_name'):
                user_sessions[user_id]['display_name'] = profile_response.display_name
                app.logger.info(f"[{time.time() - start_get_profile:.3f}s] Fetched display name for user {user_id}: {user_sessions[user_id]['display_name']}")
            else:
                app.logger.warning(f"[{time.time() - start_get_profile:.3f}s] Could not get display name for user {user_id}. Profile response: {profile_response}")
        except LineBotApiError as e: # LINE APIからのエラーを具体的にキャッチ
            app.logger.error(f"[{time.time() - start_get_profile:.3f}s] LineBotApiError getting user profile for {user_id}: {e}", exc_info=True)
            # エラー時もデフォルト名で続行
        except Exception as e: # その他の予期せぬエラー
            app.logger.error(f"[{time.time() - start_get_profile:.3f}s] Unexpected error getting user profile for {user_id}: {e}", exc_info=True)
            # エラー時もデフォルト名で続行

        user_display_name = user_sessions[user_id]['display_name']

        # パーソナライズされた初期メッセージを生成
        personalized_initial_message = (
            f"{user_display_name}さん、いつも利用者様支援に一生懸命取り組んでいただき、ありがとうございます。\n"
            "日々の業務や利用者支援でお困りでしたら、気軽にご相談ください。「支援メイトBot」が専門相談員としてサポートさせていただきます。\n\n"
            "より的確なアドバイスのため、例えば「事業所種別」や「障害の特性（例：統合失調症、知的障害3度、精神障害2級など）」など、分かる範囲でお知らせいただけますか？"
        )
        response_text = personalized_initial_message

        try:
            start_reply_initial = time.time()
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[LineReplyTextMessage(text=response_text)]
                )
            )
            app.logger.info(f"[{time.time() - start_reply_initial:.3f}s] Sent personalized initial message/daily reset message to user {user_id}.")
        except Exception as e:
            logging.error(f"Error sending personalized initial/reset reply to LINE for user {user_id}: {e}", exc_info=True)
        app.logger.info(f"[{time.time() - start_handle_time:.3f}s] handle_message finished for initial/reset flow.")
        return 'OK' # 初回メッセージ送信後はここで処理を終了。この返信はGeminiを呼び出さない。

    # Gemini API利用回数制限のチェック
    if user_sessions[user_id]['request_count'] >= MAX_GEMINI_REQUESTS_PER_DAY:
        response_text = GEMINI_LIMIT_MESSAGE
        app.logger.warning(f"User {user_id} exceeded daily Gemini request limit ({MAX_GEMINI_REQUESTS_PER_DAY}).")
        try:
            start_reply_limit = time.time()
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[LineReplyTextMessage(text=response_text)]
                )
            )
            app.logger.info(f"[{time.time() - start_reply_limit:.3f}s] Sent limit message to LINE for user {user_id}.")
        except Exception as e:
            logging.error(f"Error sending limit reply to LINE for user {user_id}: {e}", exc_info=True)
        app.logger.info(f"[{time.time() - start_handle_time:.3f}s] handle_message finished for limit exceeded flow.")
        return 'OK'

    # 会話履歴を準備
    # システムプロンプトと初期応答を履歴の最初に含める
    chat_history_for_gemini = [
        {'role': 'user', 'parts': [{'text': SHIP_SUPPORT_SYSTEM_PROMPT}]},
        {'role': 'model', 'parts': [{'text': "はい、承知いたしました。支援メイトBotとして、ご質問にお答えします。"}]}
    ]

    # MAX_CONTEXT_TURNS に基づいて過去の会話を結合
    # 各ターンはユーザーとモデルのペアなので、履歴から取得する要素数は MAX_CONTEXT_TURNS * 2
    start_index = max(0, len(user_sessions[user_id]['history']) - MAX_CONTEXT_TURNS * 2)

    app.logger.debug(f"[{time.time() - start_handle_time:.3f}s] Current history length for user {user_id}: {len(user_sessions[user_id]['history'])}. Taking from index {start_index}.")

    # 過去の会話履歴を追加
    for role, text_content in user_sessions[user_id]['history'][start_index:]:
        chat_history_for_gemini.append({'role': role, 'parts': [{'text': text_content}]})

    app.logger.debug(f"[{time.time() - start_handle_time:.3f}s] Gemini chat history prepared for user {user_id} (last message: '{user_message}'): {chat_history_for_gemini}")

    try:
        start_gemini_call = time.time() # Gemini呼び出し前を計測
        # Geminiとのチャットセッションを開始
        # historyにこれまでの会話履歴（システムプロンプト含む）を渡し、
        # 最新のユーザーメッセージのみをsend_messageで送る
        convo = gemini_model.start_chat(history=chat_history_for_gemini)
        gemini_response = convo.send_message(user_message)
        end_gemini_call = time.time() # Gemini呼び出し後を計測
        app.logger.info(f"[{end_gemini_call - start_gemini_call:.3f}s] Gemini API call completed for user {user_id}.")


        if gemini_response and hasattr(gemini_response, 'text'):
            response_text = gemini_response.text
        elif isinstance(gemini_response, list) and gemini_response and hasattr(gemini_response[0], 'text'):
            response_text = gemini_response[0].text
        else:
            logging.warning(f"[{time.time() - start_handle_time:.3f}s] Unexpected Gemini response format or no text content: {gemini_response}")
            response_text = "Geminiからの応答形式が予期せぬものでした。"

        app.logger.info(f"[{time.time() - start_handle_time:.3f}s] Gemini generated response for user {user_id}: '{response_text}'")

        # 会話履歴を更新 (user_sessionsに保存)
        user_sessions[user_id]['history'].append(['user', user_message])
        user_sessions[user_id]['history'].append(['model', response_text])

        # リクエスト数をインクリメント
        user_sessions[user_id]['request_count'] += 1
        user_sessions[user_id]['last_request_date'] = current_date # リクエスト日を更新
        app.logger.info(f"[{time.time() - start_handle_time:.3f}s] User {user_id} - Request count: {user_sessions[user_id]['request_count']}")

    except Exception as e:
        logging.error(f"[{time.time() - start_handle_time:.3f}s] Error interacting with Gemini API for user {user_id}: {e}", exc_info=True)
        response_text = "Geminiとの通信中にエラーが発生しました。時間を置いてお試しください。"

    finally:
        start_reply_line = time.time() # LINEへの返信処理の前後を計測
        try:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[LineReplyTextMessage(text=response_text)]
                )
            )
            app.logger.info(f"[{time.time() - start_reply_line:.3f}s] Reply sent to LINE successfully for user {user_id}.")
        except Exception as e:
            logging.error(f"Error replying to LINE for user {user_id}: {e}", exc_info=True)

    app.logger.info(f"[{time.time() - start_handle_time:.3f}s] Total handle_message processing time.")
    return 'OK'

if __name__ == "__main__":
    # Render環境ではPORT環境変数が設定されるため、それを使用する
    # ローカル実行時にはデフォルトで8080を使用
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
