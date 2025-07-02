# Pythonの公式イメージをベースにする
FROM python:3.9-slim-buster

# 作業ディレクトリを設定
WORKDIR /app

# 依存関係をインストール
# ローカルキャッシュを使わないことで、イメージサイズを小さく保つ
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションコードをコピー
COPY . .

# 環境変数 PORT はCloud Runが自動的に設定するため、DockerfileでENV設定は不要です。
# Gunicornが環境変数PORTを読み取るようにします。

# Gunicornを使ってアプリケーションを起動
# Flaskアプリケーションのインスタンス名が 'app' なので、'main:app' を指定
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "main:app"]