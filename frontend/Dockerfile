FROM python:3.12-slim

RUN useradd --create-home --uid 1000 appuser
WORKDIR /app

COPY frontend/index.html frontend/styles.css frontend/config.js ./
COPY frontend/js ./js
COPY frontend/entrypoint.sh /entrypoint.sh

RUN chown -R appuser:appuser /app && chmod +x /entrypoint.sh
USER appuser

EXPOSE 3000
ENTRYPOINT ["/entrypoint.sh"]
