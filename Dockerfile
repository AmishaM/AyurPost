FROM python:3.11-slim

# System deps: ffmpeg for reel assembly
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer cache)
COPY requirements-app.txt .
RUN pip install --no-cache-dir -r requirements-app.txt

# Copy source
COPY src/ src/

# Streamlit config — disable telemetry, run on 8080 (Cloud Run default)
ENV PYTHONPATH=src \
    STREAMLIT_SERVER_PORT=8080 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_HEADLESS=true

EXPOSE 8080

CMD ["streamlit", "run", "src/ayurpost/app.py"]
