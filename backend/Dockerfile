FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 && rm -rf /var/lib/apt/lists/*
COPY requirements.lock pyproject.toml ./
RUN pip install -r requirements.lock
COPY app ./app
RUN pip install --no-deps . && useradd --create-home aurea && mkdir -p /data/audio && chown aurea:aurea /data/audio
ENV AUREA_STORAGE_DIR=/data/audio
USER aurea
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--access-log"]
