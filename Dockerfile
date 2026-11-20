# Stage 1: Build React + TypeScript + Tailwind dashboard
FROM node:22-slim AS frontend-builder
WORKDIR /build/dashboard
COPY dashboard/package*.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

# Stage 2: Minimal Python runtime
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    JARVIS_HOST=0.0.0.0 \
    JARVIS_PORT=7777 \
    JARVIS_STATE_DIR=/data/state \
    JARVIS_BRAINS_DIR=/brains

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
COPY prompts/ ./prompts/
COPY system/ ./system/
COPY cli/ ./cli/
COPY --from=frontend-builder /build/dashboard/dist ./dashboard/dist

RUN mkdir -p /data/state /brains
VOLUME ["/data/state", "/brains"]
EXPOSE 7777

CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "7777"]
