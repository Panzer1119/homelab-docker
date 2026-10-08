#!/bin/bash

set -euo pipefail

# Configurable variables
REPO_DIR="$(pwd)"
REMOTE="${REMOTE:-origin}"
BRANCH="${BRANCH:-main}"
TAG_LAST="${TAG:-update/last}"
SKIP_FETCH="${SKIP_FETCH:-false}"

# Check for required tools
command -v yq >/dev/null || { echo "yq is required but not installed." >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required but not installed." >&2; exit 1; }

#TODO How do we handle different containers in the same stack that all gets the same update (like Radarr and Sonarr)?
# Should it create multiple ZFS Snapshots that contain identical data?

main() {
  if [ ! -d "${REPO_DIR}/.git" ]; then
    echo "Not a git repository: ${REPO_DIR}" >&2
    exit 1
  fi

  cd "${REPO_DIR}"
  if [ "${SKIP_FETCH}" != "true" ]; then
    echo "Fetching latest changes from ${REMOTE}/${BRANCH}..." >&2
    git fetch "${REMOTE}" "${BRANCH}" --quiet
    git fetch --force --tags "${REMOTE}" "${BRANCH}" --quiet
  else
    echo "Skipping git fetch as SKIP_FETCH is set to true" >&2
  fi

  LAST_REVISION=$(git rev-parse "${TAG_LAST}" 2>/dev/null || echo "")
  REMOTE_HEAD=$(git rev-parse "${REMOTE}/${BRANCH}")
  REVISIONS=$(git rev-list --reverse "${LAST_REVISION}..${REMOTE_HEAD}")

  [ -z "${REVISIONS}" ] && { echo "No new commits to process." >&2; echo '[]'; exit 0; }

  commit_count=$(git rev-list --count "${LAST_REVISION}..${REMOTE_HEAD}")
  echo "Processing ${commit_count} new commit(s)" >&2
  echo "Oldest: ${LAST_REVISION}" >&2
  echo "Newest: ${REMOTE_HEAD}" >&2

  local full_output="[]"
  for REVISION in ${REVISIONS}; do
    commit_output=$(process_commit "${REVISION}" || echo "")
    if [ -n "${commit_output}" ]; then
      full_output=$(jq -n \
        --argjson existing "${full_output}" \
        --argjson new "${commit_output}" \
        '$existing + [$new]')
    fi
  done

  echo "${full_output}" | jq .
}

process_commit() {
  local REVISION=${1}
  echo "Processing commit ${REVISION}" >&2
  local FILES
  FILES=$(git diff --name-status "${REVISION}^" "${REVISION}" -- . | grep -E 'compose/.*/.*/docker-compose(\.override)?\.ya?ml' || true)

  [ -z "${FILES}" ] && return

  file_count=$(echo "${FILES}" | wc -l)
  echo "Matched ${file_count} docker compose file(s)" >&2

  local project_changes="[]"
  while read -r STATUS FILEPATH; do
    [ -z "${FILEPATH}" ] && continue
    result=$(process_project_file_change "${REVISION}" "${STATUS}" "${FILEPATH}" || echo "")
    if [ -n "${result}" ]; then
      project_changes=$(jq -n \
        --argjson existing "${project_changes}" \
        --argjson new "${result}" \
        '$existing + [$new]')
    else
      echo "Commit ${REVISION} has no docker image updates" >&2
    fi
  done <<< "${FILES}"

  [ "$(echo "${project_changes}" | jq length)" -eq 0 ] && return

  local COMMITTED_AT_EPOCH_SECONDS
  COMMITTED_AT_EPOCH_SECONDS=$(git show -s --format=%ct "${REVISION}")
  local AUTHORED_AT_EPOCH_SECONDS
  AUTHORED_AT_EPOCH_SECONDS=$(git show -s --format=%at "${REVISION}")

  jq -n \
    --arg revision "${REVISION}" \
    --argjson committedAtEpochSeconds "${COMMITTED_AT_EPOCH_SECONDS}" \
    --argjson authoredAtEpochSeconds "${AUTHORED_AT_EPOCH_SECONDS}" \
    --argjson projects "${project_changes}" \
    '{revision: $revision, committedAtEpochSeconds: $committedAtEpochSeconds, authoredAtEpochSeconds: $authoredAtEpochSeconds, projects: $projects}'
}

process_project_file_change() {
  local REVISION=${1}
  local STATUS=${2}
  local FILEPATH=${3}

  local SECTION
  SECTION=$(echo "${FILEPATH}" | cut -d'/' -f2)
  local PROJECT
  PROJECT=$(echo "${FILEPATH}" | cut -d'/' -f3)
  local CHANGE_TYPE
  local OLD_CONTENT="" NEW_CONTENT=""

  # shellcheck disable=SC2034
  declare -A OLD_IMAGES NEW_IMAGES

  case "${STATUS}" in
    A)
      CHANGE_TYPE="created"
      NEW_CONTENT=$(git show "${REVISION}:${FILEPATH}" || true)
      ;;
    D)
      CHANGE_TYPE="deleted"
      OLD_CONTENT=$(git show "${REVISION}^:${FILEPATH}" || true)
      ;;
    M|*)
      CHANGE_TYPE="updated"
      OLD_CONTENT=$(git show "${REVISION}^:${FILEPATH}" || true)
      NEW_CONTENT=$(git show "${REVISION}:${FILEPATH}" || true)
      ;;
  esac

  if [ -n "${OLD_CONTENT}" ]; then
    extract_images_from_compose "${OLD_CONTENT}" "${PROJECT}" OLD_IMAGES
  fi
  if [ -n "${NEW_CONTENT}" ]; then
    extract_images_from_compose "${NEW_CONTENT}" "${PROJECT}" NEW_IMAGES
  fi

  compare_images "${SECTION}" "${PROJECT}" "${CHANGE_TYPE}" OLD_IMAGES NEW_IMAGES
}

extract_images_from_compose() {
  local yaml_content=${1}
  local project=${2}
  local -n ref=${3}

  local services
  services=$(echo "${yaml_content}" | yq -r '.services // {} | to_entries[] | @json | @base64')

  for row in ${services}; do
    _jq() { echo "${row}" | base64 --decode | jq -r "${1}"; }
    name=$(_jq '.key')
    image=$(_jq '.value.image')
    cname=$(_jq '.value.container_name')

    [ "${image}" == "null" ] && continue

    if [ "${cname}" == "null" ]; then
      cname="${project}-${name}-1"
    fi

    # shellcheck disable=SC2034
    ref["${cname}"]="${image}"
  done
}

parse_image() {
  local image_str=${1}
  local -n out_registry=${2}
  local -n out_namespace=${3}
  local -n out_repository=${4}
  local -n out_tag=${5}
  local -n out_digest=${6}

  # Set default values
  out_registry="docker.io"
  out_namespace="library"
  out_repository=""
  out_tag=""
  out_digest=""

  # shellcheck disable=SC2034
  [[ "${image_str}" == *"@"* ]] && out_digest="${image_str##*@}"
  local no_digest="${image_str%%@*}"

  # shellcheck disable=SC2034
  [[ "${no_digest}" == *":"* ]] && out_tag="${no_digest##*:}" || out_tag=""
  local no_tag="${no_digest%%:*}"

  IFS='/' read -r -a parts <<< "${no_tag}"

  # shellcheck disable=SC2034
  if [ "${#parts[@]}" -eq 3 ]; then
    out_registry="${parts[0]}"
    out_namespace="${parts[1]}"
    out_repository="${parts[2]}"
  elif [ "${#parts[@]}" -eq 2 ]; then
    out_namespace="${parts[0]}"
    out_repository="${parts[1]}"
  else
    out_repository="${parts[0]}"
  fi
}

compare_images() {
  local section=${1}
  local project=${2}
  local change_type=${3}
  declare -n old_images=${4}
  declare -n new_images=${5}

  local all_keys
  all_keys=("${!old_images[@]}" "${!new_images[@]}")
  local unique_keys
  mapfile -t unique_keys < <(printf "%s\n" "${all_keys[@]}" | sort -u)

  local containers_json="[]"
  for container in "${unique_keys[@]}"; do
    old="${old_images[${container}]:-}"
    new="${new_images[${container}]:-}"
    [ "${old}" == "${new}" ] && continue

    local updates=()

    local old_registry="docker.io" old_namespace="library" old_repository="" old_tag="" old_digest=""
    local new_registry="docker.io" new_namespace="library" new_repository="" new_tag="" new_digest=""

    if [ -n "${old}" ]; then
      parse_image "${old}" old_registry old_namespace old_repository old_tag old_digest
    fi

    if [ -n "${new}" ]; then
      parse_image "${new}" new_registry new_namespace new_repository new_tag new_digest
    fi

    if [ "${old_registry}" != "${new_registry}" ]; then updates+=("registry"); fi
    if [ "${old_namespace}" != "${new_namespace}" ]; then updates+=("namespace"); fi
    if [ "${old_repository}" != "${new_repository}" ]; then updates+=("repository"); fi
    if [ "${old_tag}" != "${new_tag}" ]; then updates+=("tag"); fi
    if [ "${old_digest}" != "${new_digest}" ]; then updates+=("digest"); fi

    updates_json=$(printf '%s\n' "${updates[@]}" | jq -R . | jq -s .)
    old_image_json=$(jq -n \
      --arg registry "${old_registry}" \
      --arg namespace "${old_namespace}" \
      --arg repository "${old_repository}" \
      --arg tag "${old_tag}" \
      --arg digest "${old_digest}" \
      '{registry: $registry, namespace: $namespace, repository: $repository, tag: $tag, digest: $digest}')
    new_image_json=$(jq -n \
      --arg registry "${new_registry}" \
      --arg namespace "${new_namespace}" \
      --arg repository "${new_repository}" \
      --arg tag "${new_tag}" \
      --arg digest "${new_digest}" \
      '{registry: $registry, namespace: $namespace, repository: $repository, tag: $tag, digest: $digest}')
    containers_json=$(jq -n \
      --arg name "${container}" \
      --arg old "${old}" \
      --arg new "${new}" \
      --argjson changedParts "${updates_json}" \
      --argjson oldImage "${old_image_json}" \
      --argjson newImage "${new_image_json}" \
      '$ARGS.named | {containerName: .name, image: {changedParts: .changedParts, old: .oldImage, new: .newImage}}' | \
      jq --argjson existing "${containers_json}" '$existing + [.]')
  done

  local count
  count=$(echo "${containers_json}" | jq 'length')
  [ "${count}" -eq 0 ] && return

  jq -n \
    --arg sectionName "${section}" \
    --arg projectName "${project}" \
    --arg changeType "${change_type}" \
    --argjson changedImageCount "${count}" \
    --argjson containers "${containers_json}" \
    '{sectionName: $sectionName, projectName: $projectName, changeType: $changeType, changedImageCount: $changedImageCount, containers: $containers}'
}

main
