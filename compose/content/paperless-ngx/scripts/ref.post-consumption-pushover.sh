#!/usr/bin/env bash

PAPERLESS_BASE_URL="op://Docker/Paperless/URL"
PAPERLESS_URL="${PAPERLESS_BASE_URL}/documents/${DOCUMENT_ID}"

export TITLE="New Document consumed"
#message="Imported document <b>${DOCUMENT_ORIGINAL_FILENAME}</b> as <b>${DOCUMENT_FILE_NAME}</b> <br> <a href=\"${PAPERLESS_BASE_URL}${DOCUMENT_DOWNLOAD_URL}\">Download</a>"
#message="Imported document as <b>${DOCUMENT_FILE_NAME}</b> <br> <a href=\"${PAPERLESS_BASE_URL}${DOCUMENT_DOWNLOAD_URL}\">Download</a>"
#message="Imported document as <b>${DOCUMENT_FILE_NAME}</b>"
#export MESSAGE="Consumed document \"<b>${DOCUMENT_ORIGINAL_FILENAME}</b>\""
#export MESSAGE="Consumed document \"<b>${DOCUMENT_ORIGINAL_FILENAME}</b>\"<br><a href=\"${PAPERLESS_URL}\">Open in Paperless</a>"
export MESSAGE="Consumed document \"<a href=\"${PAPERLESS_URL}\">${DOCUMENT_ORIGINAL_FILENAME}</a>\""
#message="Imported document <b>${DOCUMENT_ORIGINAL_FILENAME}</b><br><a href=\"paperparrot://documents/${DOCUMENT_ID}\">Open in Paperparrot</a>"

export URL="paperparrot://documents/${DOCUMENT_ID}"
export URL_TITLE="Open in Paperparrot"

#bash /scripts/send_pushover_message.sh "${message}" "New Document Imported" "${DOCUMENT_ID}"
bash /scripts/send_pushover_message.sh
