#!/bin/bash

# Constants
KEY_STACK_NAME="de.panzer1119.docker:stack_name"
KEY_STACK_IMAGE="de.panzer1119.docker:target_image"
KEY_STACK_TAG="de.panzer1119.docker:target_tag"

# Default values
DEFAULT_SNAPSHOT_PREFIX="stack-checkpoint"

# Variables
UP_AFTER=0
DEBUG=0
DRY_RUN=0
VERBOSE=0
QUIET=0

# Help message
usage() {
cat << EOF
Usage: $(basename "${0}") [options]
Snapshot bind mount volumes of a Docker Compose stack using ZFS.

Options:
  -d, --directory <directory>    Directory containing the Docker Compose stacks (required).
  -n, --name <name>              Name of the Docker Compose stack to snapshot (required).
  -i, --target-image <image>     Image that caused the snapshot.
  -t, --target-tag <tag>         Tag that caused the snapshot.
  -p, --snapshot-prefix <prefix> Prefix for the snapshot name (default: '${DEFAULT_SNAPSHOT_PREFIX}').
  -u, --up-after                 Start the stack after taking the snapshot (default is to keep it stopped).
  -D, --debug                    Debug mode. Print debug information.
  -N, --dry-run                  Dry run. Print actions without executing them.
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
    echo "Error: Unable to run docker commands. Ensure the current user has access."
    exit 1
  fi

  # Check if we are allowed to run zfs commands
  if ! zfs list &>/dev/null; then
    echo "Error: Unable to run zfs commands. Ensure the current user has access."
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

  # Generate and return the snapshot name
  echo "${snapshot_prefix}-${timestamp}"
}

# Snapshot the given volume
snapshot_volume() {
  local stack_name="${1}"
  local target_image="${2}"
  local target_tag="${3}"
  local volume_dataset="${4}"
  local snapshot_name="${5}"

  local snapshot="${volume_dataset}@${snapshot_name}"

  # Check if the dry run flag is set
  if [ "${DRY_RUN}" -eq 1 ]; then
    log "[DRY RUN] Would take snapshot '${snapshot}' of zfs dataset '${volume_dataset}'" "INFO"
    log "[DRY RUN] Would set property '${KEY_STACK_NAME}' to '${stack_name}' for snapshot '${snapshot}'" "DEBUG"
    [[ -n "${target_image}" ]] && log "[DRY RUN] Would set property '${KEY_STACK_IMAGE}' to '${target_image}' for snapshot '${snapshot}'" "DEBUG"
    [[ -n "${target_tag}" ]] && log "[DRY RUN] Would set property '${KEY_STACK_TAG}' to '${target_tag}' for snapshot '${snapshot}'" "DEBUG"
    return
  fi

  # Take a snapshot of the volume
  log "Taking snapshot '${snapshot}' of zfs dataset '${volume_dataset}'" "INFO"
  zfs snapshot "${snapshot}"

  # Set the snapshot properties
  log "Setting property '${KEY_STACK_NAME}' to '${stack_name}' for snapshot '${snapshot}'" "DEBUG"
  zfs set "${KEY_STACK_NAME}=${stack_name}" "${snapshot}"
  [[ -n "${target_image}" ]] && log "Setting property '${KEY_STACK_IMAGE}' to '${target_image}' for snapshot '${snapshot}'" "DEBUG"
  [[ -n "${target_image}" ]] && zfs set "${KEY_STACK_IMAGE}=${target_image}" "${snapshot}"
  [[ -n "${target_tag}" ]] && log "Setting property '${KEY_STACK_TAG}' to '${target_tag}' for snapshot '${snapshot}'" "DEBUG"
  [[ -n "${target_tag}" ]] && zfs set "${KEY_STACK_TAG}=${target_tag}" "${snapshot}"
}

# Extract bind mount volumes from Docker Compose config
extract_volume_datasets() {
  local docker_compose_json="${1}"

#  local top_level_volumes_json
  local service_volumes_json
  local volumes_json
  local volume_array_json
  local volume_source_array_json
  local volume_dataset_array_json

#  # Extract top-level volumes
#  top_level_volumes_json="$(echo "${docker_compose_json}" | jq -r '.volumes // {}')"
#  log "Found $(echo "${volumes_json}" | jq -r 'length') top-level volumes" "DEBUG"

  # Extract service-level volumes of type bind
  service_volumes_json="$(echo "${docker_compose_json}" | jq -r '.services[].volumes[]? | select(.type == "bind")')"
  log "Found $(echo "${service_volumes_json}" | jq -r 'length') service-level volumes" "DEBUG"

  # Combine top-level and service-level volumes
  volumes_json="${service_volumes_json}"
  log "Found $(echo "${volumes_json}" | jq -r 'length') total volumes" "DEBUG"

  # Convert the new line separated volume objects into an json array and preserve only the source and target
  volume_array_json="$(echo "${volumes_json}" | jq -s '.' | jq -r 'map({source: .source, target: .target})')"

  # Extract the source of the volumes, sort them and remove duplicates. Remove all relative paths.
  volume_source_array_json="$(echo "${volume_array_json}" | jq -r 'map(.source) | sort | unique')"

  # Remove all relative paths from the volume sources and remove the leading slash
  volume_dataset_array_json="$(echo "${volume_source_array_json}" | jq -r 'map(select(test("^/"))) | map(ltrimstr("/"))')"

  # Return the volume dataset array
  echo "${volume_dataset_array_json}"
}

# Snapshot the volumes of the given stack
snapshot_volumes() {
  local stacks_dir="${1}"
  local stack_name="${2}"
  local target_image="${3}"
  local target_tag="${4}"
  local snapshot_name="${5}"
  # Take the rest of the arguments as base datasets
  local base_dataset_array=("${@:6}")

  local docker_compose_file
  local docker_compose_json
  local volume_dataset_array_json
  local volume_dataset_array

  # Get the docker compose file for the given stack
  docker_compose_file="$(get_docker_compose_file "${stacks_dir}" "${stack_name}")"
  log "Using docker compose file: '${docker_compose_file}'" "DEBUG"

  # Parse the docker compose file as JSON
  docker_compose_json="$(docker compose -f "${docker_compose_file}" config --format json --dry-run)"

  # Extract the volume datasets from the docker compose file
  volume_dataset_array_json="$(extract_volume_datasets "${docker_compose_json}")"

  # Convert the json volume dataset array to a bash array
  mapfile -t volume_dataset_array < <(echo "${volume_dataset_array_json}" | jq -r '.[]')

  # Iterate over the volume datasets and snapshot them
  log "Snapshotting volumes of stack '${stack_name}' as '${snapshot_name}'" "INFO"
  for volume_dataset in "${volume_dataset_array[@]}"; do
    # Skip if the volume dataset does not start with any of the base datasets
    if ! echo "${volume_dataset}" | grep -qE "^$(IFS=\|; echo "${base_dataset_array[*]}")"; then
      log "Skipping volume '${volume_dataset}' as it does not start with any of the base datasets" "VERBOSE"
      continue
    fi
    snapshot_volume "${stack_name}" "${target_image}" "${target_tag}" "${volume_dataset}" "${snapshot_name}"
  done
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
  local base_dataset_array

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
        echo "Error: Invalid option '${1}'"
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

  # Check if the Docker Compose file exists
  if [ ! -f "${docker_compose_file}" ]; then
    echo "Error: Docker Compose file not found at '${docker_compose_file}'"
    exit 1
  fi

  # If the snapshot prefix is empty, use the default prefix
  if [ -z "${snapshot_prefix}" ]; then
    snapshot_prefix="${DEFAULT_SNAPSHOT_PREFIX}"
  fi

  # If the base datasets are empty, use the default base datasets
  if [ "${#base_dataset_array[@]}" -eq 0 ]; then
    base_dataset_array=("docker/config" "docker/data")
  fi

  # If running in verbose mode, print the options
  if [ "${VERBOSE}" -eq 1 ]; then
    log "Options:" "VERBOSE"
    log "Stacks directory: ${stacks_dir}" "VERBOSE"
    log "Stack name: ${stack_name}" "VERBOSE"
    log "Target image: ${target_image}" "VERBOSE"
    log "Target tag: ${target_tag}" "VERBOSE"
    log "Snapshot prefix: ${snapshot_prefix}" "VERBOSE"
    log "Base datasets: ${base_dataset_array[*]}" "VERBOSE"
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
  log "Using snapshot name: '${snapshot_name}'" "DEBUG"

  # Snapshot the volumes of the stack
  snapshot_volumes "${stacks_dir}" "${stack_name}" "${target_image}" "${target_tag}" "${snapshot_name}" "${base_dataset_array[@]}"

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
