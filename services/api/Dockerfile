FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 票据 OCR（ADR-0034）：开源 Tesseract + 中文包，容器内 POST /v1/expenses:ocr 直接可用
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# psycopg[binary] / ortools / cryptography 都有 manylinux wheel；只装运行必需的库。
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/api /app/services/api
COPY packages/planning-engine /app/packages/planning-engine

ENV PYTHONPATH=/app/services/api:/app/packages/planning-engine
WORKDIR /app/services/api

EXPOSE 8080

# 迁移在 API 启动前执行；Worker 通过 compose command 覆盖为后台循环。
CMD ["sh", "-c", "python -m alembic -c alembic.ini upgrade head && python -m uvicorn app.main:app --host 0.0.0.0 --port 8080"]
