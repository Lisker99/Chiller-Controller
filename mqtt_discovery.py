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
    SENSOR_FRIENDLY_NAMES,
    SENSOR_POLL_INTERVAL_SEC # Used for expire_after
)

DEVICE_INFO = {
    "identifiers": [f"{MQTT_BASE_TOPIC}_controller"],
    "name": "DIY Chiller Controller",
    "manufacturer": "DIY Solutions",
    "model": "RPi Chiller Control v1.0",
    "sw_version": "0.1.0" # Update as your software evolves (e.g., "0.2.0" after this feature)
}

class DiscoveryPublisher:
    def __init__(self, mqtt_client):
        self.client = mqtt_client
        self.debug = DEBUG_LOGGING_ENABLED
        self.discovery_prefix = HA_DISCOVERY_PREFIX.rstrip('/')
        self.base_topic_prefix = MQTT_BASE_TOPIC.rstrip('/')

    def _publish_config(self, entity_type, component_name, config_payload):
        config_payload["availability_topic"] = TOPIC_AVAILABILITY
        # Ensure unique_id uses base_topic_prefix for global uniqueness if multiple chillers on one HA
        config_payload["unique_id"] = f"{self.base_topic_prefix}_{component_name}"
        config_payload["device"] = DEVICE_INFO 

        topic = f"{self.discovery_prefix}/{entity_type}/{self.base_topic_prefix}/{component_name}/config"
        payload_json = json.dumps(config_payload)
        self.client.publish(topic, payload_json, retain=True)
        if self.debug:
            print(f"[DISCOVERY] Published to {topic}: {payload_json}")

    def publish_all(self):
        if self.debug:
            print("[DISCOVERY] Publishing all Home Assistant discovery messages...")

        self.publish_temperature_sensors()
        self.publish_setpoint_number()
        self.publish_differential_number()
        self.publish_ambient_lockout_number() # <-- NEW CALL
        self.publish_override_selects()
        self.publish_status_binary_sensors() # Will add new lockout status here
        self.publish_resend_discovery_button()

        if self.debug:
            print("[DISCOVERY] All discovery messages published.")

    def publish_temperature_sensors(self):
        if not SENSOR_IDS:
            if self.debug:
                print("[DISCOVERY] No sensor IDs configured. Skipping temperature sensor discovery.")
            return

        for internal_key, sensor_id_val in SENSOR_IDS.items():
            if not sensor_id_val: 
                if self.debug:
                    print(f"[DISCOVERY] Sensor ID for '{internal_key}' is empty. Skipping discovery.")
                continue

            friendly_name = SENSOR_FRIENDLY_NAMES.get(internal_key, internal_key.replace("_", " ").title())
            component_name = f"{internal_key}_temp" 

            payload = {
                "name": friendly_name,
                "state_topic": f"{TEMP_TOPIC_BASE}/{internal_key}", 
                "unit_of_measurement": "°F",
                "device_class": "temperature",
                "state_class": "measurement",
                "value_template": "{{ value | float(default=None) }}", 
                "expire_after": int(SENSOR_POLL_INTERVAL_SEC * 3 + 5) 
            }
            self._publish_config("sensor", component_name, payload)

    def publish_setpoint_number(self):
        component_name = "setpoint"
        payload = {
            "name": "Chiller Target Setpoint",
            "state_topic": f"{CONTROL_TOPIC_BASE}/setpoint",
            "command_topic": f"{CONTROL_TOPIC_BASE}/setpoint/set", 
            "min": 30,  
            "max": 70,  
            "step": 0.5,
            "unit_of_measurement": "°F",
            "mode": "box", 
            "icon": "mdi:thermometer-lines"
        }
        self._publish_config("number", component_name, payload)

    def publish_differential_number(self):
        component_name = "differential"
        payload = {
            "name": "Chiller Temperature Differential",
            "state_topic": f"{CONTROL_TOPIC_BASE}/differential",
            "command_topic": f"{CONTROL_TOPIC_BASE}/differential/set", 
            "min": 1.0, 
            "max": 10.0, 
            "step": 0.1,
            "unit_of_measurement": "°F",
            "mode": "box",
            "icon": "mdi:swap-vertical-bold"
        }
        self._publish_config("number", component_name, payload)

    # <-- NEW METHOD for Ambient Lockout Setpoint -->
    def publish_ambient_lockout_number(self):
        """Publish discovery for the ambient temperature lockout setpoint control."""
        component_name = "ambient_lockout_setpoint"
        payload = {
            "name": "Ambient Lockout Temperature",
            "state_topic": f"{CONTROL_TOPIC_BASE}/ambient_lockout_setpoint",
            "command_topic": f"{CONTROL_TOPIC_BASE}/ambient_lockout_setpoint/set",
            "min": 30,  # °F - Allow setting very low to effectively disable
            "max": 80,  # °F - Reasonable upper limit
            "step": 1.0,
            "unit_of_measurement": "°F",
            "mode": "box", 
            "icon": "mdi:account-lock-outline" # Icon suggesting environmental lockout
        }
        self._publish_config("number", component_name, payload)

    def publish_override_selects(self):
        devices = {
            "pump": "Pump",
            "chiller": "Chiller", 
            "cooling": "Cooling Call Simulation"
        }
        for internal_key, friendly_prefix in devices.items():
            component_name = f"{internal_key}_override"
            payload = {
                "name": f"{friendly_prefix} Manual Override",
                "state_topic": f"{CONTROL_TOPIC_BASE}/{internal_key}_override", 
                "command_topic": f"{CONTROL_TOPIC_BASE}/{internal_key}_override/set", 
                "options": ["auto", "on", "off"], 
                "icon": "mdi:tune" if internal_key != "cooling" else "mdi:snowflake-thermometer"
            }
            self._publish_config("select", component_name, payload)

    def publish_status_binary_sensors(self):
        statuses = {
            "cooling_call_active": {
                "name": "Chiller Cooling Call Active",
                "icon": "mdi:snowflake-alert",
                "topic_suffix": "cooling_call_active"
            },
            "pump_relay_status": {
                "name": "Chiller Pump Relay Engaged",
                "icon": "mdi:pump",
                "topic_suffix": "pump_relay_state" 
            },
            "condenser_relay_status": {
                "name": "Chiller Condenser Relay Engaged",
                "icon": "mdi:air-conditioner",
                "topic_suffix": "condenser_relay_state" 
            },
            "critical_sensor_fault": {
                "name": "Chiller Critical Sensor Fault",
                "icon": "mdi:alert-circle-outline",
                "device_class": "problem", 
                "topic_suffix": "critical_sensor_fault"
            },
            # <-- NEW Binary Sensor for Ambient Lockout Status -->
            "ambient_lockout_status": {
                "name": "Chiller Ambient Lockout Active",
                "icon": "mdi:weather-sunny-off", # Or mdi:cancel, mdi:block-helper
                "device_class": "running", # Shows as "Running" / "Not Running". "Problem" could also work.
                                           # If "Problem", ON means there is a problem (lockout is active).
                                           # If "Running", ON means it IS running (i.e. lockout NOT active).
                                           # Let's use "power" so ON means lockout is ON (active/powered).
                "device_class": "power", # ON = Lockout is active (system is "powered" down by lockout)
                                         # OFF = Lockout is not active (system can run normally)

                "topic_suffix": "ambient_lockout_active"
            }
        }

        for component_name, details in statuses.items():
            payload = {
                "name": details["name"],
                "state_topic": f"{STATUS_TOPIC_BASE}/{details['topic_suffix']}", 
                "payload_on": "ON",  
                "payload_off": "OFF", 
                "icon": details.get("icon")
            }
            if "device_class" in details:
                payload["device_class"] = details["device_class"]
            self._publish_config("binary_sensor", component_name, payload)


    def publish_resend_discovery_button(self):
        component_name = "resend_discovery"
        payload = {
            "name": "Chiller Resend HA Discovery",
            "command_topic": f"{CONTROL_TOPIC_BASE}/resend_discovery",
            "payload_press": "PRESS", 
            "icon": "mdi:refresh-auto"
        }
        self._publish_config("button", component_name, payload)