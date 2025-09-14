# Dockerfile
FROM python:3.11-slim

# install ffmpeg + build deps
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# Use gunicorn for Flask webhook hosting
ENV PORT=5000
CMD ["python3", "main:app", "-b", "0.0.0.0:5000", "--workers", "1", "--timeout", "300"]
