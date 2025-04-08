#!/bin/bash

# File that keeps track of added WARC files
ADDED_WARCS="/webarchive/added_warcs.txt"
# Directory where new WARC files will be imported
IMPORT_DIR="/webarchive/import"
# Directory where the WARC files are stored
ARCHIVE_DIR="/archivebox/archive"

echo "START $(date --iso-8601=seconds)"

# Ensure required paths exist
touch "${ADDED_WARCS}"
mkdir -p "${IMPORT_DIR}"
mkdir -p "${ARCHIVE_DIR}"

# Find all potential WARC files
mapfile -t warc_files < <(find "${ARCHIVE_DIR}" -type f -regex ".*\.warc\.gz")

# Process each WARC file
for warc_file in "${warc_files[@]}"; do
  if grep -Fxq "${warc_file}" "${ADDED_WARCS}"; then
    continue  # Skip if already added
  fi
  echo "New WARC: ${warc_file}"

  # Reconstruct the path for import
  relative_path="${warc_file#"${ARCHIVE_DIR}/"}"
  new_warc="${IMPORT_DIR}/${relative_path//\//_}"  # Replace '/' with '_'

  # Copy the WARC file to the import directory
  cp "${warc_file}" "${new_warc}"
  if ! cp "${warc_file}" "${new_warc}" 2>/dev/null; then
    echo "Failed to copy ${warc_file} to ${new_warc}"
    continue
  fi

  # Add the new WARC file to wayback
  if wb-manager add archivebox "${new_warc}" 2>&1; then
    echo "${warc_file}" >> "${ADDED_WARCS}"
    rm -f "${new_warc}"
  else
    echo "Failed to add ${new_warc}"
    rm -f "${new_warc}" # Still clean up on failure
  fi
done

echo "END   $(date --iso-8601=seconds)"
