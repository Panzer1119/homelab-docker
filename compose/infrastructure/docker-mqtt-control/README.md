[Up](../README.md)

# Docker MQTT Controller for Home Assistant

A lightweight Docker container that allows Home Assistant to start, stop, and monitor another Docker container using MQTT.

The controller uses **MQTT Discovery** to automatically create a switch in Home Assistant.

## Features

- Start and stop a Docker container through Home Assistant.
- Automatically detect the container's running state.
- Update the state every 5 seconds.
- Automatically create a Home Assistant switch via MQTT Discovery.
- Report controller availability using MQTT Last Will and Testament (LWT).
- No SSH connection required.

## Requirements

- Docker and Docker Compose
- Home Assistant with MQTT integration
- An MQTT broker (e.g., Mosquitto)
- A dedicated MQTT user

## Installation

1. Clone or download the project files:

   ```text
   docker-mqtt-control/
   ├── docker-compose.yml
   ├── Dockerfile
   ├── controller.py
   └── .env
   ```

2. Configure the MQTT connection in `docker-compose.yml`:

   ```yaml
   environment:
     MQTT_HOST: "YOUR_HA_IP"
     MQTT_PORT: "1883"
     MQTT_USER: "mqtt_docker"
     MQTT_PASSWORD: "${MQTT_PASSWORD}"
   ```

3. Create a `.env` file containing your MQTT password:

   ```env
   MQTT_PASSWORD=YOUR_PASSWORD
   ```

4. Start the controller:

   ```bash
   docker compose up -d --build
   ```

5. Check the logs:

   ```bash
   docker logs -f docker-mqtt-control
   ```

## Home Assistant

Once connected to MQTT, the controller automatically registers a switch through MQTT Discovery.

**Device:** Docker VM  
**Entity:** `switch.isponsorblocktv`

The switch can be added to any Home Assistant dashboard.

| Switch State | Docker Action |
|---|---|
| ON | Start `isponsorblocktv` |
| OFF | Stop `isponsorblocktv` |
| Unavailable | Controller disconnected |

## MQTT Topics

| Topic | Description |
|---|---|
| `docker/isponsorblocktv/set` | Receives ON/OFF commands |
| `docker/isponsorblocktv/state` | Publishes the current container state |
| `docker/isponsorblocktv/availability` | Reports controller availability |
| `homeassistant/switch/isponsorblocktv/config` | Home Assistant MQTT Discovery |

## Security

The controller requires access to the Docker socket:

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock:ro
```

**Warning:** Access to the Docker socket grants extensive control over the Docker host, even when mounted read-only. Only run trusted controller code and restrict access to the MQTT broker.

## Container Restart Policy

To prevent the controlled container from automatically restarting after being manually stopped, use:

```bash
docker update --restart unless-stopped isponsorblocktv
```

This ensures that a manually stopped container generally remains stopped after a Docker daemon restart.
