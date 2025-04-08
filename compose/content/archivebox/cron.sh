#!/bin/bash

SCRIPT_FILE="./add_new_warcs.sh"
ARCHIVEBOX_VOLUME="archivebox_data"
WEBARCHIVE_VOLUME="archivebox_webarchive"
#ARCHIVEBOX_DIR="/docker/data/archivebox"
#WEBARCHIVE_DIR="/docker/data/archivebox/webarchive"
IMAGE="webrecorder/pywb:latest"

#docker run --rm -v "${ARCHIVEBOX_DIR}:/archivebox:ro" -v "${WEBARCHIVE_DIR}:/webarchive" -v "${SCRIPT_FILE}:/run.sh:ro" "${IMAGE}" bash /run.sh
#docker run --rm -e PUID=1000 -e PGID=1000 -v "${ARCHIVEBOX_DIR}:/archivebox:ro" -v "${WEBARCHIVE_DIR}:/webarchive" -v "${SCRIPT_FILE}:/run.sh:ro" "${IMAGE}" bash /run.sh
docker run --rm -e PUID=1000 -e PGID=1000 -v "${ARCHIVEBOX_VOLUME}:/archivebox:ro" -v "${WEBARCHIVE_VOLUME}:/webarchive" -v "${SCRIPT_FILE}:/run.sh:ro" "${IMAGE}" bash /run.sh

#docker run --rm -v "${ARCHIVEBOX_DIR}:/archivebox:ro" -v "${WEBARCHIVE_DIR}:/webarchive" -v "${SCRIPT_FILE}:/run.sh:ro" "${IMAGE}" ls -lah added_warcs.txt
#docker run --rm -v "${ARCHIVEBOX_DIR}:/archivebox:ro" -v "${WEBARCHIVE_DIR}:/webarchive" -v "${SCRIPT_FILE}:/run.sh:ro" "${IMAGE}" id
#docker run --rm -v "${ARCHIVEBOX_DIR}:/archivebox:ro" -v "${WEBARCHIVE_DIR}:/webarchive" -v "${SCRIPT_FILE}:/run.sh:ro" "${IMAGE}" tail added_warcs.txt
#docker run --rm -v "${ARCHIVEBOX_DIR}:/archivebox:ro" -v "${WEBARCHIVE_DIR}:/webarchive" -v "${SCRIPT_FILE}:/run.sh:ro" "${IMAGE}" touch added_warcs.txt
