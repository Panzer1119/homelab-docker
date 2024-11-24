#!/bin/bash

USER_ID="op://Docker/Paperless/Paperparrot/User-ID"
DOCUMENT_TITLE="${DOCUMENT_ORIGINAL_FILENAME}"

curl -s \
  --request POST \
  --url https://push.paperparrot.me/ \
  --header "Content-Type:application/json" \
  --data "{\"user_id\": \"${USER_ID}\", \"document_id\": \"${DOCUMENT_ID}\", \"document_title\": \"${DOCUMENT_TITLE}\"}"
