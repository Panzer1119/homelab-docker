#!/bin/bash

# Constants
API_URL="op://Docker/SABnzbd/API/URL"
API_KEY="op://Docker/SABnzbd/API/Key"

# Create backup
curl --fail --silent --show-error "${API_URL}" --data-raw "mode=config&name=create_backup&output=json&apikey=${API_KEY}"
