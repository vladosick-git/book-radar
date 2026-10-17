# Книжный радар — образ API и worker. Версия Playwright совпадает с requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble
ARG RADAR_BROWSER=chromium
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 RADAR_MODE=server RADAR_DATA_DIR=/data
# Stage 13: local OCR engine with Russian and English data (RADAR_OCR=tesseract); no network calls.
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*
# Optional vendor browser: official HTTPS repository, APT signature verification,
# a key scoped to this repository. No certificate exceptions or host trust changes.
RUN if [ "$RADAR_BROWSER" = "yandex" ]; then \
      apt-get update && apt-get install -y --no-install-recommends curl gnupg ca-certificates && \
      curl --fail --show-error --location --proto '=https' https://repo.yandex.ru/yandex-browser/YANDEX-BROWSER-KEY.GPG -o /tmp/yandex-key && \
      gpg --batch --dearmor -o /usr/share/keyrings/yandex-browser.gpg /tmp/yandex-key && \
      echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/yandex-browser.gpg] https://repo.yandex.ru/yandex-browser/deb stable main' > /etc/apt/sources.list.d/yandex-browser.list && \
      apt-get update && apt-get install -y --no-install-recommends yandex-browser-stable && \
      rm -f /tmp/yandex-key && rm -rf /var/lib/apt/lists/*; \
    elif [ "$RADAR_BROWSER" != "chromium" ]; then echo 'Unsupported RADAR_BROWSER' >&2; exit 1; fi
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY tools/checks/browser_tls.py ./tools/checks/browser_tls.py
COPY frontend/dist ./frontend/dist
RUN mkdir -p /data /backups && chown -R pwuser:pwuser /data /backups
USER pwuser
EXPOSE 8765
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8765", "--proxy-headers", "--forwarded-allow-ips", "*"]
