#!/bin/bash

set -euo pipefail

# Configurable variables
REPO_DIR="$(pwd)"
REMOTE="${REMOTE:-origin}"
BRANCH="${BRANCH:-main}"

# Check for required tools
command -v yq >/dev/null || { echo "yq is required but not installed." >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required but not installed." >&2; exit 1; }

main() {
  if [ ! -d "${REPO_DIR}/.git" ]; then
    echo "Not a git repository: ${REPO_DIR}" >&2
    exit 1
  fi

  cd "${REPO_DIR}"
  git fetch "${REMOTE}" "${BRANCH}" --quiet

  LOCAL_HEAD=$(git rev-parse HEAD)
  REMOTE_HEAD=$(git rev-parse "${REMOTE}/${BRANCH}")
  COMMITS=$(git rev-list --reverse "${LOCAL_HEAD}..${REMOTE_HEAD}")

  [ -z "${COMMITS}" ] && { echo "No new commits to process."; exit 0; }

  commit_count=$(git rev-list --count "${LOCAL_HEAD}..${REMOTE_HEAD}")
  echo "Processing ${commit_count} new commit(s)" >&2

  local full_output="[]"
  for COMMIT in ${COMMITS}; do
    commit_output=$(process_commit "${COMMIT}" || echo "")
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
  local COMMIT=${1}
  echo "Processing commit ${COMMIT}" >&2
  local FILES
  FILES=$(git diff --name-status "${COMMIT}^" "${COMMIT}" | grep -E 'compose/.*/.*/docker-compose\.ya?ml' || true)

  [ -z "${FILES}" ] && return

  file_count=$(echo "${FILES}" | wc -l)
  echo "Matched ${file_count} docker compose file(s)" >&2

  local project_changes="[]"
  while read -r STATUS FILEPATH; do
    [ -z "${FILEPATH}" ] && continue
    result=$(process_project_file_change "${COMMIT}" "${STATUS}" "${FILEPATH}" || echo "")
    if [ -n "${result}" ]; then
      project_changes=$(jq -n \
        --argjson existing "${project_changes}" \
        --argjson new "${result}" \
        '$existing + [$new]')
    else
      echo "Commit ${COMMIT} has no docker image updates" >&2
    fi
  done <<< "${FILES}"

  [ "$(echo "${project_changes}" | jq length)" -eq 0 ] && return

  jq -n --arg sha "${COMMIT}" --argjson projects "${project_changes}" \
    '{commit: $sha, projects: $projects}'
}

process_project_file_change() {
  local COMMIT=${1}
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
      NEW_CONTENT=$(git show "${COMMIT}:${FILEPATH}" || true)
      ;;
    D)
      CHANGE_TYPE="deleted"
      OLD_CONTENT=$(git show "${COMMIT}^:${FILEPATH}" || true)
      ;;
    M|*)
      CHANGE_TYPE="updated"
      OLD_CONTENT=$(git show "${COMMIT}^:${FILEPATH}" || true)
      NEW_CONTENT=$(git show "${COMMIT}:${FILEPATH}" || true)
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
  services=$(echo "${yaml_content}" | yq -r '.services // {} | to_entries[] | @base64')

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
  local -n out_repo=${2}
  local -n out_user=${3}
  local -n out_image=${4}
  local -n out_tag=${5}
  local -n out_sha=${6}

  # Set default values
  out_repo="docker.io"
  out_user="library"
  out_image=""
  out_tag=""
  out_sha=""

  # shellcheck disable=SC2034
  [[ "${image_str}" == *"@"* ]] && out_sha="${image_str##*@}"
  local no_sha="${image_str%%@*}"

  # shellcheck disable=SC2034
  [[ "${no_sha}" == *":"* ]] && out_tag="${no_sha##*:}" || out_tag=""
  local no_tag="${no_sha%%:*}"

  IFS='/' read -r -a parts <<< "${no_tag}"

  # shellcheck disable=SC2034
  if [ "${#parts[@]}" -eq 3 ]; then
    out_repo="${parts[0]}"
    out_user="${parts[1]}"
    out_image="${parts[2]}"
  elif [ "${#parts[@]}" -eq 2 ]; then
    out_user="${parts[0]}"
    out_image="${parts[1]}"
  else
    out_image="${parts[0]}"
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

    local old_repo="docker.io" old_user="library" old_image="" old_tag="" old_sha=""
    local new_repo="docker.io" new_user="library" new_image="" new_tag="" new_sha=""

    if [ -n "${old}" ]; then
      parse_image "${old}" old_repo old_user old_image old_tag old_sha
    fi

    if [ -n "${new}" ]; then
      parse_image "${new}" new_repo new_user new_image new_tag new_sha
    fi

    if [ "${old_repo}" != "${new_repo}" ]; then updates+=("repo"); fi
    if [ "${old_user}" != "${new_user}" ]; then updates+=("user"); fi
    if [ "${old_image}" != "${new_image}" ]; then updates+=("image"); fi
    if [ "${old_tag}" != "${new_tag}" ]; then updates+=("tag"); fi
    if [ "${old_sha}" != "${new_sha}" ]; then updates+=("sha"); fi

    updates_json=$(printf '%s\n' "${updates[@]}" | jq -R . | jq -s .)
    containers_json=$(jq -n \
      --arg name "${container}" \
      --arg old "${old}" \
      --arg new "${new}" \
      --argjson changes "${updates_json}" \
      '$ARGS.named | {container_name: .name, old_image: .old, new_image: .new, update_types: $changes}' | \
      jq --argjson existing "${containers_json}" '$existing + [.]')
  done

  local count
  count=$(echo "${containers_json}" | jq 'length')
  [ "${count}" -eq 0 ] && return

  jq -n \
    --arg section "${section}" \
    --arg project "${project}" \
    --arg type "${change_type}" \
    --argjson count "${count}" \
    --argjson containers "${containers_json}" \
    '{section: $section, project: $project, change_type: $type, changed_images: $count, containers: $containers}'
}

main
