FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY exporter.py .

RUN useradd --system --uid 10001 exporter \
    && chown -R exporter:exporter /app

USER exporter

CMD ["python", "-u", "exporter.py"]
