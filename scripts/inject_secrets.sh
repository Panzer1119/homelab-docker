#!/bin/bash

# Function to process files
process_files() {
    local file="$1"
    local force="$2"
    local skip_existing="$3"

    # Determine the output filename
    if [ "$(basename "${file}")" == "ref.env" ]; then
        output_file="$(dirname "${file}")/.env"
    else
        output_file="$(dirname "${file}")/$(basename "${file}" | sed 's/^ref.//')"
    fi

    # Check if file exists and decide whether to skip or overwrite
    if [ -f "${output_file}" ] && [ "${skip_existing}" == "true" ]; then
        echo "Skipping existing file: ${output_file}"
    else
        echo "Processing ${file} to ${output_file}..."
        if [ "${force}" == "true" ]; then
            op inject -f -i "${file}" -o "${output_file}"
        else
            op inject -i "${file}" -o "${output_file}"
        fi
    fi
}

# Check if directory is provided
if [ -z "$1" ]; then
    echo "Usage: $0 [-f] [-s] <directory>"
    exit 1
fi

# Parse options
force=false
skip_existing=false

while getopts ":fs" opt; do
    case ${opt} in
        f)
            force=true
            ;;
        s)
            skip_existing=true
            ;;
        \?)
            echo "Invalid option: -$OPTARG" >&2
            echo "Usage: $0 [-f] [-s] <directory>"
            exit 1
            ;;
    esac
done
shift $((OPTIND -1))

# Directory to search
SEARCH_DIR="$1"

# Validate directory
if [ ! -d "${SEARCH_DIR}" ]; then
    echo "Directory not found: ${SEARCH_DIR}"
    exit 1
fi

# Find and process the files
find "${SEARCH_DIR}" -type f -name 'ref*.env' | while read -r file; do
    process_files "${file}" "${force}" "${skip_existing}"
done

echo "All matching files processed."
