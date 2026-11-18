FROM node:24-bookworm-slim AS web
WORKDIR /src
COPY frontend/package*.json ./
RUN npm ci --no-audit
COPY frontend/ .
RUN npm run build

FROM python:3.14-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 WEB_DIST=/app/frontend/dist
WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --system --create-home app
COPY --chown=app:app backend/ .
COPY --from=web --chown=app:app /src/dist /app/frontend/dist
USER app
EXPOSE 8000
CMD ["gunicorn","config.wsgi","--bind","0.0.0.0:8000","--workers","2","--timeout","90"]
