# Python 3.11 slim
FROM python:3.11-slim

WORKDIR /workspace

# 빌드 도구 (sentence-transformers 컴파일 가능성 대비)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl \
    && rm -rf /var/lib/apt/lists/*

# 의존성 먼저 (캐시 활용)
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# 임베딩 모델 사전 다운로드 — 런타임 첫 호출 hang 방지
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('jhgan/ko-sroberta-multitask')"

# 앱 소스
COPY app /workspace/app

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/workspace

EXPOSE 8080

CMD ["uvicorn", "app.api.app:app", "--host", "0.0.0.0", "--port", "8080"]
