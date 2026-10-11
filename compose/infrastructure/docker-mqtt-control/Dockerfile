FROM python:3.12-alpine

RUN pip install --no-cache-dir \
    paho-mqtt==2.1.0 \
    docker==7.1.0

WORKDIR /app

COPY controller.py .

CMD ["python", "-u", "controller.py"]
