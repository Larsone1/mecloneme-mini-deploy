# syntax=docker/dockerfile:1
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY clone ./clone
RUN mkdir -p /app/data
EXPOSE 8000
CMD ["uvicorn", "backend.mcm_api:app", "--host", "0.0.0.0", "--port", "8000"]
