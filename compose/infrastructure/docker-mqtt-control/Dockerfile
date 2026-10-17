FROM python:3.15-alpine@sha256:2699b90f4d562a22e2dd076c82f964032f64814204bff36d271a54ba4cc2c7e2

RUN pip install --no-cache-dir \
    paho-mqtt==2.1.0 \
    docker==7.1.0

WORKDIR /app

COPY controller.py .

CMD ["python", "-u", "controller.py"]
