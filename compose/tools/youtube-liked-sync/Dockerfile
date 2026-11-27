FROM python:3.15-alpine@sha256:0f9552bb28711333da16915ec146f5e6bb44a7fdac9ced15dc17c2e0206346dd

RUN pip install --no-cache-dir \
    google-api-python-client==2.201.0 \
    google-auth-httplib2==0.4.4 \
    google-auth-oauthlib==1.5.0

WORKDIR /app

COPY scripts/sync_liked_videos.py /app/sync_liked_videos.py
COPY compose/tools/youtube-liked-sync/entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]
