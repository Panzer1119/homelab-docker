#!/bin/bash

# Default values
PORT="22"
PASSWORD=""
IDENTITY_FILE=""
VERBOSE=0
EXIT_GRACEFULLY_IF_EXISTS=0
QUIET=0
DRY_RUN=0

# Always fail the script, because the plugin is deprecated and dangerous (can lead to data loss, see: https://github.com/vieux/docker-volume-sshfs/issues/81)
echo "Error: The vieux/sshfs plugin is deprecated and dangerous. See https://github.com/vieux/docker-volume-sshfs/issues/81 for more information."
exit 1

# Check for the existence of the vieux/sshfs plugin
if ! docker plugin inspect vieux/sshfs &> /dev/null; then
  echo "Error: The vieux/sshfs plugin is not installed. Please install it before using this script."
  exit 1
fi

# Function to display usage
usage() {
  echo "Usage: ${0} -n <volume_name> -a <address> -s <path> -u <username> [-p <port>] [-P <password>] [-i <file>] [-v] [-N] [-e] [-q]"
  echo "  -n <volume_name>: Name of the Docker volume to create"
  echo "  -a <address>: Address of the SSHFS server"
  echo "  -p <port>: Port of the SSHFS server (default: 22)"
  echo "  -s <path>: Path on the server"
  echo "  -u <username>: Username for authentication"
  echo "  -P <password>: Optional. Password for authentication"
  echo "  -i <file>: Optional. Identity file for SSH authentication"
  echo "  -v: Optional. Verbose mode"
  echo "  -N: Optional. Dry run"
  echo "  -e: Optional. Exit gracefully if volume already exists (default: exit with error)"
  echo "  -q: Optional. Quiet mode"
  exit 1
}

# Parse options
while getopts "n:a:p:s:u:P:i:vNeq" opt; do
  case ${opt} in
    n) VOLUME_NAME="${OPTARG}" ;;
    a) ADDRESS="${OPTARG}" ;;
    p) PORT="${OPTARG}" ;;
    s) SHARE_NAME="${OPTARG}" ;;
    u) USERNAME="${OPTARG}" ;;
    P) PASSWORD="${OPTARG}" ;;
    i) IDENTITY_FILE="${OPTARG}" ;;
    v) VERBOSE=1 ;;
    N) DRY_RUN=1 ;;
    e) EXIT_GRACEFULLY_IF_EXISTS=1 ;;
    q) QUIET=1 ;;
    \?) echo "Invalid option: -${OPTARG}" >&2
        usage ;;
  esac
done

# Check required options
if [[ -z ${VOLUME_NAME} || -z ${ADDRESS} || -z ${SHARE_NAME} || -z ${USERNAME} ]]; then
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

# Build the ssh command
SSH_CMD="${USERNAME}@${ADDRESS}:${SHARE_NAME}"

# Build Docker volume create command
DOCKER_CMD="docker volume create --driver vieux/sshfs --name '${VOLUME_NAME}' --opt 'sshcmd=${SSH_CMD}'"

# Add optional parameters if provided
if [[ -n ${PASSWORD} ]]; then
  DOCKER_CMD="${DOCKER_CMD} --opt 'password=${PASSWORD}'"
fi

if [[ -n ${IDENTITY_FILE} ]]; then
  DOCKER_CMD="${DOCKER_CMD} --opt 'IdentityFile=${IDENTITY_FILE}'"
fi

# If quiet is disabled, display the message
if [[ ${QUIET} -eq 0 ]]; then
  # If dry run is enabled, display the message
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "Would create Docker SSHFS volume '${VOLUME_NAME}' pointing to '${SHARE_NAME}' on '${ADDRESS}'"
  else
    echo "Creating Docker SSHFS volume '${VOLUME_NAME}' pointing to '${SHARE_NAME}' on '${ADDRESS}'..."
  fi
fi

# If verbose is enabled, display the docker command
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
  echo "Docker SSHFS volume '${VOLUME_NAME}' created successfully."
fi
