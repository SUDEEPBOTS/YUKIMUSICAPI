FROM python:3.11-slim

# ─────────────────────────────
# System dependencies + Node.js
# ─────────────────────────────
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    ca-certificates \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# ─────────────────────────────
# yt-dlp (Python module)
# ─────────────────────────────
RUN pip install --no-cache-dir yt-dlp

# ─────────────────────────────
# Python dependencies
# ─────────────────────────────
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# ─────────────────────────────
# App code
# ─────────────────────────────
WORKDIR /app
COPY . /app

# ─────────────────────────────
# Expose port (Render ignores but ok)
# ─────────────────────────────
EXPOSE 10000

# ─────────────────────────────
# Start server
# ─────────────────────────────
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000", "--workers", "1", "--timeout-keep-alive", "75"]
