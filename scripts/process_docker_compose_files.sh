#!/bin/bash

CREATE_CIFS_VOLUME_SCRIPT_FILENAME="create_docker_cifs_volume.sh"
CREATE_SSHFS_VOLUME_SCRIPT_FILENAME="create_docker_sshfs_volume.sh"

SCRIPTS_DIR=$(dirname "${0}")
CREATE_CIFS_VOLUME_SCRIPT_FILE="${SCRIPTS_DIR}/${CREATE_CIFS_VOLUME_SCRIPT_FILENAME}"
CREATE_SSHFS_VOLUME_SCRIPT_FILE="${SCRIPTS_DIR}/${CREATE_SSHFS_VOLUME_SCRIPT_FILENAME}"

DIRECTORY=""
VERBOSE=0
QUIET=0
DRY_RUN=0
DELETE=0

DEFAULT_CIFS_HOST=""
DEFAULT_CIFS_USERNAME=""
DEFAULT_CIFS_PASSWORD=""

DEFAULT_SSHFS_PORT=22

# Function to display usage
usage() {
  cat << EOF
Usage: ${0} [-v] [-q] [-n] [-D] <directory>
  <directory>: Directory to recursively search for docker-compose.yml files
  -v: Optional. Verbose mode
  -q: Optional. Quiet mode
  -n: Optional. Dry run
  -D: Optional. Delete the CIFS/SSHFS volumes (only requires -d)
EOF
  exit 1
}

# Check for required commands
for cmd in jq yq op; do
  if ! command -v "${cmd}" &> /dev/null; then
    echo "Error: ${cmd} is not installed. Please install ${cmd} to run this script."
    exit 1
  fi
done

# Parse command line arguments
while getopts "vqnD" opt; do
  case ${opt} in
    v) VERBOSE=1 ;;
    q) QUIET=1 ;;
    n) DRY_RUN=1 ;;
    D) DELETE=1 ;;
    \?) echo "Invalid option: -${OPTARG}" >&2
        usage ;;
  esac
done
shift $((OPTIND -1))

# Directory to search
DIRECTORY="$1"

# Ensure directory is provided
if [ -z "${DIRECTORY}" ]; then
  usage
fi

# Validate directory
if [ ! -d "${DIRECTORY}" ]; then
    echo "Directory not found: ${DIRECTORY}"
    exit 1
fi

load_defaults() {
  DEFAULT_CIFS_HOST=$(op read "op://Docker/Default/CIFS/Host")
  DEFAULT_CIFS_USERNAME=$(op read "op://Docker/Default/CIFS/Username")
  DEFAULT_CIFS_PASSWORD=$(op read "op://Docker/Default/CIFS/Password")
}

# Function to check if a value is empty or null
check_value() {
  local value="${1}"
  local name="${2}"
  if [ -z "${value}" ] || [ "${value}" == "null" ]; then
    echo "Error: ${name} is required for volume '${volume_name}' in '${file}'"
    exit 1
  fi
}

create_cifs_volume() {
  local volume_name="${1}"
  local volume_dictionaries="${2}"

  local host share username password

  # Get the host, share, username, and password from the volume dictionaries
  host=$(echo "${volume_dictionaries}" | jq -r ".[\"${volume_name}\"].host")
  share=$(echo "${volume_dictionaries}" | jq -r ".[\"${volume_name}\"].share")
  username=$(echo "${volume_dictionaries}" | jq -r ".[\"${volume_name}\"].username")
  password=$(echo "${volume_dictionaries}" | jq -r ".[\"${volume_name}\"].password")

  # If the host is empty or null, use the default host
  [ -z "${host}" ] || [ "${host}" == "null" ] && host="${DEFAULT_CIFS_HOST}"

  # If the username is empty or null, use the default username
  [ -z "${username}" ] || [ "${username}" == "null" ] && username="${DEFAULT_CIFS_USERNAME}"

  # If the password is empty or null, use the default password
  [ -z "${password}" ] || [ "${password}" == "null" ] && password="${DEFAULT_CIFS_PASSWORD}"

  # If all values are empty or null, skip
  if { [ -z "${host}" ] || [ "${host}" == "null" ]; } &&
     { [ -z "${share}" ] || [ "${share}" == "null" ]; } &&
     { [ -z "${username}" ] || [ "${username}" == "null" ]; } &&
     { [ -z "${password}" ] || [ "${password}" == "null" ]; }; then
    return
  fi

  # Check each required CIFS value
  check_value "${host}" "CIFS Share host"
  check_value "${share}" "CIFS Share name"
  check_value "${username}" "CIFS Username"
  check_value "${password}" "CIFS Password"

  # If quiet mode is not enabled, display the volume name and share name
  [ "${QUIET}" -eq 0 ] && echo "Found CIFS volume '${volume_name}' with share name '${share}' in '${file}'"

  # Build the command to create the CIFS volume
  local command=("bash" "${CREATE_CIFS_VOLUME_SCRIPT_FILE}" "-n" "${volume_name}" "-a" "${host}" "-s" "${share}" "-u" "${username}" "-p" "${password}" "-e")

  # Add verbose option if enabled
  [ "${VERBOSE}" -eq 1 ] && command+=("-v")

  # Add dry run option if enabled
  [ "${DRY_RUN}" -eq 1 ] && command+=("-N")

  # Add quiet option if enabled
  [ "${QUIET}" -eq 1 ] && command+=("-q")

  # If verbose is enabled, display the command
  if [ "${VERBOSE}" -eq 1 ]; then
    echo "${command[*]}"
  fi

#  # If dry run is enabled, return
#  if [ "${DRY_RUN}" -eq 1 ]; then
#    return
#  fi

  # Create the CIFS volume
  if ! "${command[@]}"; then
    exit 1
  fi
}

create_sshfs_volume() {
  local volume_name="${1}"
  local volume_dictionaries="${2}"

  local host port path username password

  # Get the host, path, username, and password from the volume dictionaries
  host=$(echo "${volume_dictionaries}" | jq -r ".[\"${volume_name}\"].host")
  port=$(echo "${volume_dictionaries}" | jq -r ".[\"${volume_name}\"].port")
  path=$(echo "${volume_dictionaries}" | jq -r ".[\"${volume_name}\"].path")
  username=$(echo "${volume_dictionaries}" | jq -r ".[\"${volume_name}\"].username")
  password=$(echo "${volume_dictionaries}" | jq -r ".[\"${volume_name}\"].password")

  # If the port is empty or null, use the default ssh port
  [ -z "${port}" ] || [ "${port}" == "null" ] && port="${DEFAULT_SSHFS_PORT}"

  # If all values are empty or null, skip
  if { [ -z "${host}" ] || [ "${host}" == "null" ]; } &&
     { [ -z "${port}" ] || [ "${port}" == "null" ]; } &&
     { [ -z "${path}" ] || [ "${path}" == "null" ]; } &&
     { [ -z "${username}" ] || [ "${username}" == "null" ]; } &&
     { [ -z "${password}" ] || [ "${password}" == "null" ]; }; then
    return
  fi

  # Check each required SSHFS value
  check_value "${host}" "SSHFS Host"
  check_value "${port}" "SSHFS Port"
  check_value "${path}" "SSHFS Path"
  check_value "${username}" "SSHFS Username"
  check_value "${password}" "SSHFS Password"

  # If quiet mode is not enabled, display the volume name and path
  [ "${QUIET}" -eq 0 ] && echo "Found SSHFS volume '${volume_name}' with path '${path}' in '${file}'"

  # Build the command to create the SSHFS volume
  local command=("bash" "${CREATE_SSHFS_VOLUME_SCRIPT_FILE}" "-n" "${volume_name}" "-a" "${host}" "-p" "${port}" "-s" "${path}" "-u" "${username}" "-P" "${password}" "-e")

  # Add verbose option if enabled
  [ "${VERBOSE}" -eq 1 ] && command+=("-v")

  # Add dry run option if enabled
  [ "${DRY_RUN}" -eq 1 ] && command+=("-N")

  # Add quiet option if enabled
  [ "${QUIET}" -eq 1 ] && command+=("-q")

  # If verbose is enabled, display the command
  if [ "${VERBOSE}" -eq 1 ]; then
    echo "${command[*]}"
  fi

#  # If dry run is enabled, return
#  if [ "${DRY_RUN}" -eq 1 ]; then
#    return
#  fi

  # Create the SSHFS volume
  if ! "${command[@]}"; then
    exit 1
  fi
}

# Function to process docker-compose.yml files
process_docker_compose() {
  local file="${1}"
  local services service_keys

  # Extract top-level services element
  services=$(yq '.services' "${file}")

  # If the services element is empty or null, skip
  [ -z "${services}" ] || [ "${services}" == "null" ] && return

  # Extract top-level service elements with labels defined (ignore stderr)
  service_keys=$(yq '.services | to_entries | map(select(.value.labels)) | map(.key) | .[]' "${file}" 2>/dev/null)

  # If no service keys are found or the service keys array is empty, skip
  [ -z "${service_keys}" ] || [ "${service_keys}" == "[]" ] && return

  # Iterate over the service keys
  for service_key in ${service_keys}; do
    local labels volume_labels volume_dictionaries

    # Get the labels array for the service
    labels=$(yq ".services.${service_key}.labels" "${file}")

    # If the labels are an empty array, empty, or null, skip
    [ "${labels}" == "[]" ] || [ -z "${labels}" ] || [ "${labels}" == "null" ] && continue

    # Convert the labels array to a dictionary
    labels=$(echo "${labels}" | jq -r '. | map(split("=")) | map({(.[0]): .[1]}) | add')

    # If the labels are an empty dictionary, empty, or null, skip
    if [ "${labels}" == "{}" ] || [ -z "${labels}" ] || [ "${labels}" == "null" ]; then
      continue
    fi

    # Filter all labels starting with "de.panzer1119.docker.volume."
    volume_labels=$(echo "${labels}" | jq -r 'to_entries | map(select(.key | startswith("de.panzer1119.docker.volume."))) | from_entries')

    # Iterate over the volume labels and replace the values with the secrets
    for key in $(echo "${volume_labels}" | jq -r 'keys[]'); do
      value=$(echo "${volume_labels}" | jq -r ".[\"${key}\"]")
      if [[ "${value}" == "op://"* ]]; then
        volume_labels=$(echo "${volume_labels}" | jq -r ".[\"${key}\"] |= \"$(op read "${value}")\"")
      fi
    done

    # Create an empty dictionary for the volume dictionaries
    volume_dictionaries="{}"

    # Iterate over the volume label keys
    for volume_label_key in $(echo "${volume_labels}" | jq -r 'keys[]'); do
      local volume_label_value volume_name volume_driver key

      # Get the volume label value
      volume_label_value=$(echo "${volume_labels}" | jq -r ".[\"${volume_label_key}\"]")

      # If the volume label value is empty or null, skip
      [ -z "${volume_label_value}" ] || [ "${volume_label_value}" == "null" ] && continue

      # Extract the volume name from the volume label key (the first part after "de.panzer1119.docker.volume.")
      volume_name=$(echo "${volume_label_key}" | sed -E 's/^de\.panzer1119\.docker\.volume\.(.*)\.(cifs|sshfs).(host|port|share|username|password)$/\1/')

      # If the volume name is empty or null, skip
      [ -z "${volume_name}" ] || [ "${volume_name}" == "null" ] && continue

      # Extract the driver from the volume label key (the second part after "de.panzer1119.docker.volume.")
      volume_driver=$(echo "${volume_label_key}" | sed -E 's/^de\.panzer1119\.docker\.volume\.(.*)\.(cifs|sshfs).(host|port|share|username|password)$/\2/')

      # Create a dictionary with the volume name as the key and another dictionary with the key driver
      volume_dictionaries=$(echo "${volume_dictionaries}" | jq -r ".[\"${volume_name}\"] |= . + {\"driver\": \"${volume_driver}\"}")

      # Extract the key from the volume label key (the second part after "de.panzer1119.docker.volume.")
      key=$(echo "${volume_label_key}" | sed -E 's/^de\.panzer1119\.docker\.volume\.(.*)\.(cifs|sshfs).(host|port|share|username|password)$/\3/')

      # If the key is empty or null, skip
      [ -z "${key}" ] || [ "${key}" == "null" ] && continue

      # Create a dictionary with the volume name as the key and another dictionary with the key as the key and the volume label value as the value
      volume_dictionaries=$(echo "${volume_dictionaries}" | jq -r ".[\"${volume_name}\"] |= . + {\"${key}\": \"${volume_label_value}\"}")
    done

    # Iterate over the volume dictionaries keys
    for volume_name in $(echo "${volume_dictionaries}" | jq -r 'keys[]'); do
      local driver

      # If delete mode is enabled, delete the volume
      if [ "${DELETE}" -eq 1 ]; then
        # If quiet mode is not enabled, display the volume name
        [ "${QUIET}" -eq 0 ] && echo "Found volume '${volume_name}' in '${file}'"
        # Check if the volume exists
        if ! docker volume inspect "${volume_name}" &> /dev/null; then
          # If quiet mode is not enabled, display a message
          [ "${QUIET}" -eq 0 ] && echo "Skipping volume '${volume_name}' as it does not exist"
          continue
        fi
        # If dry run is enabled, display a message
        if [ "${DRY_RUN}" -eq 1 ]; then
          [ "${QUIET}" -eq 0 ] && echo "Would delete volume '${volume_name}'"
          continue
        else
          # If quiet mode is not enabled, display a message
          [ "${QUIET}" -eq 0 ] && echo "Deleting volume '${volume_name}'..."
        fi
        # Delete the volume
        if ! docker volume rm "${volume_name}"; then
          echo "Error: Failed to delete volume '${volume_name}'"
          exit 1
        fi
        continue
      fi

      # Get the driver from the volume dictionaries
      driver=$(echo "${volume_dictionaries}" | jq -r ".[\"${volume_name}\"].driver")

      # Switch on the driver
      case "${driver}" in
        cifs)
          create_cifs_volume "${volume_name}" "${volume_dictionaries}"
          ;;
        sshfs)
          create_sshfs_volume "${volume_name}" "${volume_dictionaries}"
          ;;
        *)
          echo "Error: Unsupported driver '${driver}' for volume '${volume_name}' in '${file}'"
          exit 1
          ;;
      esac
    done
  done
}

export -f process_docker_compose

# Find all docker-compose.yml files
files=$(find "${DIRECTORY}" -type f -name "*docker-compose.yml")

# If no files are found, exit
[ -z "${files}" ] && exit 0

# Load the default values
load_defaults

# Process each docker-compose.yml file
for file in ${files}; do
  process_docker_compose "${file}"
done
