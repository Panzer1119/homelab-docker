#!/bin/bash

ENV_FILENAME="create_volumes_monitoring.env"
CREATE_VOLUME_SCRIPT_FILENAME="create_docker_cifs_volume.sh"

ENV_FILE="${PWD}/${ENV_FILENAME}"
REF_ENV_FILE="${PWD}/ref.${ENV_FILENAME}"
CREATE_VOLUME_SCRIPT_FILE="${PWD}/${CREATE_VOLUME_SCRIPT_FILENAME}"

# Check if create_volumes_monitoring.env exists
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Environment file '${ENV_FILE}' not found."
  if op inject -i "${REF_ENV_FILE}" -o "${ENV_FILE}" > /dev/null; then
    echo "Environment file '${ENV_FILE}' created successfully."
  else
    echo "Error: Failed to create environment file '${ENV_FILE}'."
    echo "Please make sure to create the environment file by executing the following command:"
    echo "op inject -i '${REF_ENV_FILE}' -o '${ENV_FILE}' -f"
    echo "Or copy the environment file from the reference file and replace the values accordingly:"
    echo "cp '${REF_ENV_FILE}' '${ENV_FILE}'"
    exit 1
  fi
fi

# shellcheck source=create_volumes_monitoring.env
source "${ENV_FILE}"

create_cifs_volume() {
  # Replace slashes with hyphens
  local VOLUME_NAME="${1//\//-}"
  local SHARE_NAME="${2}"
  local PASSWORD="${3:-${DEFAULT_PASSWORD}}"
  local USERNAME="${4:-${DEFAULT_USERNAME}}"
  local ADDRESS="${5:-${DEFAULT_ADDRESS}}"
  # Call the create_docker_cifs_volume.sh script
  if ! bash "${CREATE_VOLUME_SCRIPT_FILE}" -n "${VOLUME_NAME}" -a "${ADDRESS}" -s "${SHARE_NAME}" -u "${USERNAME}" -p "${PASSWORD}" -e -q ; then
    echo "Error: Failed to create Docker volume '${VOLUME_NAME}'"
    exit 1
  fi
  echo "Docker volume '${VOLUME_NAME}' created successfully."
}

main() {
  # Graylog
  create_cifs_volume "graylog/log_data" "Graylog"
}

# Call the main function
main
