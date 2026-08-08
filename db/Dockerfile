FROM python:3.12-slim

WORKDIR /app
COPY db/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY db/schema.sql db/load.py ./
COPY discord_analytics_dataset ./discord_analytics_dataset

CMD ["python", "load.py"]
