# sensors.py

import time
import glob
import os
from config import (
    DEBUG_LOGGING_ENABLED,
    SENSOR_IDS,
    SENSOR_POLL_INTERVAL_SEC
)
# SystemState and MQTTClient are passed as arguments, no direct import needed here
# from state import SystemState
# from mqtt_client import MQTTClient

# Base path for 1-Wire devices on Raspberry Pi
W1_BASE_DIR = "/sys/bus/w1/devices/"

def _read_temp_from_path(sensor_file_path):
    """
    Reads and parses temperature from a specific 1-Wire sensor file.
    Returns temperature in Fahrenheit or None if an error occurs.
    """
    try:
        with open(sensor_file_path, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        if DEBUG_LOGGING_ENABLED: # Use global debug flag if no state instance here
            print(f"[SENSORS_ERROR] Sensor file not found: {sensor_file_path}")
        return None
    except Exception as e:
        if DEBUG_LOGGING_ENABLED:
            print(f"[SENSORS_ERROR] Error reading sensor file {sensor_file_path}: {e}")
        return None

    # Verify CRC check and temperature data
    if lines[0].strip()[-3:] == "YES":
        temp_line_pos = lines[1].find("t=")
        if temp_line_pos != -1:
            try:
                temp_string = lines[1][temp_line_pos + 2:]
                temp_c = float(temp_string) / 1000.0
                temp_f = temp_c * 9.0 / 5.0 + 32.0
                return round(temp_f, 2)
            except ValueError:
                if DEBUG_LOGGING_ENABLED:
                    print(f"[SENSORS_ERROR] Could not parse temperature from sensor {sensor_file_path}: {lines[1]}")
                return None
    if DEBUG_LOGGING_ENABLED:
        print(f"[SENSORS_ERROR] CRC check failed or invalid data for sensor {sensor_file_path}: {lines[0]}")
    return None

def read_all_configured_sensors():
    """
    Reads all sensors specified in SENSOR_IDS from config.
    Returns a dictionary: {internal_key: temperature_F_or_None, ...}
    """
    temperatures = {}
    if not SENSOR_IDS:
        if DEBUG_LOGGING_ENABLED:
            print("[SENSORS] No sensor IDs configured. Skipping sensor reads.")
        # Return dict with None for all configured keys to mark them as unreadable
        for key in SENSOR_IDS.keys(): # SENSOR_IDS might be empty, this is fine
            temperatures[key] = None
        return temperatures


    for internal_key, sensor_hw_id in SENSOR_IDS.items():
        if not sensor_hw_id: # Skip if sensor ID is empty in config
             if DEBUG_LOGGING_ENABLED:
                  print(f"[SENSORS_WARN] HW ID for sensor '{internal_key}' is not configured. Skipping.")
             temperatures[internal_key] = None
             continue

        sensor_file_path = os.path.join(W1_BASE_DIR, sensor_hw_id, "w1_slave")
        temp_f = _read_temp_from_path(sensor_file_path)
        temperatures[internal_key] = temp_f # Store temp or None if read failed

        if DEBUG_LOGGING_ENABLED:
            if temp_f is not None:
                print(f"[SENSORS] Read: {internal_key} ({sensor_hw_id}) = {temp_f:.2f}°F")
            else:
                print(f"[SENSORS] Failed to read: {internal_key} ({sensor_hw_id})")
    return temperatures

def sensor_loop(system_state, mqtt_client_instance, stop_event):
    """
    Periodically reads sensors, updates SystemState, and publishes to MQTT.
    :param system_state: The shared SystemState instance.
    :param mqtt_client_instance: The shared MQTTClient instance.
    :param stop_event: A threading.Event() to signal when to stop the loop.
    """
    if DEBUG_LOGGING_ENABLED:
        print("[SENSORS_LOOP] Sensor polling loop started.")
        if not SENSOR_IDS:
             print("[SENSORS_LOOP] WARNING: No sensors configured in SENSOR_IDS. Loop will run but read no data.")

    while not stop_event.is_set():
        current_temperatures = read_all_configured_sensors()

        # Update SystemState with all readings (including None for failed ones)
        system_state.update_temperatures(current_temperatures)

        # Publish each valid temperature individually via MQTTClient
        if mqtt_client_instance and mqtt_client_instance.connected:
            for internal_key, temp_f in current_temperatures.items():
                # mqtt_client.publish_temperature handles the None case
                mqtt_client_instance.publish_temperature(internal_key, temp_f)

        # Wait for the next polling interval
        # Check stop_event frequently during sleep to allow faster shutdown
        for _ in range(int(SENSOR_POLL_INTERVAL_SEC)):
            if stop_event.is_set():
                break
            time.sleep(1)

    if DEBUG_LOGGING_ENABLED:
        print("[SENSORS_LOOP] Sensor polling loop stopped.")