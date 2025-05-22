# mqtt_discovery.py

import json
from config import (
    DEBUG_LOGGING_ENABLED,
    MQTT_BASE_TOPIC,
    HA_DISCOVERY_PREFIX,
    TOPIC_AVAILABILITY,
    CONTROL_TOPIC_BASE,
    STATUS_TOPIC_BASE,
    TEMP_TOPIC_BASE,
    SENSOR_IDS,
    SENSOR_FRIENDLY_NAMES
)

DEVICE_INFO = {
    "identifiers": [f"{MQTT_BASE_TOPIC}_controller"],
    "name": "DIY Chiller Controller",
    "manufacturer": "DIY Solutions",
    "model": "RPi Chiller Control v1.0",
    "sw_version": "0.1.0" # You can update this as your software evolves
}

class DiscoveryPublisher:
    def __init__(self, mqtt_client):
        """
        :param mqtt_client: paho.mqtt.client.Client instance
        """
        self.client = mqtt_client
        self.debug = DEBUG_LOGGING_ENABLED
        self.discovery_prefix = HA_DISCOVERY_PREFIX.rstrip('/')
        self.base_topic_prefix = MQTT_BASE_TOPIC.rstrip('/')

    def _publish_config(self, entity_type, component_name, config_payload):
        """
        Helper to publish a discovery message.
        :param entity_type: e.g., "sensor", "number", "select", "binary_sensor", "button"
        :param component_name: A unique name for this component, e.g., "supply_temp", "setpoint"
        :param config_payload: The JSON payload for the discovery message.
        """
        # Ensure common fields are present
        config_payload["availability_topic"] = TOPIC_AVAILABILITY
        config_payload["unique_id"] = f"{self.base_topic_prefix}_{component_name}"
        config_payload["device"] = DEVICE_INFO # Add device information

        topic = f"{self.discovery_prefix}/{entity_type}/{self.base_topic_prefix}/{component_name}/config"
        payload_json = json.dumps(config_payload)
        self.client.publish(topic, payload_json, retain=True)
        if self.debug:
            print(f"[DISCOVERY] Published to {topic}: {payload_json}")

    def publish_all(self):
        """Publish all discovery configurations."""
        if self.debug:
            print("[DISCOVERY] Publishing all Home Assistant discovery messages...")

        self.publish_temperature_sensors()
        self.publish_setpoint_number()
        self.publish_differential_number()
        self.publish_override_selects()
        self.publish_status_binary_sensors()
        self.publish_resend_discovery_button()

        if self.debug:
            print("[DISCOVERY] All discovery messages published.")

    def publish_temperature_sensors(self):
        """Publish discovery for all configured temperature sensors."""
        if not SENSOR_IDS:
            if self.debug:
                print("[DISCOVERY] No sensor IDs configured. Skipping temperature sensor discovery.")
            return

        for internal_key, sensor_id_val in SENSOR_IDS.items():
            if not sensor_id_val: # Skip if sensor ID is empty
                if self.debug:
                    print(f"[DISCOVERY] Sensor ID for '{internal_key}' is empty. Skipping discovery.")
                continue

            friendly_name = SENSOR_FRIENDLY_NAMES.get(internal_key, internal_key.replace("_", " ").title())
            component_name = f"{internal_key}_temp" # e.g., supply_temp

            payload = {
                "name": friendly_name,
                "state_topic": f"{TEMP_TOPIC_BASE}/{internal_key}", # e.g., chiller/sensor/supply
                "unit_of_measurement": "°F",
                "device_class": "temperature",
                "state_class": "measurement",
                "value_template": "{{ value | float(default=None) }}", # Ensure it's treated as a float
                "expire_after": int(SENSOR_POLL_INTERVAL_SEC * 3 + 5) if 'SENSOR_POLL_INTERVAL_SEC' in globals() else 30 # Mark unavailable if no updates
            }
            self._publish_config("sensor", component_name, payload)

    def publish_setpoint_number(self):
        """Publish discovery for the setpoint control."""
        component_name = "setpoint"
        payload = {
            "name": "Chiller Target Setpoint",
            "state_topic": f"{CONTROL_TOPIC_BASE}/setpoint",
            "command_topic": f"{CONTROL_TOPIC_BASE}/setpoint/set", # Separate command topic
            "min": 30,  # °F
            "max": 70,  # °F
            "step": 0.5,
            "unit_of_measurement": "°F",
            "mode": "box", # Or "slider"
            "icon": "mdi:thermometer-lines"
        }
        self._publish_config("number", component_name, payload)

    def publish_differential_number(self):
        """Publish discovery for the differential control."""
        component_name = "differential"
        payload = {
            "name": "Chiller Temperature Differential",
            "state_topic": f"{CONTROL_TOPIC_BASE}/differential",
            "command_topic": f"{CONTROL_TOPIC_BASE}/differential/set", # Separate command topic
            "min": 1.0, # °F
            "max": 10.0, # °F
            "step": 0.1,
            "unit_of_measurement": "°F",
            "mode": "box",
            "icon": "mdi:swap-vertical-bold"
        }
        self._publish_config("number", component_name, payload)

    def publish_override_selects(self):
        """Publish discovery for manual override selectors (pump, chiller, cooling)."""
        # "chiller" is the common term, but internally we might use "condenser" for the pin.
        # For HA UI, "Chiller Override" might be more intuitive than "Condenser Override".
        devices = {
            "pump": "Pump",
            "chiller": "Chiller", # User-facing name for the condenser system
            "cooling": "Cooling Call Simulation"
        }
        for internal_key, friendly_prefix in devices.items():
            component_name = f"{internal_key}_override"
            payload = {
                "name": f"{friendly_prefix} Manual Override",
                "state_topic": f"{CONTROL_TOPIC_BASE}/{internal_key}_override", # e.g. chiller/control/pump_override
                "command_topic": f"{CONTROL_TOPIC_BASE}/{internal_key}_override/set", # e.g. chiller/control/pump_override/set
                "options": ["auto", "on", "off"], # MQTT client will handle "clear" mapping to "auto"
                "icon": "mdi:tune" if internal_key != "cooling" else "mdi:snowflake-thermometer"
            }
            self._publish_config("select", component_name, payload)

    def publish_status_binary_sensors(self):
        """Publish discovery for status indicators."""
        statuses = {
            "cooling_call_active": {
                "name": "Chiller Cooling Call Active",
                "icon": "mdi:snowflake-alert",
                "topic_suffix": "cooling_call_active"
            },
            "pump_relay_status": {
                "name": "Chiller Pump Relay Engaged",
                "icon": "mdi:pump",
                "topic_suffix": "pump_relay_state" # Actual state of the pump relay
            },
            "condenser_relay_status": {
                "name": "Chiller Condenser Relay Engaged",
                "icon": "mdi:air-conditioner",
                "topic_suffix": "condenser_relay_state" # Actual state of the condenser relay
            },
            "critical_sensor_fault": {
                "name": "Chiller Critical Sensor Fault",
                "icon": "mdi:alert-circle-outline",
                "device_class": "problem", # This will show as "Problem Detected"
                "topic_suffix": "critical_sensor_fault"
            }
        }

        for component_name, details in statuses.items():
            payload = {
                "name": details["name"],
                "state_topic": f"{STATUS_TOPIC_BASE}/{details['topic_suffix']}", # e.g. chiller/status/cooling_call_active
                "payload_on": "ON",  # The string we'll publish for ON state
                "payload_off": "OFF", # The string we'll publish for OFF state
                "icon": details.get("icon")
            }
            if "device_class" in details:
                payload["device_class"] = details["device_class"]
            self._publish_config("binary_sensor", component_name, payload)


    def publish_resend_discovery_button(self):
        """Publish discovery for a button to resend discovery messages."""
        component_name = "resend_discovery"
        payload = {
            "name": "Chiller Resend HA Discovery",
            "command_topic": f"{CONTROL_TOPIC_BASE}/resend_discovery",
            "payload_press": "PRESS", # The payload to send when pressed
            "icon": "mdi:refresh-auto"
        }
        self._publish_config("button", component_name, payload)