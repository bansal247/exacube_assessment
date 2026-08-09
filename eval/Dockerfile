FROM python:3.12-slim

WORKDIR /app
COPY eval/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY eval/questions.py eval/run_eval.py ./

CMD ["python", "run_eval.py"]
