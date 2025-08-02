#!/bin/bash

# Default values
SMB_VERSION="3.0"
IO_CHARSET="utf8"
VERBOSE=0
EXIT_GRACEFULLY_IF_EXISTS=0
QUIET=0
QUIET=0

# Function to display usage
usage() {
  echo "Usage: ${0} -n <volume_name> -a <address> -s <share_name> -u <username> -p <password> [-V <smb_version>] [-C <io_charset>] [-i <uid>] [-g <gid>] [-v] [-N] [-e] [-q]"
  echo "  -n <volume_name>: Name of the Docker volume to create"
  echo "  -a <address>: Address of the CIFS/SMB server"
  echo "  -s <share_name>: Name of the shared folder on the server"
  echo "  -u <username>: Username for authentication"
  echo "  -p <password>: Password for authentication"
  echo "  -V <smb_version>: Optional. SMB version (default: 3.0)"
  echo "  -C <io_charset>: Optional. IO charset (default: utf8)"
  echo "  -i <uid>: Optional. UID for file access (default: not set)"
  echo "  -g <gid>: Optional. GID for file access (default: not set)"
  echo "  -v: Optional. Verbose mode"
  echo "  -N: Optional. Dry run"
  echo "  -e: Optional. Exit gracefully if volume already exists (default: exit with error)"
  echo "  -q: Optional. Quiet mode"
  exit 1
}

# Parse options
while getopts "n:a:s:u:p:V:C:i:g:vNeq" opt; do
  case ${opt} in
    n) VOLUME_NAME="${OPTARG}" ;;
    a) ADDRESS="${OPTARG}" ;;
    s) SHARE_NAME="${OPTARG}" ;;
    u) USERNAME="${OPTARG}" ;;
    p) PASSWORD="${OPTARG}" ;;
    V) SMB_VERSION="${OPTARG}" ;;
    C) IO_CHARSET="${OPTARG}" ;;
    i) USER_ID="${OPTARG}" ;;
    g) GROUP_ID="${OPTARG}" ;;
    v) VERBOSE=1 ;;
    N) DRY_RUN=1 ;;
    e) EXIT_GRACEFULLY_IF_EXISTS=1 ;;
    q) QUIET=1 ;;
    \?) echo "Invalid option: -${OPTARG}" >&2
        usage ;;
  esac
done

# Check required options
if [[ -z ${VOLUME_NAME} || -z ${ADDRESS} || -z ${SHARE_NAME} || -z ${USERNAME} || -z ${PASSWORD} ]]; then
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

# Build Docker volume create command
#TODO Add option nobrl?
DOCKER_CMD="docker volume create --driver local --name '${VOLUME_NAME}' --opt type=cifs --opt 'device=//${ADDRESS}/${SHARE_NAME}' --opt 'o=addr=${ADDRESS},username=${USERNAME},password=${PASSWORD},noperm,file_mode=0777,dir_mode=0777,iocharset=${IO_CHARSET},vers=${SMB_VERSION}"

# Add optional parameters if provided
if [[ -n ${USER_ID} ]]; then
  DOCKER_CMD="${DOCKER_CMD},uid=${USER_ID}"
fi

if [[ -n ${GROUP_ID} ]]; then
  DOCKER_CMD="${DOCKER_CMD},gid=${GROUP_ID}"
fi

# Close the options string
DOCKER_CMD="${DOCKER_CMD}'"

# If quiet is disabled, display the message
if [[ ${QUIET} -eq 0 ]]; then
  # If dry run is enabled, display the message
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "Would create Docker CIFS volume '${VOLUME_NAME}' pointing to '${SHARE_NAME}' on '${ADDRESS}'"
  else
    echo "Creating Docker CIFS volume '${VOLUME_NAME}' pointing to '${SHARE_NAME}' on '${ADDRESS}'..."
  fi
fi

# If verbose is enabled, display the docker command
if [[ ${VERBOSE} -eq 1 ]]; then
  echo "Docker command: ${DOCKER_CMD}"
fi

# If dry run is enabled, exit
if [[ ${DRY_RUN} -eq 1 ]]; then
  exit 0
fi

# Execute Docker command
eval "${DOCKER_CMD}"

# Display success message if not in quiet mode
if [[ ${QUIET} -eq 0 ]]; then
  # Check if the volume was created successfully
  if ! docker volume inspect "${VOLUME_NAME}" &> /dev/null; then
    echo "Error: Failed to create Docker Rclone volume '${VOLUME_NAME}'."
    exit 1
  fi
  echo "Docker CIFS volume '${VOLUME_NAME}' created successfully."
fi
