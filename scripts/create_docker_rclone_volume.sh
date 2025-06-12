#!/bin/bash

DOCKER_VOLUME_PLUGIN_RCLONE_CONFIG_DIR="/var/lib/docker-plugins/rclone/config"

# Default values
DOCKER_VOLUME_NAME=""
RCLONE_TYPE=""
RCLONE_HOST=""
RCLONE_PORT=""
RCLONE_PATH=""
RCLONE_USERNAME=""
RCLONE_PASSWORD=""
RCLONE_SSH_KEY_FILE=""
RCLONE_SSH_KEY_FILE_INNER_PATH=""
VERBOSE=0
DRY_RUN=0
EXIT_GRACEFULLY_IF_EXISTS=0
QUIET=0

# Check for the existence of the rclone plugin
if ! docker plugin inspect rclone &> /dev/null; then
  echo "Error: rclone plugin not found. Please install the rclone plugin before creating a volume."
  exit 1
fi

# Function to display usage
usage() {
cat << EOF
Usage: ${0} -n <volume_name> -t <type> -h <host> [-p <port>] [-s <path>] [-u <username>] [-P <password>] [-S <ssh-key-file>] [-v] [-N] [-e] [-q]
Create a Docker volume using the Rclone plugin.
  -n <volume_name>: Name of the Docker volume to create
  -t <type>: Type of the Rclone remote (e.g. sftp)
  -h <host>: Hostname or IP address of the Rclone remote
  -p <port>: Optional. Port number of the Rclone remote
  -s <path>: Optional. Path of the Rclone remote
  -u <username>: Optional. Username for authentication
  -P <password>: Optional. Password for authentication
  -S <ssh-key-file>: Optional. Path to the SSH key file for authentication
  -v: Optional. Verbose mode
  -N: Optional. Dry run
  -e: Optional. Exit gracefully if volume already exists (default: exit with error)
  -q: Optional. Quiet mode
EOF
}

# Exit if not root
if [[ ${EUID} -ne 0 ]]; then
  echo "Error: This script must be run as root."
  exit 1
fi

# Parse options
while getopts "n:t:h:p:s:u:P:S:vNeq" opt; do
  case ${opt} in
    n) DOCKER_VOLUME_NAME="${OPTARG}" ;;
    t) RCLONE_TYPE="${OPTARG}" ;;
    h) RCLONE_HOST="${OPTARG}" ;;
    p) RCLONE_PORT="${OPTARG}" ;;
    s) RCLONE_PATH="${OPTARG}" ;;
    u) RCLONE_USERNAME="${OPTARG}" ;;
    P) RCLONE_PASSWORD="${OPTARG}" ;;
    S) RCLONE_SSH_KEY_FILE="${OPTARG}" ;;
    v) VERBOSE=1 ;;
    N) DRY_RUN=1 ;;
    e) EXIT_GRACEFULLY_IF_EXISTS=1 ;;
    q) QUIET=1 ;;
    \?) echo "Invalid option: -${OPTARG}" >&2
        usage ;;
  esac
done

# Check required options
if [[ -z "${DOCKER_VOLUME_NAME}" || -z "${RCLONE_TYPE}" ]]; then
  echo "Error: Missing required options."
  usage
fi

# Check if the rclone type is valid (only allow sftp for now)
if [[ "${RCLONE_TYPE}" != "sftp" ]]; then
  echo "Error: Invalid rclone type '${RCLONE_TYPE}'. Only 'sftp' is currently supported."
  exit 1
fi

# Check if volume already exists
if docker volume inspect "${DOCKER_VOLUME_NAME}" &> /dev/null; then
  if [[ ${EXIT_GRACEFULLY_IF_EXISTS} -eq 1 ]]; then
    #echo "Docker volume '${DOCKER_VOLUME_NAME}' already exists. Exiting gracefully."
    exit 0
  else
    echo "Error: Docker volume '${DOCKER_VOLUME_NAME}' already exists."
    exit 1
  fi
fi

# Process SSH key file if provided
if [[ -n "${RCLONE_SSH_KEY_FILE}" ]]; then
  # Check if the SSH key file exists
  if [[ ! -f "${RCLONE_SSH_KEY_FILE}" ]]; then
    echo "Error: SSH key file '${RCLONE_SSH_KEY_FILE}' does not exist."
    exit 1
  fi

  # Set the rclone config volume directory
  rclone_config_volume_dir="${DOCKER_VOLUME_PLUGIN_RCLONE_CONFIG_DIR}/${DOCKER_VOLUME_NAME}"
  # Get the SSH key file name
  rclone_ssh_key_file_name=$(basename "${RCLONE_SSH_KEY_FILE}")
  # Set the full path for the SSH key file in the rclone config volume directory
  rclone_config_volume_ssh_key_file="${rclone_config_volume_dir}/${rclone_ssh_key_file_name}"
  # Set the inner path for the SSH key file (inside the Rclone docker volume plugin container)
  RCLONE_SSH_KEY_FILE_INNER_PATH="/data/config/${DOCKER_VOLUME_NAME}/${rclone_ssh_key_file_name}"

  # Create the rclone config directory for the volume if it does not exist
  if [[ ! -d "${rclone_config_volume_dir}" ]]; then
    # If dry run is enabled, display the message
    if [[ ${DRY_RUN} -eq 1 ]]; then
      # If quiet is disabled, display the message
      if [[ ${QUIET} -eq 0 ]]; then
        echo "Would create Docker Rclone volume directory '${rclone_config_volume_dir}'"
      fi
    else
      # If verbose is enabled and quiet disabled, display the rclone config part
      if [ "${VERBOSE}" -eq 1 ] && [[ ${QUIET} -eq 0 ]]; then
        echo "Creating Docker Rclone volume directory '${rclone_config_volume_dir}'..."
      fi
      mkdir -p "${rclone_config_volume_dir}"
    fi
  fi

  # Remove the SSH key file if it already exists in the rclone config directory
  if [[ -f "${rclone_config_volume_ssh_key_file}" ]]; then
    # If dry run is enabled, display the message
    if [[ ${DRY_RUN} -eq 1 ]]; then
      # If quiet is disabled, display the message
      if [[ ${QUIET} -eq 0 ]]; then
        echo "Would remove existing SSH key file '${rclone_config_volume_ssh_key_file}'"
      fi
    else
      # If verbose is enabled and quiet disabled, display the rclone config part
      if [ "${VERBOSE}" -eq 1 ] && [[ ${QUIET} -eq 0 ]]; then
        echo "Removing existing SSH key file '${rclone_config_volume_ssh_key_file}'..."
      fi
      rm -f "${rclone_config_volume_ssh_key_file}"
    fi
  fi

  # If dry run is enabled, display the message
  if [[ ${DRY_RUN} -eq 1 ]]; then
    # If quiet is disabled, display the message
    if [[ ${QUIET} -eq 0 ]]; then
      echo "Would copy SSH key file '${RCLONE_SSH_KEY_FILE}' to '${rclone_config_volume_ssh_key_file}'"
      echo "Would set permissions for SSH key file '${rclone_config_volume_ssh_key_file}' to 600"
    fi
  else
    # If verbose is enabled and quiet disabled, display the rclone config part
    if [ "${VERBOSE}" -eq 1 ] && [[ ${QUIET} -eq 0 ]]; then
      echo "Copying SSH key file '${RCLONE_SSH_KEY_FILE}' to '${rclone_config_volume_ssh_key_file}'..."
    fi
    # Copy the SSH key file to the rclone config directory
    cp "${RCLONE_SSH_KEY_FILE}" "${rclone_config_volume_ssh_key_file}"

    # If verbose is enabled and quiet disabled, display the rclone config part
    if [ "${VERBOSE}" -eq 1 ] && [[ ${QUIET} -eq 0 ]]; then
      echo "Setting permissions for SSH key file '${rclone_config_volume_ssh_key_file}' to 600..."
    fi
    # Set the permissions for the SSH key file
    chmod 600 "${rclone_config_volume_ssh_key_file}"
  fi
fi

# Build Docker volume create command
DOCKER_CMD="docker volume create --driver rclone --name '${DOCKER_VOLUME_NAME}'"

# Add the type option if provided
if [[ -n ${RCLONE_TYPE} ]]; then
  DOCKER_CMD="${DOCKER_CMD} --opt 'type=${RCLONE_TYPE}'"
fi

# Add the sftp host option if provided
if [[ -n ${RCLONE_HOST} ]]; then
  DOCKER_CMD="${DOCKER_CMD} --opt 'sftp-host=${RCLONE_HOST}'"
fi

# Add the sftp port option if provided
if [[ -n "${RCLONE_PORT}" ]]; then
  DOCKER_CMD="${DOCKER_CMD} --opt 'sftp-port=${RCLONE_PORT}'"
fi

# Add the sftp username option if provided
if [[ -n "${RCLONE_USERNAME}" ]]; then
  DOCKER_CMD="${DOCKER_CMD} --opt 'sftp-user=${RCLONE_USERNAME}'"
fi

# Add the sftp password option if provided
if [[ -n "${RCLONE_PASSWORD}" ]]; then
  DOCKER_CMD="${DOCKER_CMD} --opt 'sftp-pass=${RCLONE_PASSWORD}'"
fi

# Add the SSH key file option if provided
if [[ -n "${RCLONE_SSH_KEY_FILE_INNER_PATH}" ]]; then
  DOCKER_CMD="${DOCKER_CMD} --opt 'sftp-key-file=${RCLONE_SSH_KEY_FILE_INNER_PATH}'"
fi

# Add the path option if provided
if [[ -n "${RCLONE_PATH}" ]]; then
  DOCKER_CMD="${DOCKER_CMD} --opt 'path=${RCLONE_PATH}'"
fi

# If quiet is disabled, display the message
if [[ ${QUIET} -eq 0 ]]; then
  # If dry run is enabled, display the message
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "Would create Docker Rclone volume '${DOCKER_VOLUME_NAME}'"
  else
    echo "Creating Docker Rclone volume '${DOCKER_VOLUME_NAME}'..."
  fi
fi

# If verbose is enabled, display the rclone config part
if [ "${VERBOSE}" -eq 1 ]; then
  echo "Docker command: ${DOCKER_CMD}"
fi

# If dry run is enabled, exit
if [ "${DRY_RUN}" -eq 1 ]; then
  exit 0
fi

# Execute Docker command
eval "${DOCKER_CMD}"

# Display success message if not in quiet mode
if [[ ${QUIET} -eq 0 ]]; then
  echo "Docker Rclone volume '${DOCKER_VOLUME_NAME}' created successfully."
fi
