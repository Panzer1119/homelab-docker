FROM python:3.15-alpine@sha256:a0f2e268f2ce8f3709611a90054ec6a8ae31f6251f5896b8648b9bb99835e30a

RUN pip install --no-cache-dir \
    google-api-python-client==2.201.0 \
    google-auth-httplib2==0.4.4 \
    google-auth-oauthlib==1.5.0

WORKDIR /app

COPY scripts/sync_liked_videos.py /app/sync_liked_videos.py
COPY compose/tools/youtube-liked-sync/entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]
