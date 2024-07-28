#!/bin/bash

# Function to process files
process_file() {
    local file="${1}"
    local force="${2}"
    local skip_existing="${3}"
    local dry_run="${4}"

    # Determine the output filename
    if [ "$(basename "${file}")" == "ref.env" ]; then
        output_file="$(dirname "${file}")/.env"
    else
        output_file="$(dirname "${file}")/$(basename "${file}" | sed 's/^ref.//')"
    fi

    # Check if file exists and decide whether to skip or overwrite
    if [ -f "${output_file}" ] && [ "${skip_existing}" == "true" ]; then
        echo "Skipping existing file: ${output_file}"
        return
    fi

    # If dry run is enabled, display the message and return
    if [ "${dry_run}" == "true" ]; then
        echo "Dry run: Would process ${file} to ${output_file}"
        return
    fi

    # Process the file
    echo "Processing ${file} to ${output_file}..."
    if [ "${force}" == "true" ]; then
        op inject -f -i "${file}" -o "${output_file}" > /dev/null
    else
        op inject -i "${file}" -o "${output_file}" > /dev/null
    fi
}

# Check if directory is provided
if [ -z "${1}" ]; then
    echo "Usage: ${0} [-f] [-s] [-n] <directory>"
    exit 1
fi

# Parse options
force=false
skip_existing=false
dry_run=false

while getopts ":fsn" opt; do
    case ${opt} in
        f)
            force=true
            ;;
        s)
            skip_existing=true
            ;;
        n)
            dry_run=true
            ;;
        \?)
            echo "Invalid option: -${OPTARG}" >&2
            echo "Usage: ${0} [-f] [-s] [-n] <directory>"
            exit 1
            ;;
    esac
done
shift $((OPTIND -1))

# Directory to search
SEARCH_DIR="${1}"

# Validate directory
if [ ! -d "${SEARCH_DIR}" ]; then
    echo "Directory not found: ${SEARCH_DIR}"
    exit 1
fi

# Find and process the files
find "${SEARCH_DIR}" -type f -name 'ref.*' | while read -r file; do
    process_file "${file}" "${force}" "${skip_existing}" "${dry_run}"
done

echo "All matching files processed."
