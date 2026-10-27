FROM python:3.15-alpine@sha256:0aa144e9ec2ae8fd80dfd94070ef3e432a0df17b6e5e0091dc05323974ce9913

RUN pip install --no-cache-dir \
    paho-mqtt==2.1.0 \
    docker==7.1.0

WORKDIR /app

COPY controller.py .

CMD ["python", "-u", "controller.py"]
