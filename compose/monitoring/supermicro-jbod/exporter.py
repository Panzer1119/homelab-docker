#!/usr/bin/env python3
import json
import logging
import os
import re
import time

import paho.mqtt.client as mqtt
from smbmc import Client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("supermicro-jbod")

BMC_HOST = os.environ["BMC_HOST"]
BMC_USER = os.environ["BMC_USER"]
BMC_PASSWORD = os.environ["BMC_PASSWORD"]

MQTT_HOST = os.environ["MQTT_HOST"]
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.environ["MQTT_USER"]
MQTT_PASSWORD = os.environ["MQTT_PASSWORD"]

INTERVAL = int(os.getenv("POLL_INTERVAL", "30"))

DEVICE_ID = os.environ["DEVICE_ID"]
DEVICE_NAME = os.environ["DEVICE_NAME"] or f"Supermicro JBOD {DEVICE_ID}"
BASE = os.environ["BASE"]
STATE_TOPIC = f"{BASE}/state"
AVAIL_TOPIC = f"{BASE}/availability"
DISCOVERY = "homeassistant"

DEVICE = {
    "identifiers": [DEVICE_ID],
    "name": DEVICE_NAME,
    "manufacturer": "Supermicro",
    "model": "CSE-PTJBOD-CB3",
}

UNITS = {
    "input_voltage": ("V", "voltage"),
    "output_voltage": ("V", "voltage"),
    "input_current": ("A", "current"),
    "output_current": ("A", "current"),
    "input_power": ("W", "power"),
    "output_power": ("W", "power"),
    "temp_1": ("°C", "temperature"),
    "temp_2": ("°C", "temperature"),
    "fan_1": ("rpm", None),
    "fan_2": ("rpm", None),
}

UNIT_NAMES = {
    "DEGREES_CELSIUS": ("°C", "temperature"),
    "VOLTS": ("V", "voltage"),
    "AMPS": ("A", "current"),
    "RPM": ("rpm", None),
}

discovered = set()


def slug(value):
    value = re.sub(r"[^a-zA-Z0-9]+", "_", str(value))
    return value.strip("_").lower()


def plain(value):
    if value is None:
        return None
    if hasattr(value, "name"):
        return value.name
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def publish_discovery(client, key, name, unit=None, device_class=None):
    if key in discovered:
        return

    config = {
        "name": name,
        "unique_id": f"{DEVICE_ID}_{key}",
        "state_topic": STATE_TOPIC,
        "value_template": f'{{{{ value_json["{key}"] }}}}',
        "availability_topic": AVAIL_TOPIC,
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": DEVICE,
    }

    if unit:
        config["unit_of_measurement"] = unit
    if device_class:
        config["device_class"] = device_class
        if device_class in ("voltage", "current", "power", "temperature"):
            config["state_class"] = "measurement"

    topic = f"{DISCOVERY}/sensor/{DEVICE_ID}_{key}/config"
    info = client.publish(topic, json.dumps(config), qos=1, retain=True)
    info.wait_for_publish()
    discovered.add(key)
    log.info("Discovered entity: %s", name)


def collect(client, bmc):
    values = {}

    # Standard IPMI sensor readings
    for sensor in bmc.get_sensor_metrics():
        name = slug(sensor.name)
        state = plain(sensor.state)

        state_key = f"sensor_{name}_status"
        values[state_key] = state
        publish_discovery(
            client, state_key, f"{sensor.name} Status"
        )

        if state != "PRESENT":
            continue

        reading = plain(sensor.reading)
        if reading is None:
            continue

        key = f"sensor_{name}"
        unit_name = getattr(sensor.unit, "name", "")
        unit, device_class = UNIT_NAMES.get(unit_name, (None, None))

        values[key] = reading
        publish_discovery(
            client, key, sensor.name, unit, device_class
        )

    # PSU PMBus telemetry
    for psu in bmc.get_pmbus_metrics():
        psu_id = int(psu.id) + 1
        prefix = f"psu_{psu_id}"

        for field, raw_value in vars(psu).items():
            if field in ("id", "name") or raw_value is None:
                continue

            value = plain(raw_value)
            if value is None:
                continue

            key = f"{prefix}_{slug(field)}"
            unit, device_class = UNITS.get(field, (None, None))

            values[key] = value
            publish_discovery(
                client,
                key,
                f"PSU {psu_id} {field.replace('_', ' ').title()}",
                unit,
                device_class,
            )

    return values


def main():
    bmc = Client(
        BMC_HOST,
        BMC_USER,
        BMC_PASSWORD,
        verify=False,
        request_timeout=15,
    )
    bmc.login()

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="supermicro-jbod-exporter",
    )
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    client.will_set(AVAIL_TOPIC, "offline", qos=1, retain=True)

    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()

    try:
        while True:
            try:
                values = collect(client, bmc)

                if values:
                    client.publish(
                        STATE_TOPIC,
                        json.dumps(values),
                        qos=1,
                        retain=True,
                    ).wait_for_publish()

                    client.publish(
                        AVAIL_TOPIC,
                        "online",
                        qos=1,
                        retain=True,
                    ).wait_for_publish()

                    log.info("Published %d metrics", len(values))

            except Exception:
                log.exception("Polling failed")

            time.sleep(INTERVAL)

    finally:
        client.publish(
            AVAIL_TOPIC, "offline", qos=1, retain=True
        ).wait_for_publish()
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
