import os
import glob
import time
import json

# Load config
CONFIG_PATH = 'config.json'  # Update this if your JSON file is named differently

try:
    with open(CONFIG_PATH, 'r') as f:
        config = json.load(f)
        sensor_labels = {v: k for k, v in config.get('sensor_ids', {}).items()}
except Exception as e:
    print(f"Error loading config: {e}")
    sensor_labels = {}

def read_temp(sensor_path):
    try:
        with open(sensor_path, 'r') as f:
            lines = f.readlines()
            if lines[0].strip()[-3:] != 'YES':
                return None
            equals_pos = lines[1].find('t=')
            if equals_pos != -1:
                temp_string = lines[1][equals_pos+2:]
                temp_c = float(temp_string) / 1000.0
                return temp_c
    except:
        return None

base_dir = '/sys/bus/w1/devices/'
device_folders = glob.glob(base_dir + '28-*')

if not device_folders:
    print("No DS18B20 sensors found. Check wiring and 1-Wire config.")
    exit()

print("\nTouch a sensor and watch its temp change.\nPress Ctrl+C to stop.\n")

try:
    while True:
        for folder in device_folders:
            device_id = os.path.basename(folder)
            label = sensor_labels.get(device_id, device_id)  # Use label or fallback to ID
            temp = read_temp(os.path.join(folder, 'w1_slave'))
            if temp is not None:
                print(f"{label}: {temp:.2f}°C")
            else:
                print(f"{label}: Error reading temperature")
        print("-" * 40)
        time.sleep(3)

except KeyboardInterrupt:
    print("\nExiting. Label your sensors accordingly.")
