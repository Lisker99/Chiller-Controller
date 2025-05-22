# Chiller Controller

This is the control logic for a homebuilt water chiller system. It manages temperature monitoring, relay control, and MQTT messaging to interact with remote devices such as air handling units (AHUs).

## Features

- DS18B20-based temperature sensing
- Setpoint configuration and override logic
- MQTT-based communication for control and monitoring
- Integration-ready with a web dashboard

## Usage

Run `main.py` to start the chiller controller:
```bash
python main.py
