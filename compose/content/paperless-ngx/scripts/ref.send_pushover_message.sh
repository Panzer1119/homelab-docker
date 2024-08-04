#!/bin/bash

APP_KEY="op://Docker/Paperless/Pushover/App-Key"
USER_KEY="op://Docker/Paperless/Pushover/User-Key"
#MESSAGE="$1"
PRIORITY="${4:-0}"
#TITLE="$2"
HTML="1"

curl -s \
  --form-string "token=${APP_KEY}" \
  --form-string "user=${USER_KEY}" \
  --form-string "message=${MESSAGE}" \
  --form-string "priority=${PRIORITY}" \
  --form-string "title=${TITLE}" \
  --form-string "html=${HTML}" \
  --form-string "url=${URL}" \
  --form-string "url_title=${URL_TITLE}" \
  https://api.pushover.net/1/messages.json
