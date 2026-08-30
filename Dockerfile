# ---- 构建阶段 ----
FROM python:3.12-slim AS base

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data

# 依赖层（利用 Docker 缓存）
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ---- 运行阶段 ----
COPY backend/ ./backend/
COPY frontend/ ./frontend/

EXPOSE 8322
WORKDIR /app/backend

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8322"]