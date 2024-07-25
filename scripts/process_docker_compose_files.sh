#!/bin/bash

CREATE_CIFS_VOLUME_SCRIPT_FILENAME="create_docker_cifs_volume.sh"

CREATE_CIFS_VOLUME_SCRIPT_FILE="${PWD}/${CREATE_CIFS_VOLUME_SCRIPT_FILENAME}"

# Function to display usage
usage() {
  echo "Usage: ${0} -d <directory> -a <address> -u <username> -p <password> [-q]"
  echo "  -d <directory>: Directory to recursively search for docker-compose.yml files"
  echo "  -a <address>: Address of the CIFS/SMB server"
  echo "  -u <username>: Username for authentication"
  echo "  -p <password>: Password for authentication"
  echo "  -q: Optional. Quiet mode"
  echo "  -n: Optional. Dry run"
  exit 1
}

# Check if jq is installed
if ! command -v jq &> /dev/null; then
  echo "jq could not be found. Please install jq to run this script."
  exit 1
fi

# Check if yq is installed
if ! command -v yq &> /dev/null; then
  echo "yq could not be found. Please install yq to run this script."
  exit 1
fi

# Parse command line arguments
while getopts "d:a:u:p:qn" opt; do
  case ${opt} in
    d) directory="${OPTARG}" ;;
    a) address="${OPTARG}" ;;
    u) username="${OPTARG}" ;;
    p) password="${OPTARG}" ;;
    q) QUIET=1 ;;
    n) DRY_RUN=1 ;;
    \?) echo "Invalid option: -${OPTARG}" >&2
        usage ;;
  esac
done

# Ensure all required arguments are provided
if [ -z "${directory}" ] || [ -z "${address}" ] || [ -z "${username}" ] || [ -z "${password}" ]; then
  usage
fi

# Function to process docker-compose.yml files
process_docker_compose() {
  local file=$1
  local volume_keys
  local volume_key

  # Extract top-level volume elements with external enabled
  volume_keys=$(yq '.volumes | to_entries | map(select(.value.external == true)) | .[].key' "${file}")

  # Iterate over the volume keys
  for volume_key in volume_keys; do
    local labels

    # Get the label array for the volume
    labels=$(yq ".volumes.${volume_key}.labels" "${file}")

    # If the labels are an empty array, empty, or null, skip
    if [ "${labels}" == "[]" ] || [ -z "${labels}" ] || [ "${labels}" == "null" ]; then
      continue
    fi

    local volume_name
    local share_name

    # Get the volume name
    volume_name=$(yq -r ".volumes.${volume_key}.name" "${file}")

    # Convert the label array to a dictionary
    labels=$(echo "${labels}" | jq -r '. | map(split("=")) | map({(.[0]): .[1]}) | add')

    # If the labels are an empty dictionary, empty, or null, skip
    if [ "${labels}" == "{}" ] || [ -z "${labels}" ] || [ "${labels}" == "null" ]; then
      continue
    fi

    # Check if volume has the specific label "de.panzer1119.docker.volume.cifs.share"
    share_name=$(echo "${labels}" | jq -r '.["de.panzer1119.docker.volume.cifs.share"]')

    # If the share name is null or empty, skip
    if [ -z "${share_name}" ] || [ "${share_name}" == "null" ]; then
      continue
    fi

    # If quiet mode is not enabled, display the volume name and share name
    if [ -z "${QUIET}" ]; then
      echo "Found volume '${volume_name}' with share name '${share_name}' in '${file}'"
    fi

    local command="bash \"${CREATE_CIFS_VOLUME_SCRIPT_FILE}\" -n \"${volume_name}\" -a \"${address}\" -s \"${share_name}\" -u \"${username}\" -p \"${password}\" -e"

    # If quiet mode is enabled, suppress output
    if [ -n "${QUIET}" ]; then
      command="${command} -q"
    fi

    # If dry run is enabled, display the command
    if [ -n "${DRY_RUN}" ]; then
      # If quiet mode is not enabled, display the command
      if [ -z "${QUIET}" ]; then
        echo "${command}"
      fi
      continue
    fi

    # Create the CIFS volume
    if ! eval "${command}"; then
      exit 1
    fi
  done
}

export -f process_docker_compose

# Find and process all docker-compose.yml files
find "${directory}" -type f -name "*docker-compose.yml" -exec bash -c 'process_docker_compose "$0"' {} \;
