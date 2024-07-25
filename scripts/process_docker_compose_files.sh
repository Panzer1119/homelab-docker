#!/bin/bash

CREATE_CIFS_VOLUME_SCRIPT_FILENAME="create_docker_cifs_volume.sh"

CREATE_CIFS_VOLUME_SCRIPT_FILE="${PWD}/${CREATE_CIFS_VOLUME_SCRIPT_FILENAME}"
QUIET=0
DRY_RUN=0
DELETE=0

# Function to display usage
usage() {
  echo "Usage: ${0} -d <directory> -a <address> -u <username> -p <password> [-q]"
  echo "  -d <directory>: Directory to recursively search for docker-compose.yml files"
  echo "  -a <address>: Address of the CIFS/SMB server"
  echo "  -u <username>: Username for authentication"
  echo "  -p <password>: Password for authentication"
  echo "  -q: Optional. Quiet mode"
  echo "  -n: Optional. Dry run"
  echo "  -D: Optional. Delete the CIFS volumes"
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
while getopts "d:a:u:p:qnD" opt; do
  case ${opt} in
    d) directory="${OPTARG}" ;;
    a) address="${OPTARG}" ;;
    u) username="${OPTARG}" ;;
    p) password="${OPTARG}" ;;
    q) QUIET=1 ;;
    n) DRY_RUN=1 ;;
    D) DELETE=1 ;;
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
  local volumes
  local volume_keys
  local volume_key

  # Extract top-level volumes element
  volumes=$(yq '.volumes' "${file}")

  # If the volumes element is empty or null, skip
  if [ -z "${volumes}" ] || [ "${volumes}" == "null" ]; then
    return
  fi

  # Extract top-level volume elements with external enabled (ignore stderr)
  volume_keys=$(yq '.volumes | to_entries | map(select(.value.external)) | map(.key) | .[]' "${file}" 2>/dev/null)

  # If no volume keys are found or the volume keys array is empty, skip
  if [ -z "${volume_keys}" ] || [ "${volume_keys}" == "[]" ]; then
    return
  fi

  # Iterate over the volume keys
  for volume_key in ${volume_keys}; do
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
    if [[ "${QUIET}" -eq 0 ]]; then
      echo "Found volume '${volume_name}' with share name '${share_name}' in '${file}'"
    fi

    # If delete mode is enabled, delete the volume
    if [[ "${DELETE}" -eq 1 ]]; then
      echo "Deleting volume '${volume_name}'..."
      if ! docker volume rm "${volume_name}"; then
        echo "Error: Failed to delete volume '${volume_name}'"
        exit 1
      fi
      continue
    fi

    # Build the command to create the CIFS volume
    local command="bash \"${CREATE_CIFS_VOLUME_SCRIPT_FILE}\" -n \"${volume_name}\" -a \"${address}\" -s \"${share_name}\" -u \"${username}\" -p \"${password}\" -e"

    # If quiet mode is enabled, suppress output
    if [[ "${QUIET}" -eq 1 ]]; then
      command="${command} -q"
    fi

    # If dry run is enabled, display the command
    if [[ "${DRY_RUN}" -eq 1 ]]; then
      # If quiet mode is not enabled, display the command
      if [[ "${QUIET}" -eq 0 ]]; then
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

# Find all docker-compose.yml files
files=$(find "${directory}" -type f -name "*docker-compose.yml")

# Process each docker-compose.yml file
for file in ${files}; do
  process_docker_compose "${file}"
done
