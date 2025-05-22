# config.py
import json
import os

# --- Configuration File Path ---
CONFIG_FILE_PATH = "config.json"

# --- Default Values (used if config.json is missing or incomplete) ---
DEFAULT_CONFIG = {
    "mqtt_settings": {
        "broker_address": "localhost",
        "broker_port": 1883,
        "username": "",
        "password": "",
        "client_id": "chiller_controller_default",
        "base_topic": "chiller",
        "home_assistant_discovery_prefix": "homeassistant",
        "external_ahu_call_topic": "chiller/external_ahu/call"
    },
    "gpio_settings": {
        "pump_pin": 17,
        "condenser_pin": 27,
        "relay_active_high": True
    },
    "operational_parameters": {
        "fallback_setpoint_f": 45.0,
        "initial_differential_f": 3.0,
        "condenser_min_off_time_sec": 120,
        "ahu_call_timeout_sec": 300,
        "pump_post_purge_duration_sec": 60
    },
    "timing_intervals": {
        "controller_loop_interval_sec": 2,
        "sensor_poll_interval_sec": 5,
        "mqtt_status_publish_interval_sec": 60,
        "mqtt_failsafe_timeout_sec": 75
    },
    "temperature_sensors": {
        "ids": {}, # No default sensor IDs, must be in config.json
        "friendly_names": {},
        "critical_sensors": ["supply"]
    },
    "general_settings": {
        "debug_logging_enabled": True
    }
}

# --- Load Configuration ---
_config_data = {}
if os.path.exists(CONFIG_FILE_PATH):
    try:
        with open(CONFIG_FILE_PATH, "r") as f:
            _config_data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"[CONFIG_ERROR] Failed to decode {CONFIG_FILE_PATH}: {e}. Using default values.")
        _config_data = DEFAULT_CONFIG
    except Exception as e:
        print(f"[CONFIG_ERROR] Failed to load {CONFIG_FILE_PATH}: {e}. Using default values.")
        _config_data = DEFAULT_CONFIG
else:
    print(f"[CONFIG_WARNING] {CONFIG_FILE_PATH} not found. Using default values.")
    _config_data = DEFAULT_CONFIG

def _get_config_value(path, default):
    """Helper to get nested config values."""
    keys = path.split('.')
    val = _config_data
    try:
        for key in keys:
            val = val[key]
        return val
    except (KeyError, TypeError):
        return default

# --- MQTT Settings ---
MQTT_BROKER_ADDRESS = str(_get_config_value("mqtt_settings.broker_address", DEFAULT_CONFIG["mqtt_settings"]["broker_address"]))
MQTT_BROKER_PORT = int(_get_config_value("mqtt_settings.broker_port", DEFAULT_CONFIG["mqtt_settings"]["broker_port"]))
MQTT_USERNAME = str(_get_config_value("mqtt_settings.username", DEFAULT_CONFIG["mqtt_settings"]["username"]))
MQTT_PASSWORD = str(_get_config_value("mqtt_settings.password", DEFAULT_CONFIG["mqtt_settings"]["password"]))
MQTT_CLIENT_ID = str(_get_config_value("mqtt_settings.client_id", DEFAULT_CONFIG["mqtt_settings"]["client_id"]))
MQTT_BASE_TOPIC = str(_get_config_value("mqtt_settings.base_topic", DEFAULT_CONFIG["mqtt_settings"]["base_topic"])).rstrip('/')
HA_DISCOVERY_PREFIX = str(_get_config_value("mqtt_settings.home_assistant_discovery_prefix", DEFAULT_CONFIG["mqtt_settings"]["home_assistant_discovery_prefix"])).rstrip('/')

# Derived MQTT Topics (common ones)
TOPIC_AVAILABILITY = f"{MQTT_BASE_TOPIC}/status/availability"
TOPIC_EXTERNAL_AHU_CALL = str(_get_config_value("mqtt_settings.external_ahu_call_topic", DEFAULT_CONFIG["mqtt_settings"]["external_ahu_call_topic"]))

# Control topics (suffixes will be added by components, e.g., "setpoint", "pump_override")
CONTROL_TOPIC_BASE = f"{MQTT_BASE_TOPIC}/control"
STATUS_TOPIC_BASE = f"{MQTT_BASE_TOPIC}/status"
TEMP_TOPIC_BASE = f"{MQTT_BASE_TOPIC}/sensor" # For publishing individual sensor temps

# --- GPIO Settings ---
PUMP_PIN = int(_get_config_value("gpio_settings.pump_pin", DEFAULT_CONFIG["gpio_settings"]["pump_pin"]))
CONDENSER_PIN = int(_get_config_value("gpio_settings.condenser_pin", DEFAULT_CONFIG["gpio_settings"]["condenser_pin"]))
RELAY_ACTIVE_HIGH = bool(_get_config_value("gpio_settings.relay_active_high", DEFAULT_CONFIG["gpio_settings"]["relay_active_high"]))

# --- Operational Parameters ---
FALLBACK_SETPOINT_F = float(_get_config_value("operational_parameters.fallback_setpoint_f", DEFAULT_CONFIG["operational_parameters"]["fallback_setpoint_f"]))
INITIAL_DIFFERENTIAL_F = float(_get_config_value("operational_parameters.initial_differential_f", DEFAULT_CONFIG["operational_parameters"]["initial_differential_f"]))
CONDENSER_MIN_OFF_TIME_SEC = int(_get_config_value("operational_parameters.condenser_min_off_time_sec", DEFAULT_CONFIG["operational_parameters"]["condenser_min_off_time_sec"]))
AHU_CALL_TIMEOUT_SEC = int(_get_config_value("operational_parameters.ahu_call_timeout_sec", DEFAULT_CONFIG["operational_parameters"]["ahu_call_timeout_sec"]))
PUMP_POST_PURGE_DURATION_SEC = int(_get_config_value("operational_parameters.pump_post_purge_duration_sec", DEFAULT_CONFIG["operational_parameters"]["pump_post_purge_duration_sec"]))

# --- Timing Intervals ---
CONTROLLER_LOOP_INTERVAL_SEC = float(_get_config_value("timing_intervals.controller_loop_interval_sec", DEFAULT_CONFIG["timing_intervals"]["controller_loop_interval_sec"]))
SENSOR_POLL_INTERVAL_SEC = float(_get_config_value("timing_intervals.sensor_poll_interval_sec", DEFAULT_CONFIG["timing_intervals"]["sensor_poll_interval_sec"]))
MQTT_STATUS_PUBLISH_INTERVAL_SEC = float(_get_config_value("timing_intervals.mqtt_status_publish_interval_sec", DEFAULT_CONFIG["timing_intervals"]["mqtt_status_publish_interval_sec"]))
MQTT_FAILSAFE_TIMEOUT_SEC = float(_get_config_value("timing_intervals.mqtt_failsafe_timeout_sec", DEFAULT_CONFIG["timing_intervals"]["mqtt_failsafe_timeout_sec"]))

# --- Temperature Sensor Configuration ---
# Sensor IDs (internal_key: actual_sensor_id)
SENSOR_IDS = _get_config_value("temperature_sensors.ids", DEFAULT_CONFIG["temperature_sensors"]["ids"])
print(f"[DEBUG_CONFIG_PY] Loaded SENSOR_IDS directly in config.py: {SENSOR_IDS}, type: {type(SENSOR_IDS)}")
# Sensor Friendly Names for HA (internal_key: friendly_name)
SENSOR_FRIENDLY_NAMES = _get_config_value("temperature_sensors.friendly_names", DEFAULT_CONFIG["temperature_sensors"]["friendly_names"])
# Critical sensor internal keys (e.g., ["supply"])
CRITICAL_SENSOR_KEYS = list(_get_config_value("temperature_sensors.critical_sensors", DEFAULT_CONFIG["temperature_sensors"]["critical_sensors"]))

# --- General Settings ---
DEBUG_LOGGING_ENABLED = bool(_get_config_value("general_settings.debug_logging_enabled", DEFAULT_CONFIG["general_settings"]["debug_logging_enabled"]))

# --- Persistent State File Paths ---
# These are not in config.json as they are application internals
PERSISTENCE_DIR = os.path.dirname(os.path.abspath(__file__)) # Or a dedicated /data directory
SETPOINT_FILE = os.path.join(PERSISTENCE_DIR, "setpoint.json")
OPERATIONAL_PARAMS_FILE = os.path.join(PERSISTENCE_DIR, "operational_params.json") # For differential, etc.
OVERRIDES_FILE = os.path.join(PERSISTENCE_DIR, "overrides.json")

# --- Sanity Checks & Logging (Optional but Recommended) ---
if DEBUG_LOGGING_ENABLED:
    print("[CONFIG_LOADED] MQTT Broker:", MQTT_BROKER_ADDRESS, ":", MQTT_BROKER_PORT)
    print("[CONFIG_LOADED] MQTT Base Topic:", MQTT_BASE_TOPIC)
    print("[CONFIG_LOADED] GPIO Pump Pin:", PUMP_PIN, "Condenser Pin:", CONDENSER_PIN, "Active High:", RELAY_ACTIVE_HIGH)
    print("[CONFIG_LOADED] Sensor IDs:", SENSOR_IDS)
    if not SENSOR_IDS:
        print("[CONFIG_WARNING] No sensor IDs found in configuration. Temperature sensing will not function.")
    if "supply" not in SENSOR_IDS:
        print("[CONFIG_WARNING] 'supply' sensor ID not found in configuration. This is critical for operation.")