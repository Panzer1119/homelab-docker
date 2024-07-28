#!/bin/bash

DOCKER_VOLUME_PLUGIN_RCLONE_CONFIG_FILE="/var/lib/docker-plugins/rclone/config/rclone.conf"

# Default values
VOLUME_NAME=""
TYPE=""
HOST=""
PORT=""
USERNAME=""
PASSWORD=""
VERBOSE=0
DRY_RUN=0
EXIT_GRACEFULLY_IF_EXISTS=0
QUIET=0

# Check for the existence of the rclone plugin
if ! docker plugin inspect rclone &> /dev/null; then
  echo "Error: rclone plugin not found. Please install the rclone plugin before creating a volume."
  exit 1
fi

#TODO Make it accept any key value pair options for the rclone config file

# Function to display usage
usage() {
cat << EOF
Usage: ${0} -n <volume_name> -t <type> -h <host> [-p <port>] [-u <username>] [-P <password>] [-v] [-N] [-e] [-q]
Create a Docker volume using the Rclone plugin.
  -n <volume_name>: Name of the Docker volume to create
  -t <type>: Type of the Rclone remote (e.g. sftp)
  -h <host>: Hostname or IP address of the Rclone remote
  -p <port>: Optional. Port number of the Rclone remote
  -u <username>: Optional. Username for authentication
  -P <password>: Optional. Password for authentication
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
while getopts "n:t:h:p:u:P:vNeq" opt; do
  case ${opt} in
    n) VOLUME_NAME="${OPTARG}" ;;
    t) TYPE="${OPTARG}" ;;
    h) HOST="${OPTARG}" ;;
    p) PORT="${OPTARG}" ;;
    u) USERNAME="${OPTARG}" ;;
    P) PASSWORD="${OPTARG}" ;;
    v) VERBOSE=1 ;;
    N) DRY_RUN=1 ;;
    e) EXIT_GRACEFULLY_IF_EXISTS=1 ;;
    q) QUIET=1 ;;
    \?) echo "Invalid option: -${OPTARG}" >&2
        usage ;;
  esac
done

# Check required options
if [[ -z "${VOLUME_NAME}" || -z "${TYPE}" || -z "${HOST}" ]]; then
  echo "Error: Missing required options."
  usage
fi

# Check if volume already exists
if docker volume inspect "${VOLUME_NAME}" &> /dev/null; then
  if [[ ${EXIT_GRACEFULLY_IF_EXISTS} -eq 1 ]]; then
    #echo "Docker volume '${VOLUME_NAME}' already exists. Exiting gracefully."
    exit 0
  else
    echo "Error: Docker volume '${VOLUME_NAME}' already exists."
    exit 1
  fi
fi

# Check if the rclone config file exists and if so check if the remote already exists
if [[ -f "${DOCKER_VOLUME_PLUGIN_RCLONE_CONFIG_FILE}" ]]; then
  if grep -q "\[${VOLUME_NAME}\]" "${DOCKER_VOLUME_PLUGIN_RCLONE_CONFIG_FILE}"; then
    if [[ ${EXIT_GRACEFULLY_IF_EXISTS} -eq 1 ]]; then
      echo "Rclone remote '${VOLUME_NAME}' already exists. Exiting gracefully."
      exit 0
    else
      echo "Error: Rclone remote '${VOLUME_NAME}' already exists."
      exit 1
    fi
  fi
fi

# Build the rclone config file part
RCLONE_CONFIG_PART="[${VOLUME_NAME}]"

# Add the type
RCLONE_CONFIG_PART="${RCLONE_CONFIG_PART}\ntype = ${TYPE}"

# Add the host if provided
if [[ -n "${HOST}" ]]; then
  RCLONE_CONFIG_PART="${RCLONE_CONFIG_PART}\nhost = ${HOST}"
fi

# Add the port if provided
if [[ -n "${PORT}" ]]; then
  RCLONE_CONFIG_PART="${RCLONE_CONFIG_PART}\nport = ${PORT}"
fi

# Add the username if provided
if [[ -n "${USERNAME}" ]]; then
  RCLONE_CONFIG_PART="${RCLONE_CONFIG_PART}\nuser = ${USERNAME}"
fi

# Add the password if provided
if [[ -n "${PASSWORD}" ]]; then
  RCLONE_CONFIG_PART="${RCLONE_CONFIG_PART}\npass = ${PASSWORD}"
fi

# If quiet is disabled, display the message
if [[ ${QUIET} -eq 0 ]]; then
  # If dry run is enabled, display the message
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "Would add Rclone remote '${VOLUME_NAME}' to the rclone config file."
  else
    echo "Adding Rclone remote '${VOLUME_NAME}' to the rclone config file."
  fi
fi

# If verbose is enabled, display the rclone config part
if [ "${VERBOSE}" -eq 1 ]; then
  echo "Rclone config part:"
  echo -e "${RCLONE_CONFIG_PART}"
fi

# If dry run is enabled, exit
if [ "${DRY_RUN}" -eq 1 ]; then
  exit 0
fi
# Add the rclone config part to the rclone config file
echo -e "${RCLONE_CONFIG_PART}" >> "${DOCKER_VOLUME_PLUGIN_RCLONE_CONFIG_FILE}"

# Check if the rclone config file part was added successfully
if ! grep -q "\[${VOLUME_NAME}\]" "${DOCKER_VOLUME_PLUGIN_RCLONE_CONFIG_FILE}"; then
  echo "Error: Failed to add Rclone remote '${VOLUME_NAME}' to the rclone config file."
  exit 1
fi

# Display success message if not in quiet mode
if [[ ${QUIET} -eq 0 ]]; then
  echo "Rclone remote '${VOLUME_NAME}' added successfully to the rclone config file."
fi
