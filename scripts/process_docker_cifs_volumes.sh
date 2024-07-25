#!/bin/bash

# Function to display usage
usage() {
  echo "Usage: ${0} -d <directory> -a <address> -u <username> -p <password> [-q]"
  echo "  -d <directory>: Directory to recursively search for docker-compose.yml files"
  echo "  -a <address>: Address of the CIFS/SMB server"
  echo "  -u <username>: Username for authentication"
  echo "  -p <password>: Password for authentication"
  echo "  -q: Optional. Quiet mode"
  exit 1
}

# Check if yq is installed
if ! command -v yq &> /dev/null; then
  echo "yq could not be found. Please install yq to run this script."
  exit 1
fi

# Parse command line arguments
while getopts "d:a:u:p:" opt; do
  case ${opt} in
    d )
      directory=$OPTARG
      ;;
    a )
      address=$OPTARG
      ;;
    u )
      username=$OPTARG
      ;;
    p )
      password=$OPTARG
      ;;
    \? )
      usage
      ;;
  esac
done

# Ensure all required arguments are provided
if [ -z "${directory}" ] || [ -z "${address}" ] || [ -z "${username}" ] || [ -z "${password}" ]; then
  usage
fi

# Function to process docker-compose.yml files
process_docker_compose() {
  local file=$1

  # Extract top-level volume elements with external enabled
  volumes=$(yq e '.volumes | to_entries | map(select(.value.external == true)) | .[].key' "$file")

  for volume in $volumes; do
    # Check if volume has the specific label
    label_value=$(yq e ".volumes.$volume.labels[\"de.panzer1119.docker.volume.cifs.share\"]" "$file")

    if [ "$label_value" != "null" ]; then
      # Call create_docker_cifs_volume.sh with the necessary parameters
      ./create_docker_cifs_volume.sh -n "$volume" -a "$address" -s "$label_value" -u "$username" -p "$password"
    fi
  done
}

export -f process_docker_compose

# Find and process all docker-compose.yml files
find "${directory}" -type f -name "*docker-compose.yml" -exec bash -c 'process_docker_compose "$0"' {} \;
