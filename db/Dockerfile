FROM python:3.12-slim AS builder

WORKDIR /app
COPY db/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.12-slim

RUN useradd --create-home --uid 1000 appuser
WORKDIR /app

COPY --from=builder /root/.local /home/appuser/.local
COPY db/schema.sql db/load.py ./
COPY discord_analytics_dataset ./discord_analytics_dataset

RUN chown -R appuser:appuser /app
USER appuser
ENV PATH=/home/appuser/.local/bin:$PATH

CMD ["python", "load.py"]
