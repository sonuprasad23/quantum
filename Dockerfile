# Dockerfile
FROM python:3.11-slim

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC
ENV HOME=/home/appuser
ENV PYTHONUSERBASE=/home/appuser/.local

# Install system dependencies optimized for Railway
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg \
    unzip \
    ca-certificates \
    chromium \
    chromium-driver \
    libnss3 \
    libxss1 \
    libasound2 \
    libxtst6 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libgbm1 \
    fonts-liberation \
    libappindicator3-1 \
    libdrm2 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    && rm -rf /var/lib/apt/lists/*

# Create unprivileged user
RUN addgroup --system appuser && adduser --system --ingroup appuser appuser
RUN mkdir -p /home/appuser/.local && chown -R appuser:appuser /app /home/appuser

USER appuser

# Install Python dependencies
COPY --chown=appuser:appuser requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

ENV PATH="/home/appuser/.local/bin:${PATH}"

# Copy application files
COPY --chown=appuser:appuser . .

# Create config directory
RUN mkdir -p config

# Railway uses PORT environment variable
EXPOSE $PORT

CMD ["python", "server.py"]
