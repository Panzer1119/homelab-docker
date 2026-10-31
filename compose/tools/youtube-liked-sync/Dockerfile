FROM python:3.12-alpine@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8

RUN pip install --no-cache-dir \
    google-api-python-client==2.201.0 \
    google-auth-httplib2==0.4.4 \
    google-auth-oauthlib==1.5.0

WORKDIR /app

COPY scripts/sync_liked_videos.py /app/sync_liked_videos.py
COPY compose/tools/youtube-liked-sync/entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]
