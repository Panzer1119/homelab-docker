import os
import time
import threading

import docker
import paho.mqtt.client as mqtt


# Configuration
MQTT_HOST = os.environ["MQTT_HOST"]
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))

MQTT_USER = os.environ["MQTT_USER"]
MQTT_PASSWORD = os.environ["MQTT_PASSWORD"]

CONTAINER_NAME = "isponsorblocktv"

TOPIC_COMMAND = "docker/isponsorblocktv/set"
TOPIC_STATE = "docker/isponsorblocktv/state"
TOPIC_AVAILABILITY = "docker/isponsorblocktv/availability"

TOPIC_DISCOVERY = (
    "homeassistant/switch/isponsorblocktv/config"
)


# Docker-Connection
docker_client = docker.from_env()


def get_container():
    return docker_client.containers.get(CONTAINER_NAME)


def get_state():
    try:
        container = get_container()
        container.reload()

        if container.status == "running":
            return "ON"

        return "OFF"

    except docker.errors.NotFound:
        return "OFF"

    except Exception as e:
        print(f"Docker error: {e}")
        return None


def publish_state():
    state = get_state()

    if state is not None:
        client.publish(
            TOPIC_STATE,
            state,
            retain=True
        )


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code != 0:
        print(f"MQTT connection failed: {reason_code}")
        return

    print("Connected to MQTT")

    client.subscribe(TOPIC_COMMAND)

    # Home Assistant MQTT Discovery
    import json

    discovery = {
        "name": "iSponsorBlockTV",
        "unique_id": "docker_isponsorblocktv",

        "command_topic": TOPIC_COMMAND,
        "state_topic": TOPIC_STATE,

        "availability_topic": TOPIC_AVAILABILITY,

        "payload_on": "ON",
        "payload_off": "OFF",

        "state_on": "ON",
        "state_off": "OFF",

        "icon": "mdi:docker",

        "device": {
            "identifiers": ["docker_vm"],
            "name": "Docker VM",
            "manufacturer": "Docker"
        }
    }

    client.publish(
        TOPIC_DISCOVERY,
        json.dumps(discovery),
        retain=True
    )

    client.publish(
        TOPIC_AVAILABILITY,
        "online",
        retain=True
    )

    publish_state()


def on_message(client, userdata, msg):
    command = msg.payload.decode().strip()

    print(f"Received command: {command}")

    try:
        container = get_container()

        if command == "ON":
            container.start()

        elif command == "OFF":
            container.stop()

        else:
            return

        publish_state()

    except Exception as e:
        print(f"Error: {e}")
        publish_state()


client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id="docker-isponsorblocktv"
)

client.username_pw_set(
    MQTT_USER,
    MQTT_PASSWORD
)

client.will_set(
    TOPIC_AVAILABILITY,
    "offline",
    retain=True
)

client.on_connect = on_connect
client.on_message = on_message


def monitor():
    while True:
        if client.is_connected():
            publish_state()

        time.sleep(5)


threading.Thread(
    target=monitor,
    daemon=True
).start()


client.connect(
    MQTT_HOST,
    MQTT_PORT,
    keepalive=60
)

client.loop_forever()
