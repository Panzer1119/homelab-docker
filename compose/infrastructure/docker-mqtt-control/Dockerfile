FROM python:3.14-alpine@sha256:601e99e090cb08c2784a42395487709500bc39930307fe72f3f11a8f3c430762

RUN pip install --no-cache-dir \
    paho-mqtt==2.1.0 \
    docker==7.1.0

WORKDIR /app

COPY controller.py .

CMD ["python", "-u", "controller.py"]
