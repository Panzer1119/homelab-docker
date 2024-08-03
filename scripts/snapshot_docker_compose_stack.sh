#!/bin/bash

# Constants
KEY_STACK_NAME="de.panzer1119.docker:stack_name"
KEY_STACK_IMAGE="de.panzer1119.docker:target_image"
KEY_STACK_TAG="de.panzer1119.docker:target_tag"
DEFAULT_SNAPSHOT_PREFIX="stack-checkpoint"
DOCKER_ZFS_PLUGIN_SERVICE_FILE="/etc/systemd/system/docker-zfs-plugin.service"

# Defaults
UP_AFTER=0
DEBUG=0
DRY_RUN=0
VERBOSE=0
QUIET=0

# Help message
usage() {
cat << EOF
Usage: $(basename "${0}") [options]
Snapshot a Docker Compose stack using ZFS.

Options:
  -d, --directory <directory>    Directory containing the Docker Compose stacks (required).
  -n, --name <name>              Name of the Docker Compose stack to snapshot (required).
  -i, --target-image <image>     Image that caused the snapshot.
  -t, --target-tag <tag>         Tag that caused the snapshot.
  -p, --snapshot-prefix <prefix> Prefix for the snapshot name (default: '${DEFAULT_SNAPSHOT_PREFIX}').
  -u, --up-after                 Start the stack after taking the snapshot (default is to keep it stopped).
  -D, --debug                    Debug mode. Print debug information.
  -N, --dry-run                  Dry run. Print the actions that would be taken without actually executing them.
  -v, --verbose                  Verbose mode. Print additional information (also enables logging of debug messages).
  -q, --quiet                    Quiet mode. Only print errors.
  -h, --help                     Display this help message.
EOF
}

# Check if the required commands are available and the required permissions are granted
check_requirements() {
  # Check if this script is executed with root permissions
  if [ "$(id -u)" -ne 0 ]; then
    echo "Error: This script must be executed with root permissions."
    exit 1
  fi

  # Check if the required commands are available
  local commands=("jq" "docker" "zfs")
  for command in "${commands[@]}"; do
    if ! command -v "${command}" &>/dev/null; then
      echo "Error: '${command}' is not installed."
      exit 1
    fi
  done

  # Check if we are allowed to run docker commands
  if ! docker ps &>/dev/null; then
    echo "Error: Unable to run docker commands. Please make sure that the current user is in the 'docker' group."
    exit 1
  fi

  # Check if we are allowed to run zfs commands
  if ! zfs list &>/dev/null; then
    echo "Error: Unable to run zfs commands. Please make sure that the current user is in the 'zfs' group."
    exit 1
  fi
}

# Log a message with the given log level
log() {
  local message="${1}"
  local log_level="${2:-INFO}"

  # Pad the log level to 7 characters
  log_level="$(printf "%-7s" "${log_level}")"

  # Check if the log level is debug and if the debug or the verbose flag is set
  if [ "${log_level}" == "DEBUG" ] && [ "${DEBUG}" -ne 1 ] && [ "${VERBOSE}" -ne 1 ]; then
    return
  fi

  # Check if the log level is verbose and if the verbose flag is set
  if [ "${log_level}" == "VERBOSE" ] && [ "${VERBOSE}" -ne 1 ]; then
    return
  fi

  # Check if the log level is not error and if the quiet flag is set
  if [ "${log_level}" != "ERROR" ] && [ "${QUIET}" -eq 1 ]; then
    return
  fi

  # If the log level is error or warning, print the message to stderr
  if [ "${log_level}" == "ERROR" ] || [ "${log_level}" == "WARNING" ]; then
    echo "[${log_level}] ${message}" >&2
    return
  fi

  # Log the message
  echo "[${log_level}] ${message}"
}

# Get the docker compose file for the given stack
get_docker_compose_file() {
  local stacks_dir="${1}"
  local stack_name="${2}"
  echo "${stacks_dir}/${stack_name}/docker-compose.yml"
}

# Generate the snapshot name for the given stack
generate_snapshot_name() {
  local snapshot_prefix="${1}"

  local timestamp

  # Generate the timestamp
  timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"

  # If the snapshot prefix is empty, use the default prefix
  if [ -z "${snapshot_prefix}" ]; then
    snapshot_prefix="${DEFAULT_SNAPSHOT_PREFIX}"
  fi

  # Generate and return the snapshot name
  echo "${snapshot_prefix}-${timestamp}"
}

# Snapshot the given volume
snapshot_volume() {
  local stack_name="${1}"
  local target_image="${2}"
  local target_tag="${3}"
  local base_dataset="${4}"
  local relative_dataset="${5}"
  local snapshot_name="${6}"

  local snapshot="${base_dataset}/${relative_dataset}@${snapshot_name}"

  # Check if the dry run flag is set
  if [ "${DRY_RUN}" -eq 1 ]; then
    log "[DRY RUN] Would take snapshot '${snapshot}' of docker zfs volume '${relative_dataset}'" "INFO"
    log "[DRY RUN] Would set property '${KEY_STACK_NAME}' to '${stack_name}' for snapshot '${snapshot}'" "VERBOSE"
    [[ -n "${target_image}" ]] && log "[DRY RUN] Would set property '${KEY_STACK_IMAGE}' to '${target_image}' for snapshot '${snapshot}'" "VERBOSE"
    [[ -n "${target_tag}" ]] && log "[DRY RUN] Would set property '${KEY_STACK_TAG}' to '${target_tag}' for snapshot '${snapshot}'" "VERBOSE"
    return
  fi

  # Take a snapshot of the volume
  log "Taking snapshot '${snapshot}' of docker zfs volume '${relative_dataset}'" "INFO"
  zfs snapshot "${snapshot}"

  # Set the snapshot properties
  log "Setting properties for snapshot '${snapshot}'" "VERBOSE"
  zfs set "${KEY_STACK_NAME}=${stack_name}" "${snapshot}"
  [[ -n "${target_image}" ]] && zfs set "${KEY_STACK_IMAGE}=${target_image}" "${snapshot}"
  [[ -n "${target_tag}" ]] && zfs set "${KEY_STACK_TAG}=${target_tag}" "${snapshot}"
}

# Snapshot the volumes of the given stack
snapshot_volumes() {
  local stacks_dir="${1}"
  local stack_name="${2}"
  local target_image="${3}"
  local target_tag="${4}"
  local base_dataset="${5}"
  local snapshot_name="${6}"

  local docker_compose_file
  local docker_compose_json
  local volumes_json
  local volume_array_json
  local relative_dataset_array_json
  local relative_dataset_array

  # Get the docker compose file for the given stack
  docker_compose_file="$(get_docker_compose_file "${stacks_dir}" "${stack_name}")"
  log "Using docker compose file: '${docker_compose_file}'" "DEBUG"

  # Parse the docker compose file as JSON
  docker_compose_json="$(docker compose -f "${docker_compose_file}" config --format json --dry-run)"

  # Get the volumes section of the docker compose file
  volumes_json="$(echo "${docker_compose_json}" | jq -r '.volumes')"
  log "Found $(echo "${volumes_json}" | jq -r 'length') volumes" "DEBUG"

  # Get all volume objects that are using the zfs driver and convert them to an array
  volume_array_json="$(echo "${volumes_json}" | jq -r 'map(select(.driver == "zfs"))')"

  # Get the zfs datasets for the volumes (simply the name of the volume)
  relative_dataset_array_json="$(echo "${volume_array_json}" | jq -r 'map(.name)')"
  log "Found relative zfs datasets: ${relative_dataset_array_json}" "DEBUG"

  # Convert the relative dataset array to a bash array
  mapfile -t relative_dataset_array < <(echo "${relative_dataset_array_json}" | jq -r '.[]')

  # Iterate over the zfs datasets and snapshot them
  log "Snapshotting volumes for stack '${stack_name}' as '${snapshot_name}'" "INFO"
  for relative_dataset in "${relative_dataset_array[@]}"; do
    snapshot_volume "${stack_name}" "${target_image}" "${target_tag}" "${base_dataset}" "${relative_dataset}" "${snapshot_name}"
  done
}

# Get the base zfs dataset of the zfs docker volume plugin
get_base_dataset() {
  local base_dataset

  # Check if the docker zfs plugin service file exists
  if [ ! -f "${DOCKER_ZFS_PLUGIN_SERVICE_FILE}" ]; then
    log "Docker zfs plugin service file not found: '${DOCKER_ZFS_PLUGIN_SERVICE_FILE}'" "ERROR"
    exit 1
  fi

  # Get the base dataset from the docker zfs plugin service file
  base_dataset="$(grep -oP '(?<=--dataset-name )\S+' "${DOCKER_ZFS_PLUGIN_SERVICE_FILE}")"

  # Check if the base dataset is empty
  if [ -z "${base_dataset}" ]; then
    log "Base dataset not found in docker zfs plugin service file: '${DOCKER_ZFS_PLUGIN_SERVICE_FILE}'" "ERROR"
    exit 1
  fi

  # Check if the base dataset exists
  if [ -z "${base_dataset}" ]; then
    log "Base dataset '${base_dataset}' found in docker zfs plugin service file: '${DOCKER_ZFS_PLUGIN_SERVICE_FILE}' does not exist" "ERROR"
    exit 1
  fi

  # Return the base dataset
  echo "${base_dataset}"
}

# Main function
main() {
  # Check requirements
  check_requirements

  local stacks_dir
  local stack_name
  local target_image
  local target_tag
  local snapshot_prefix

  local docker_compose_file
  local snapshot_name

  # Parse options
  while [[ $# -gt 0 ]]; do
    case "${1}" in
      -d|--directory)
        stacks_dir="${2}"
        shift
        ;;
      -n|--name)
        stack_name="${2}"
        shift
        ;;
      -i|--target-image)
        target_image="${2}"
        shift
        ;;
      -t|--target-tag)
        target_tag="${2}"
        shift
        ;;
      -p|--snapshot-prefix)
        snapshot_prefix="${2}"
        shift
        ;;
      -u|--up-after)
        UP_AFTER=1
        ;;
      -D|--debug)
        DEBUG=1
        ;;
      -N|--dry-run)
        DRY_RUN=1
        ;;
      -v|--verbose)
        VERBOSE=1
        ;;
      -q|--quiet)
        QUIET=1
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Error: Invalid option: '${1}'"
        usage
        exit 1
        ;;
    esac
    shift
  done

  # Check if the stacks directory is provided
  if [ -z "${stacks_dir}" ]; then
    log "Stacks directory not provided." "ERROR"
    usage
    exit 1
  fi

  # Check if the stack name is provided
  if [ -z "${stack_name}" ]; then
    log "Stack name not provided." "ERROR"
    usage
    exit 1
  fi

  # If running in dry run mode, print a message
  if [ "${DRY_RUN}" -eq 1 ]; then
    log "Running in dry run mode. No changes will be made." "INFO"
  fi

  # Get the docker compose file for the given stack
  docker_compose_file="$(get_docker_compose_file "${stacks_dir}" "${stack_name}")"

  # If running in verbose mode, print the options
  if [ "${VERBOSE}" -eq 1 ]; then
    log "Options:" "VERBOSE"
    log "Stacks directory: ${stacks_dir}" "VERBOSE"
    log "Stack name: ${stack_name}" "VERBOSE"
    log "Target image: ${target_image}" "VERBOSE"
    log "Target tag: ${target_tag}" "VERBOSE"
    log "Snapshot prefix: ${snapshot_prefix}" "VERBOSE"
    log "Up after: ${UP_AFTER}" "VERBOSE"
    log "Debug: ${DEBUG}" "VERBOSE"
    log "Dry run: ${DRY_RUN}" "VERBOSE"
    log "Verbose: ${VERBOSE}" "VERBOSE"
    log "Quiet: ${QUIET}" "VERBOSE"
    log "Docker compose file: ${docker_compose_file}" "VERBOSE"
  fi

  # Stop the stack (if not in dry run mode)
  if [ "${DRY_RUN}" -eq 1 ]; then
    log "[DRY RUN] Would stop stack '${stack_name}'" "INFO"
  else
    log "Stopping stack '${stack_name}'" "DEBUG"
    docker compose -f "${docker_compose_file}" down

    while docker compose -f "${docker_compose_file}" ps -q | xargs docker inspect --format '{{.State.Status}}' | grep -q "running"; do
      log "Stack '${stack_name}' is still running. Waiting..." "VERBOSE"
      sleep 5
    done
  fi

  # Generate the snapshot name
  snapshot_name="$(generate_snapshot_name "${snapshot_prefix}")"

  # Get the base dataset of the zfs docker volume plugin
  base_dataset="$(get_base_dataset)"

  # Snapshot the volumes of the stack
  snapshot_volumes "${stacks_dir}" "${stack_name}" "${target_image}" "${target_tag}" "${base_dataset}" "${snapshot_name}"

  # Start the stack if the up-after flag is set (if not in dry run mode)
  if [ "${UP_AFTER}" -eq 1 ]; then
    if [ "${DRY_RUN}" -eq 1 ]; then
      log "[DRY RUN] Would start stack '${stack_name}'" "INFO"
    else
      log "Starting stack '${stack_name}'" "DEBUG"
      docker compose -f "${docker_compose_file}" up -d
    fi
  fi
}

# Call the main function
main "${@}"
