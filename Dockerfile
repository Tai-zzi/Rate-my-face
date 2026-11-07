FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# opencv-python-headless 휠이 Debian slim에서 필요로 하는 공유 라이브러리
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py .
COPY static/ ./static/
COPY models/ ./models/

# 이미지 빌드 단계에서 모델이 정상 로드되는지 검증 (실패 시 빌드 중단)
RUN python -c "import app; print('model check OK:', app.MODEL_PATH.name, app.DETECTOR is not None)"

# 배포 버전 식별 정보 (CI에서 --build-arg 로 주입)
ARG APP_VERSION=1.0.0
ARG APP_REVISION=local
ENV APP_VERSION=${APP_VERSION} \
    APP_REVISION=${APP_REVISION} \
    HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
