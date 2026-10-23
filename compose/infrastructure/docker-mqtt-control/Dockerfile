FROM python:3.15-alpine@sha256:d44b1254cb92329418d164b22b20d86bbd3869d48e80e09e476531a409a728bb

RUN pip install --no-cache-dir \
    paho-mqtt==2.1.0 \
    docker==7.1.0

WORKDIR /app

COPY controller.py .

CMD ["python", "-u", "controller.py"]
