import os
import json
import time
import threading
import paho.mqtt.client as mqtt

class CallTracker:
    def __init__(self, broker, port, topic, timeout_sec, debug=False):
        self.topic = topic
        self.last_call = 0
        self.timeout = timeout_sec
        self.debug = debug

        self.broker = broker
        self.port = port

        self.client = mqtt.Client()
        self.client.on_message = self.on_message
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect

        self.connected = False

        # Manual overrides
        self.manual_pump = None
        self.manual_chiller = None
        self.manual_cooling = None

        self.setpoint_file = "setpoint.json"
        self.setpoint = self.load_setpoint()

        # Load persistent overrides from disk
        self.load_overrides()

        # Connect and start loop in thread
        self._connect()

        # Start status publishing loop
        self.start_status_loop()

    def _connect(self):
        def try_connect():
            while not self.connected:
                try:
                    if self.debug:
                        print(f"Attempting MQTT connect to {self.broker}:{self.port}...")
                    self.client.connect(self.broker, self.port)
                    self.client.loop_start()
                    # Wait for on_connect callback to set connected = True
                    timeout = 5
                    while not self.connected and timeout > 0:
                        time.sleep(0.1)
                        timeout -= 0.1
                    if not self.connected:
                        self.client.loop_stop()
                        raise Exception("MQTT connect timeout")
                except Exception as e:
                    if self.debug:
                        print(f"MQTT connect failed: {e}, retrying in 5 seconds")
                    time.sleep(5)
        threading.Thread(target=try_connect, daemon=True).start()

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            if self.debug:
                print("MQTT connected successfully")
            # Subscribe to topics on each connect
            self.client.subscribe(self.topic)
            self.client.subscribe("chiller/control/pump")
            self.client.subscribe("chiller/control/chiller")
            self.client.subscribe("chiller/control/cooling")
            self.client.subscribe("chiller/control/setpoint")
        else:
            if self.debug:
                print(f"MQTT connection failed with code {rc}")

    def on_disconnect(self, client, userdata, rc):
        self.connected = False
        if self.debug:
            print(f"MQTT disconnected with code {rc}")
        # Stop loop and try reconnect
        self.client.loop_stop()
        self._connect()

    def is_connected(self):
        return self.connected

    def on_message(self, client, userdata, msg):
        payload_raw = msg.payload.decode()
        payload = payload_raw.lower()
        if self.debug:
            print(f"MQTT message received on {msg.topic}: {payload_raw}")

        if msg.topic == self.topic:
            if payload == "1":
                self.last_call = time.time()

        elif msg.topic == "chiller/control/pump":
            if payload == "on":
                self.manual_pump = True
                if self.debug:
                    print("[DEBUG] Manual pump override set to ON")
            elif payload == "off":
                self.manual_pump = False
                if self.debug:
                    print("[DEBUG] Manual pump override set to OFF")
            elif payload in ["clear", "reset"]:
                self.manual_pump = None
                if self.debug:
                    print("[DEBUG] Manual pump override CLEARED")
            else:
                if self.debug:
                    print(f"Unknown payload for pump override: {payload}")
            self.save_overrides()  # Save persistent overrides on change

        elif msg.topic == "chiller/control/chiller":
            if payload == "on":
                self.manual_chiller = True
            elif payload == "off":
                self.manual_chiller = False
            elif payload in ["clear", "reset"]:
                self.manual_chiller = None
            else:
                if self.debug:
                    print(f"Unknown payload for chiller override: {payload}")
            # Do NOT save chiller override persistently

        elif msg.topic == "chiller/control/cooling":
            if payload == "on":
                self.manual_cooling = True
            elif payload == "off":
                self.manual_cooling = False
            elif payload in ["clear", "reset"]:
                self.manual_cooling = None
            else:
                if self.debug:
                    print(f"Unknown payload for cooling override: {payload}")
            self.save_overrides()  # Save persistent overrides on change

        elif msg.topic == "chiller/control/setpoint":
            try:
                self.setpoint = float(payload_raw)
                self.save_setpoint()
                if self.debug:
                    print(f"Updated setpoint to {self.setpoint}°F")
            except ValueError:
                if self.debug:
                    print(f"Invalid setpoint value received: {payload_raw}")

    def should_run(self):
        # Return True if cooling call active (no override), or override True
        if self.manual_cooling is not None:
            return self.manual_cooling
        return (time.time() - self.last_call) < self.timeout

    def publish_status(self):
        status = (
            "Forced" if self.manual_cooling is not None else
            "Active" if self.should_run() else
            "Inactive"
        )
        self.client.publish("chiller/status/ahu_call", status)
        if self.debug:
            print(f"Published AHU call status: {status}")

    def start_status_loop(self):
        def loop():
            while True:
                if self.connected:
                    self.publish_status()
                time.sleep(5)  # adjust frequency as needed
        threading.Thread(target=loop, daemon=True).start()

    def load_setpoint(self):
        if os.path.exists(self.setpoint_file):
            try:
                with open(self.setpoint_file, "r") as f:
                    return float(json.load(f).get("setpoint"))
            except (ValueError, json.JSONDecodeError, TypeError):
                if self.debug:
                    print("Failed to load setpoint, using default None")
        return None

    def save_setpoint(self):
        try:
            print("[MQTT Handler] Saving setpoint to file")
            with open("setpoint.json", "w") as f:
                json.dump({"setpoint": self.setpoint}, f)
        except Exception as e:
            print(f"[MQTT Handler] Failed to save setpoint: {e}")

    # --- New methods for persistent overrides ---

    def load_overrides(self):
        try:
            if os.path.exists("overrides.json"):
                with open("overrides.json", "r") as f:
                    data = json.load(f)
                    self.manual_pump = data.get("manual_pump")
                    self.manual_cooling = data.get("manual_cooling")
                if self.debug:
                    print(f"Loaded overrides: pump={self.manual_pump}, cooling={self.manual_cooling}")
        except Exception as e:
            if self.debug:
                print(f"Failed to load overrides: {e}")

    def save_overrides(self):
        try:
            print("[MQTT Handler] Saving overrides to file")
            with open("overrides.json", "w") as f:
                json.dump({
                    "manual_pump": self.manual_pump,
                    "manual_chiller": self.manual_chiller,
                    "manual_cooling": self.manual_cooling
                }, f)
        except Exception as e:
            print(f"[MQTT Handler] Failed to save overrides: {e}")
