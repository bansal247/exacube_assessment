FROM python:3.12-slim AS builder

WORKDIR /app
COPY eval/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.12-slim

RUN useradd --create-home --uid 1000 appuser
WORKDIR /app

COPY --from=builder /root/.local /home/appuser/.local
COPY eval/questions.py eval/run_eval.py ./

RUN chown -R appuser:appuser /app
USER appuser
ENV PATH=/home/appuser/.local/bin:$PATH

CMD ["python", "run_eval.py"]
