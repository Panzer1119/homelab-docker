#!/bin/bash

CREATE_CIFS_VOLUME_SCRIPT_FILENAME="create_docker_cifs_volume.sh"

SCRIPTS_DIR=$(dirname "${0}")
CREATE_CIFS_VOLUME_SCRIPT_FILE="${SCRIPTS_DIR}/${CREATE_CIFS_VOLUME_SCRIPT_FILENAME}"

DIRECTORY=""
QUIET=0
DRY_RUN=0
DELETE=0

# Function to display usage
usage() {
  cat << EOF
Usage: ${0} -d <directory> [-q] [-n] [-D]
  -d <directory>: Directory to recursively search for docker-compose.yml files
  -q: Optional. Quiet mode
  -n: Optional. Dry run
  -D: Optional. Delete the CIFS volumes (only requires -d)
EOF
  exit 1
}

# Check for required commands
for cmd in jq yq; do
  if ! command -v "${cmd}" &> /dev/null; then
    echo "Error: ${cmd} is not installed. Please install ${cmd} to run this script."
    exit 1
  fi
done

# Parse command line arguments
while getopts "d:qnD" opt; do
  case ${opt} in
    d) DIRECTORY=${OPTARG} ;;
    q) QUIET=1 ;;
    n) DRY_RUN=1 ;;
    D) DELETE=1 ;;
    \?) echo "Invalid option: -${OPTARG}" >&2
        usage ;;
  esac
done

# Ensure directory is provided
if [ -z "${DIRECTORY}" ]; then
  usage
fi

# Function to process docker-compose.yml files
process_docker_compose() {
  local file=$1
  local volumes
  local volume_keys

  # Extract top-level volumes element
  volumes=$(yq '.volumes' "${file}")

  # If the volumes element is empty or null, skip
  [ -z "${volumes}" ] || [ "${volumes}" == "null" ] && return

  # Extract top-level volume elements with external enabled (ignore stderr)
  volume_keys=$(yq '.volumes | to_entries | map(select(.value.external)) | map(.key) | .[]' "${file}" 2>/dev/null)

  # If no volume keys are found or the volume keys array is empty, skip
  [ -z "${volume_keys}" ] || [ "${volume_keys}" == "[]" ] && return

  # Iterate over the volume keys
  for volume_key in ${volume_keys}; do
    local labels volume_name cifs_host cifs_share cifs_username cifs_password

    # Get the label array for the volume
    labels=$(yq ".volumes.${volume_key}.labels" "${file}")

    # If the labels are an empty array, empty, or null, skip
    [ "${labels}" == "[]" ] || [ -z "${labels}" ] || [ "${labels}" == "null" ] && continue

    # Get the volume name
    volume_name=$(yq -r ".volumes.${volume_key}.name" "${file}")

    # If quiet mode is not enabled, display the volume name and share name
    [ "${QUIET}" -eq 0 ] && echo "Found CIFS volume '${volume_name}' with share name '${cifs_share}' in '${file}'"

    # If delete mode is enabled, delete the volume
    if [ "${DELETE}" -eq 1 ]; then
      # Check if the volume exists
      if ! docker volume inspect "${volume_name}" &> /dev/null; then
        echo "Volume '${volume_name}' does not exist. Skipping deletion..."
        continue
      fi
      # Delete the volume
      echo "Deleting volume '${volume_name}'..."
      if ! docker volume rm "${volume_name}"; then
        echo "Error: Failed to delete volume '${volume_name}'"
        exit 1
      fi
      continue
    fi

    # Convert the label array to a dictionary
    labels=$(echo "${labels}" | jq -r '. | map(split("=")) | map({(.[0]): .[1]}) | add')

    # If the labels are an empty dictionary, empty, or null, skip
    if [ "${labels}" == "{}" ] || [ -z "${labels}" ] || [ "${labels}" == "null" ]; then
      continue
    fi

    # Extract CIFS details from labels
    cifs_host=$(echo "${labels}" | jq -r '.["de.panzer1119.docker.volume.cifs.host"]')
    cifs_share=$(echo "${labels}" | jq -r '.["de.panzer1119.docker.volume.cifs.share"]')
    cifs_username=$(echo "${labels}" | jq -r '.["de.panzer1119.docker.volume.cifs.username"]')
    cifs_password=$(echo "${labels}" | jq -r '.["de.panzer1119.docker.volume.cifs.password"]')

    # If all cifs values are empty or null, skip
    if [ -z "${cifs_host}" ] || [ "${cifs_host}" == "null" ] || [ -z "${cifs_share}" ] || [ "${cifs_share}" == "null" ] || [ -z "${cifs_username}" ] || [ "${cifs_username}" == "null" ] || [ -z "${cifs_password}" ] || [ "${cifs_password}" == "null" ]; then
      continue
    fi

    # If the cifs host is null or empty, throw an error
    if [ -z "${cifs_host}" ] || [ "${cifs_host}" == "null" ]; then
      echo "Error: Share host is required for volume '${volume_name}' in '${file}'"
      exit 1
    fi

    # If the cifs share is null or empty, throw an error
    if [ -z "${cifs_share}" ] || [ "${cifs_share}" == "null" ]; then
      echo "Error: Share name is required for volume '${volume_name}' in '${file}'"
      exit 1
    fi

    # If the cifs username is null or empty, throw an error
    if [ -z "${cifs_username}" ] || [ "${cifs_username}" == "null" ]; then
      echo "Error: Username is required for volume '${volume_name}' in '${file}'"
      exit 1
    fi

    # If the cifs password is null or empty, throw an error
    if [ -z "${cifs_password}" ] || [ "${cifs_password}" == "null" ]; then
      echo "Error: Password is required for volume '${volume_name}' in '${file}'"
      exit 1
    fi

    # Use op to retrieve values if needed
    for var in cifs_host cifs_share cifs_username cifs_password; do
      [[ "${!var}" == *"op://"* ]] && eval "${var}=$(op read "${!var}")"
    done

    # Build the command to create the CIFS volume
    local command=("bash" "${CREATE_CIFS_VOLUME_SCRIPT_FILE}" "-n" "${volume_name}" "-a" "${cifs_host}" "-s" "${cifs_share}" "-u" "${cifs_username}" "-p" "${cifs_password}" "-e")

    # Add quiet option if enabled
    [ "${QUIET}" -eq 1 ] && command+=("-q")

    # If dry run is enabled, display the command
    if [ "${DRY_RUN}" -eq 1 ]; then
      [ "${QUIET}" -eq 0 ] && echo "${command[*]}"
      continue
    fi

    # Create the CIFS volume
    if ! "${command[@]}"; then
      exit 1
    fi
  done
}

export -f process_docker_compose

# Find all docker-compose.yml files
files=$(find "${DIRECTORY}" -type f -name "*docker-compose.yml")

# Process each docker-compose.yml file
for file in ${files}; do
  process_docker_compose "${file}"
done
