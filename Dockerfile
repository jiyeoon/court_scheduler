FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Seoul

WORKDIR /app

# System dependencies:
# - chromium/chromium-driver: Selenium browser automation
# - tesseract-ocr: OCR for captcha
# - fonts/libnss/etc: runtime dependencies for headless chrome
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    tesseract-ocr \
    tesseract-ocr-eng \
    fonts-noto-cjk \
    libnss3 \
    libxss1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    ca-certificates \
    curl \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src

# Optional hints for runtime (Selenium mostly finds them automatically)
ENV CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver

# Same as reserve.yml command
CMD ["python", "-m", "src.main_hybrid", "--preferred-hour", "20", "--weekend-hour", "10", "--target-time", "09:00:00.300"]

