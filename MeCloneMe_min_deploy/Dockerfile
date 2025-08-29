FROM python:3.11-slim
ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt
COPY . .
CMD ["sh","-c","uvicorn backend.app:app --host 0.0.0.0 --port $PORT"]
