#!/bin/bash

# Constants
KEY_SECTION_NAME="de.panzer1119.docker:section_name"
KEY_STACK_NAME="de.panzer1119.docker:stack_name"
KEY_TARGET_IMAGE="de.panzer1119.docker:target_image"
KEY_TARGET_TAG="de.panzer1119.docker:target_tag"
KEY_TARGET_SHA256="de.panzer1119.docker:target_sha256"
KEY_GIT_COMMIT_SHA1="de.panzer1119.docker:git_commit_sha1"

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
  -d, --directory <directory>    Directory containing the Docker Compose stacks (default is the current directory).
  -S, --section <section>        Section of Docker Compose stack. If not provided, the directory's name is used and the directory gets set it to its parent.
  -n, --name <name>              Name of the Docker Compose stack to snapshot. If not provided, the directory's name is used and the directory gets set it to its parent.
  -i, --target-image <image>     Image that caused the snapshot.
  -t, --target-tag <tag>         Tag that caused the snapshot.
  -s, --target-sha256 <sha256>   SHA256 that caused the snapshot.
  -C, --commit-sha1 <sha1>       SHA1 of the git commit that caused the snapshot. If not provided, the current git commit is used.
  -c, --target-container <name>  Container that caused the snapshot (use placeholder '-' to use the stack name as the target container).
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
  local commands=("jq" "docker" "zfs" "git")
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
  local directory="${1}"
  local section_name="${2}"
  local stack_name="${3}"
  echo "${directory}/${section_name}/${stack_name}/docker-compose.yml"
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
  local section_name="${1}"
  local stack_name="${2}"
  local target_image="${3}"
  local target_tag="${4}"
  local target_sha256="${5}"
  local commit_sha1="${6}"
  local volume_dataset="${7}"
  local snapshot_name="${8}"

  local snapshot="${volume_dataset}@${snapshot_name}"

  # Check if the dry run flag is set
  if [ "${DRY_RUN}" -eq 1 ]; then
    log "[DRY RUN] Would take snapshot '${snapshot}' of zfs dataset '${volume_dataset}'" "INFO"
    log "[DRY RUN] Would set property '${KEY_SECTION_NAME}' to '${section_name}' for snapshot '${snapshot}'" "DEBUG"
    log "[DRY RUN] Would set property '${KEY_STACK_NAME}' to '${stack_name}' for snapshot '${snapshot}'" "DEBUG"
    [[ -n "${target_image}" ]] && log "[DRY RUN] Would set property '${KEY_TARGET_IMAGE}' to '${target_image}' for snapshot '${snapshot}'" "DEBUG"
    [[ -n "${target_tag}" ]] && log "[DRY RUN] Would set property '${KEY_TARGET_TAG}' to '${target_tag}' for snapshot '${snapshot}'" "DEBUG"
    [[ -n "${target_sha256}" ]] && log "[DRY RUN] Would set property '${KEY_TARGET_SHA256}' to '${target_sha256}' for snapshot '${snapshot}'" "DEBUG"
    [[ -n "${commit_sha1}" ]] && log "[DRY RUN] Would set property '${KEY_GIT_COMMIT_SHA1}' to '${commit_sha1}' for snapshot '${snapshot}'" "DEBUG"
    return
  fi

  # Take a snapshot of the volume
  log "Taking snapshot '${snapshot}' of zfs dataset '${volume_dataset}'" "INFO"
  zfs snapshot "${snapshot}"

  # Set the snapshot properties
  log "Setting property '${KEY_SECTION_NAME}' to '${section_name}' for snapshot '${snapshot}'" "DEBUG"
  zfs set "${KEY_SECTION_NAME}=${section_name}" "${snapshot}"
  log "Setting property '${KEY_STACK_NAME}' to '${stack_name}' for snapshot '${snapshot}'" "DEBUG"
  zfs set "${KEY_STACK_NAME}=${stack_name}" "${snapshot}"
  [[ -n "${target_image}" ]] && log "Setting property '${KEY_TARGET_IMAGE}' to '${target_image}' for snapshot '${snapshot}'" "DEBUG"
  [[ -n "${target_image}" ]] && zfs set "${KEY_TARGET_IMAGE}=${target_image}" "${snapshot}"
  [[ -n "${target_tag}" ]] && log "Setting property '${KEY_TARGET_TAG}' to '${target_tag}' for snapshot '${snapshot}'" "DEBUG"
  [[ -n "${target_tag}" ]] && zfs set "${KEY_TARGET_TAG}=${target_tag}" "${snapshot}"
  [[ -n "${target_sha256}" ]] && log "Setting property '${KEY_TARGET_SHA256}' to '${target_sha256}' for snapshot '${snapshot}'" "DEBUG"
  [[ -n "${target_sha256}" ]] && zfs set "${KEY_TARGET_SHA256}=${target_sha256}" "${snapshot}"
  [[ -n "${commit_sha1}" ]] && log "Setting property '${KEY_GIT_COMMIT_SHA1}' to '${commit_sha1}' for snapshot '${snapshot}'" "DEBUG"
  [[ -n "${commit_sha1}" ]] && zfs set "${KEY_GIT_COMMIT_SHA1}=${commit_sha1}" "${snapshot}"
}

# Extract bind mount volumes from Docker Compose config
extract_volume_datasets() {
  local docker_compose_json="${1}"

  local top_level_volumes_array_json
  local service_volumes_json
  local service_volume_array_json
  local volume_array_json
  local volume_source_array_json
  local volume_dataset_array_json

  # Extract top-level volumes of driver local and with driver_opts "o" of bind
  top_level_volumes_array_json="$(echo "${docker_compose_json}" | jq -r '.volumes // {} | map(select(.driver == "local" and .driver_opts.o == "bind"))')"
#  log "Found $(echo "${volumes_json}" | jq -r 'length') top-level volumes" "DEBUG"

  # Convert the top-level volumes to an array of objects with source (driver_opts.device) (and target set to null)
  top_level_volumes_array_json="$(echo "${top_level_volumes_array_json}" | jq -r 'map({source: .driver_opts.device, target: null})')"

  # Extract service-level volumes of type bind
  service_volumes_json="$(echo "${docker_compose_json}" | jq -r '.services[].volumes[]? | select(.type == "bind")')"
#  log "Found $(echo "${service_volumes_json}" | jq -r 'length') service-level volumes" "VERBOSE"

  # Convert the new line separated volume objects into an json array and preserve only the source and target
  service_volume_array_json="$(echo "${service_volumes_json}" | jq -s '.' | jq -r 'map({source: .source, target: .target})')"
#  log "Converted volume array to json" "VERBOSE"

  # Combine top-level and service-level volumes
  volume_array_json="$(echo "${top_level_volumes_array_json} ${service_volume_array_json}" | jq -s '. | add')"

  # Extract the source of the volumes, sort them and remove duplicates. Remove all relative paths.
  volume_source_array_json="$(echo "${volume_array_json}" | jq -r 'map(.source) | sort | unique')"
#  log "Extracted volume sources" "VERBOSE"

  # Remove all relative paths from the volume sources and remove the leading slash
  volume_dataset_array_json="$(echo "${volume_source_array_json}" | jq -r 'map(select(test("^/"))) | map(ltrimstr("/"))')"
#  log "Removed relative paths from volume sources" "VERBOSE"

  # Return the volume dataset array
  echo "${volume_dataset_array_json}"
}

# Snapshot the volumes of the given stack
snapshot_volumes() {
  local directory="${1}"
  local section_name="${2}"
  local stack_name="${3}"
  local target_image="${4}"
  local target_tag="${5}"
  local target_sha256="${6}"
  local commit_sha1="${7}"
  local snapshot_name="${8}"
  # Take the rest of the arguments as base datasets
  local base_dataset_array=("${@:9}")

  local docker_compose_file
  local docker_compose_json
  local volume_dataset_array_json
  local volume_dataset_array

  # Get the docker compose file for the given stack
  docker_compose_file="$(get_docker_compose_file "${directory}" "${section_name}" "${stack_name}")"
  log "Using docker compose file: '${docker_compose_file}'" "DEBUG"

  # Parse the docker compose file as JSON
  docker_compose_json="$(docker compose -f "${docker_compose_file}" config --format json --dry-run)"
  log "Parsed docker compose file as JSON" "VERBOSE"

  # Extract the volume datasets from the docker compose file
  volume_dataset_array_json="$(extract_volume_datasets "${docker_compose_json}")"
  log "Extracted volume datasets from docker compose file" "VERBOSE"

  # Convert the json volume dataset array to a bash array
  mapfile -t volume_dataset_array < <(echo "${volume_dataset_array_json}" | jq -r '.[]')
  log "Converted volume dataset array to bash array" "VERBOSE"

  # Iterate over the volume datasets and snapshot them
  log "Snapshotting volumes of stack '${stack_name}' as '${snapshot_name}'" "INFO"
  for volume_dataset in "${volume_dataset_array[@]}"; do
    log "Processing volume '${volume_dataset}'" "VERBOSE"
    # Skip if the volume dataset does not start with any of the base datasets
    if ! echo "${volume_dataset}" | grep -qE "^$(IFS=\|; echo "${base_dataset_array[*]}")"; then
      log "Skipping volume '${volume_dataset}' as it does not start with any of the base datasets" "VERBOSE"
      continue
    fi
    snapshot_volume "${section_name}" "${stack_name}" "${target_image}" "${target_tag}" "${target_sha256}" "${commit_sha1}" "${volume_dataset}" "${snapshot_name}"
  done
}

# Main function
main() {
  # Check requirements
  check_requirements

  local directory
  local section_name
  local stack_name
  local target_image
  local target_tag
  local target_sha256
  local commit_sha1
  local target_container
  local snapshot_prefix
  local base_dataset_array

  local docker_compose_file
  local snapshot_name

  # Parse options
  while [[ $# -gt 0 ]]; do
    case "${1}" in
      -d|--directory)
        directory="${2}"
        shift
        ;;
      -S|--section)
        section_name="${2}"
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
      -s|--target-sha256)
        target_sha256="${2}"
        shift
        ;;
      -C|--commit-sha1)
        commit_sha1="${2}"
        shift
        ;;
      -c|--target-container)
        target_container="${2}"
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

  # If running in dry run mode, print a message
  if [ "${DRY_RUN}" -eq 1 ]; then
    log "Running in dry run mode. No changes will be made." "INFO"
  fi

  # If the directory is empty, set it to the current directory
  if [ -z "${directory}" ]; then
    directory="$(pwd)"
    log "Directory not provided. Using current directory: '${directory}'" "DEBUG"
  fi

  # Get the absolute path of the directory
  directory="$(realpath "${directory}")"

  # Check if the directory exists
  if [ ! -d "${directory}" ]; then
    echo "Error: Directory not found at '${directory}'"
    exit 1
  fi

  # If the stack name is not provided, set it to the directory name and set the directory to its parent
  if [ -z "${stack_name}" ]; then
    stack_name="$(basename "${directory}")"
    directory="$(dirname "${directory}")"
    log "Stack name not provided. Using directory name: '${stack_name}'" "DEBUG"
    log "Setting directory to parent directory: '${directory}'" "DEBUG"
  fi

  # If the section name is not provided, set it to the directory name and set the directory to its parent
  if [ -z "${section_name}" ]; then
    section_name="$(basename "${directory}")"
    directory="$(dirname "${directory}")"
    log "Section name not provided. Using directory name: '${section_name}'" "DEBUG"
    log "Setting directory to parent directory: '${directory}'" "DEBUG"
  fi

  # If the target container is provided, check if it is the placeholder '-'
  if [ "${target_container}" == "-" ]; then
    target_container="${stack_name}"
    log "Using stack name as target container: '${target_container}'" "DEBUG"
  fi

  # Get the docker compose file for the given stack
  docker_compose_file="$(get_docker_compose_file "${directory}" "${section_name}" "${stack_name}")"

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

  # If the git commit sha1 is empty, get the current git commit sha1
  if [ -z "${commit_sha1}" ]; then
    log "Extracting git commit sha1 from the current git commit" "DEBUG"
    commit_sha1="$(git rev-parse HEAD)"
    log "Extracted git commit sha1: ${commit_sha1}" "VERBOSE"
  fi

  # If a target container is provided, check if it exists
  if [ -n "${target_container}" ]; then
    log "Checking if target container '${target_container}' exists" "VERBOSE"
    if ! docker ps -a --format '{{.Names}}' | grep -q "^${target_container}$"; then
      echo "Error: Target container '${target_container}' not found"
      exit 1
    fi
  fi

  # If a target container is provided, extract the target image, tag and sha256 (if missing)
  if [ -n "${target_container}" ]; then
    log "Extracting target image, tag, and sha256 from container '${target_container}'" "DEBUG"
    # Get the image, tag, and sha256 of the target container
    local docker_image
    docker_image="$(docker inspect --format '{{.Config.Image}}' "${target_container}")"
    # Extract the docker image sha256 if present
    local docker_image_sha256=""
    if [[ "${docker_image}" =~ @sha256:([a-f0-9]+) ]]; then
      docker_image_sha256="${BASH_REMATCH[1]}"
      log "Extracted image sha256: ${docker_image_sha256}" "VERBOSE"
      # Remove the sha256 part from the docker image
      docker_image="${docker_image%@sha256:*}"
    fi
    # Extract the docker image tag if present
    local docker_image_tag="latest"
    if [[ "${docker_image}" =~ :([^:]+)$ ]]; then
      docker_image_tag="${BASH_REMATCH[1]}"
      log "Extracted image tag: ${docker_image_tag}" "VERBOSE"
      # Remove the tag part from the docker image
      docker_image="${docker_image%:*}"
    fi
    # Extract docker image repository, user, and name
    local docker_image_repository=""
    local docker_image_user=""
    local docker_image_name="${docker_image}"
    if [[ "${docker_image}" =~ / ]]; then
      local repo_part="${docker_image%%/*}"
      local rest="${docker_image#*/}"
      # If the repo part contains a dot or a colon, it is the repository, else it is the user
      if [[ "${repo_part}" =~ [.:] ]]; then
        docker_image_repository="${repo_part}"
        if [[ "${rest}" =~ / ]]; then
          docker_image_user="${rest%%/*}"
          docker_image_name="${rest#*/}"
        else
          docker_image_user=""
          docker_image_name="${rest}"
        fi
      else
        docker_image_user="${repo_part}"
        docker_image_name="${rest}"
      fi
      log "Extracted image repository: ${docker_image_repository}" "VERBOSE"
      log "Extracted image user: ${docker_image_user}" "VERBOSE"
      log "Extracted image name: ${docker_image_name}" "VERBOSE"
    fi
    # If docker image repository is empty, set it to "docker.io"
    if [ -z "${docker_image_repository}" ]; then
      docker_image_repository="docker.io"
    fi
    # If docker image user is empty, set it to "_"
    if [ -z "${docker_image_user}" ]; then
      docker_image_user="_"
    fi
    # If no target image is provided, use the extracted image repository, user, and name
    if [ -z "${target_image}" ]; then
      target_image="${docker_image_repository}/${docker_image_user}/${docker_image_name}"
    fi
    # If no target tag is provided, use the extracted image tag
    if [ -z "${target_tag}" ]; then
      target_tag="${docker_image_tag}"
    fi
    # If no target sha256 is provided, use the extracted image sha256
    if [ -z "${target_sha256}" ]; then
      target_sha256="${docker_image_sha256}"
    fi
  fi

  # If running in verbose mode, print the options
  if [ "${VERBOSE}" -eq 1 ]; then
    log "Options:" "VERBOSE"
    log "Directory: ${directory}" "VERBOSE"
    log "Section name: ${section_name}" "VERBOSE"
    log "Stack name: ${stack_name}" "VERBOSE"
    log "Target image: ${target_image}" "VERBOSE"
    log "Target tag: ${target_tag}" "VERBOSE"
    log "Target sha256: ${target_sha256}" "VERBOSE"
    log "Git commit sha1: ${commit_sha1}" "VERBOSE"
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

    local container_ids
    while true; do
        # Get the container ids of the stack
        container_ids="$(docker compose -f "${docker_compose_file}" ps -q)"

        # If no container ids are returned, the stack is stopped
        if [ -z "${container_ids}" ]; then
            break
        fi

        # Check if the containers are still running
        # shellcheck disable=SC2086
        if docker inspect --format '{{.State.Status}}' ${container_ids} | grep -q "running"; then
            log "Stack '${stack_name}' is still running. Waiting..." "VERBOSE"
            sleep 5
        else
            break
        fi
    done
    log "Stack '${stack_name}' is stopped" "DEBUG"
  fi

  # Generate the snapshot name
  snapshot_name="$(generate_snapshot_name "${snapshot_prefix}")"
  log "Using snapshot name: '${snapshot_name}'" "DEBUG"

  # Snapshot the volumes of the stack
  snapshot_volumes "${directory}" "${section_name}" "${stack_name}" "${target_image}" "${target_tag}" "${target_sha256}" "${commit_sha1}" "${snapshot_name}" "${base_dataset_array[@]}"

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
