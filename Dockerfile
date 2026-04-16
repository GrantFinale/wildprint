# wildprint — production Docker image
# Runs the Flask app via gunicorn, serves the poster creator webapp,
# console, admin dashboard, and review flow. Masters + backgrounds live
# on a persistent volume mounted at /app/output.

FROM python:3.11-slim

# System deps for Pillow + rembg (onnxruntime needs libgomp) + curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gunicorn numpy rembg onnxruntime replicate requests

# Copy the app
COPY config/ ./config/
COPY data/ ./data/
COPY prompts/ ./prompts/
COPY providers/ ./providers/
COPY scripts/ ./scripts/
COPY poster_layout/ ./poster_layout/
COPY review_app/ ./review_app/
COPY webapp/ ./webapp/
COPY metadata/manifest_schema.json ./metadata/

# Seed masters into the image so the app works immediately.
# Runtime-generated content (uploads, posters, new generations, backgrounds)
# lives under /app/output which is mounted as a persistent volume.
COPY output/master ./output/master

# Ensure runtime directories exist
RUN mkdir -p output/raw output/normalized output/posters output/uploads output/backgrounds metadata

ENV PYTHONUNBUFFERED=1
ENV FLASK_SECRET_KEY=change-me-in-prod

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -fsS http://localhost:8080/create >/dev/null || exit 1

# Gunicorn with 2 workers, 4 threads each. Masters load fast; most work is
# subprocess-bound (batch_generate) or network-bound (Replicate), so threaded
# workers are fine for this scale.
CMD ["gunicorn", "-b", "0.0.0.0:8080", "-w", "2", "--threads", "4", \
     "--timeout", "180", "review_app.app:app"]
