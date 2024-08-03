FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install uv

COPY requirements.txt .

RUN uv pip install --system -r requirements.txt

COPY mainbot.py .

CMD ["python", "mainbot.py"] 