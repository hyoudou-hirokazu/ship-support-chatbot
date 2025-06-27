import os
from dotenv import load_dotenv
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import google.generativeai as genai

# .envファイルから環境変数を読み込む
load_dotenv()

app = Flask(__name__)

# 環境変数の取得
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN or not GEMINI_API_KEY:
    raise ValueError("環境変数が設定されていません。LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN, GEMINI_API_KEY を設定してください。")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Gemini APIの設定
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-pro')

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text
    reply_message = ""

    # ここにGemini APIへのプロンプト設計を工夫する
    # 例：社会福祉法人SHIPの障害者福祉事業所に関する質問としてGeminiに投げる
    prompt = f"""
あなたは社会福祉法人SHIPの障害者福祉事業所の支援者向けサポートAIです。
以下の情報に基づいて、支援者からの質問に正確かつ丁寧に回答してください。
社会福祉法人SHIPは、就労移行支援、就労定着支援、B型作業所、放課後等デイサービス、
グループホーム（障害区分中～軽度、重度対応）を運営しています。

各事業所の特徴、利用方法、対象者、緊急連絡先、よくある質問、
そして障害者福祉全般に関する専門知識について、支援者が求める情報を提供してください。

---
質問: {user_message}
---

上記質問に対して、具体的な情報やサポートを提供してください。
もし情報が不足している場合や、専門的な判断が必要な場合は、
「この内容については、より詳細な情報が必要なため、各事業所の担当者または法人本部にお問い合わせください。」
のように案内してください。

"""
    try:
        response = model.generate_content(prompt)
        reply_message = response.text
    except Exception as e:
        app.logger.error(f"Gemini API Error: {e}")
        reply_message = "現在、システムに問題が発生しており、ご質問にお答えできません。しばらくしてから再度お試しいただくか、担当者までお問い合わせください。"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_message)
    )

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)