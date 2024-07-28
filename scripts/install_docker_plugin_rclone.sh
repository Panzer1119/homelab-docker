#!/bin/bash

# Install FUSE driver
sudo apt install -y fuse

# Create directories for rclone plugin
sudo mkdir -p /var/lib/docker-plugins/rclone/config
sudo mkdir -p /var/lib/docker-plugins/rclone/cache

# Touch the rclone config file
sudo touch /var/lib/docker-plugins/rclone/config/rclone.conf

# Install rclone plugin for docker
docker plugin install --alias rclone --grant-all-permissions rclone/docker-volume-rclone:amd64
