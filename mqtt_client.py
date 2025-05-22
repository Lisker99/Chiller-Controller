# mqtt_client.py

import time
import json
import paho.mqtt.client as mqtt
from config import (
    DEBUG_LOGGING_ENABLED,
    MQTT_BROKER_ADDRESS,
    MQTT_BROKER_PORT,
    MQTT_USERNAME,
    MQTT_PASSWORD,
    MQTT_CLIENT_ID,
    TOPIC_AVAILABILITY,
    CONTROL_TOPIC_BASE,
    STATUS_TOPIC_BASE,
    TEMP_TOPIC_BASE,
    TOPIC_EXTERNAL_AHU_CALL,
    MQTT_STATUS_PUBLISH_INTERVAL_SEC # Used in the status publishing loop
)
from state import SystemState # Type hinting
from mqtt_discovery import DiscoveryPublisher

class MQTTClient:
    def __init__(self, state: SystemState):
        self.state = state
        self.debug = DEBUG_LOGGING_ENABLED
        self.client_id = MQTT_CLIENT_ID
        self.discovery_publisher = None # Will be set after MQTT client is created

        self.client = mqtt.Client(client_id=self.client_id)
        if MQTT_USERNAME and MQTT_PASSWORD:
            self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        # Last Will and Testament
        self.client.will_set(TOPIC_AVAILABILITY, payload="offline", qos=1, retain=True)

        self.connected = False
        self._stop_status_loop = False
        self.status_thread = None


    def connect(self):
        if self.debug:
            print(f"[MQTT] Attempting to connect to broker {MQTT_BROKER_ADDRESS}:{MQTT_BROKER_PORT} as {self.client_id}")
        try:
            self.client.connect(MQTT_BROKER_ADDRESS, MQTT_BROKER_PORT, 60)
            self.client.loop_start() # Starts a background thread to handle network traffic, dispatches, and callbacks
        except Exception as e:
            print(f"[MQTT_ERROR] Connection failed: {e}")
            # Consider adding a retry mechanism here or in main.py

    def disconnect(self):
        if self.debug:
            print("[MQTT] Disconnecting from broker...")
        self._stop_status_loop = True
        if self.status_thread and self.status_thread.is_alive():
            self.status_thread.join(timeout=5) # Wait for status loop to finish
        self.publish_availability("offline") # Try to send offline LWT before disconnecting
        self.client.loop_stop() # Stop the network loop
        self.client.disconnect()
        if self.debug:
            print("[MQTT] Disconnected.")

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            if self.debug:
                print(f"[MQTT] Connected to broker successfully (rc={rc})")
            self.state.update_mqtt_timestamp() # Update MQTT communication timestamp

            # Subscribe to command topics
            self.client.subscribe(f"{CONTROL_TOPIC_BASE}/setpoint/set", qos=1)
            self.client.subscribe(f"{CONTROL_TOPIC_BASE}/differential/set", qos=1)
            self.client.subscribe(f"{CONTROL_TOPIC_BASE}/pump_override/set", qos=1)
            self.client.subscribe(f"{CONTROL_TOPIC_BASE}/chiller_override/set", qos=1) # "chiller" is HA entity
            self.client.subscribe(f"{CONTROL_TOPIC_BASE}/cooling_override/set", qos=1)
            self.client.subscribe(f"{CONTROL_TOPIC_BASE}/resend_discovery", qos=1)
            self.client.subscribe(TOPIC_EXTERNAL_AHU_CALL, qos=1) # For external AHU calls

            if self.debug:
                print("[MQTT] Subscribed to command topics.")

            # Publish initial availability
            self.publish_availability("online")

            # Initialize and run HA discovery
            if not self.discovery_publisher:
                 self.discovery_publisher = DiscoveryPublisher(self.client)
            self.discovery_publisher.publish_all()

            # Publish all current states to ensure HA is in sync
            self.publish_all_states()

            # Start periodic status publishing loop if not already running
            if self.status_thread is None or not self.status_thread.is_alive():
                self._stop_status_loop = False
                import threading # Local import for the thread
                self.status_thread = threading.Thread(target=self._periodic_status_publisher_loop, daemon=True)
                self.status_thread.start()

        else:
            self.connected = False
            print(f"[MQTT_ERROR] Connection failed with code {rc}. Check MQTT broker settings and credentials.")
            # Common rc codes:
            # 1: Connection refused - incorrect protocol version
            # 2: Connection refused - invalid client identifier
            # 3: Connection refused - server unavailable
            # 4: Connection refused - bad username or password
            # 5: Connection refused - not authorised

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        # LWT should handle availability, but we can log it.
        if rc != 0:
            print(f"[MQTT_WARNING] Unexpectedly disconnected from broker (rc={rc}). Will attempt to reconnect automatically.")
        else:
            if self.debug:
                 print("[MQTT] Disconnected cleanly.")
        # Paho MQTT client handles reconnection automatically by default if loop_start() is used.

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload_str = msg.payload.decode('utf-8')
            if self.debug:
                print(f"[MQTT_RX] Received message on topic '{topic}': {payload_str}")
            self.state.update_mqtt_timestamp() # Message received, update timestamp

            # --- Handle Control Commands ---
            if topic == f"{CONTROL_TOPIC_BASE}/setpoint/set":
                self.state.set_setpoint(payload_str)
                self.publish_state(f"{CONTROL_TOPIC_BASE}/setpoint", self.state.setpoint) # Echo back new state

            elif topic == f"{CONTROL_TOPIC_BASE}/differential/set":
                self.state.set_differential(payload_str)
                self.publish_state(f"{CONTROL_TOPIC_BASE}/differential", self.state.differential)

            elif topic.endswith("_override/set"):
                # e.g., chiller/control/pump_override/set
                device_key = topic.split('/')[-2].replace("_override", "") # "pump", "chiller", "cooling"
                # Map "auto" from HA to None for state.set_override
                # state.set_override expects "on", "off", or None (which clears override)
                effective_payload = None if payload_str.lower() == "auto" else payload_str
                self.state.set_override(device_key, effective_payload)
                # Echo back the state to the non-/set topic
                # state.get_override returns "on", "off", or None. MQTT client needs to publish "auto" for None.
                current_override_state = self.state.get_override(device_key)
                publish_payload = "auto" if current_override_state is None else current_override_state
                self.publish_state(f"{CONTROL_TOPIC_BASE}/{device_key}_override", publish_payload)


            elif topic == f"{CONTROL_TOPIC_BASE}/resend_discovery":
                if payload_str.upper() == "PRESS": # Or check if payload is not empty
                    if self.debug: print("[MQTT] Resend discovery command received.")
                    if self.discovery_publisher:
                        self.discovery_publisher.publish_all()
                    self.publish_availability("online") # Re-affirm availability
                    self.publish_all_states() # Re-publish all states

            elif topic == TOPIC_EXTERNAL_AHU_CALL:
                # Any non-empty payload on this topic is considered a call.
                # The exact payload can be "ON", "OFF", a JSON, etc.
                # For simplicity, any message means "call is active", timeout handled by SystemState.
                if payload_str: # or specific payload check like payload_str.upper() == "ON"
                    if self.debug: print(f"[MQTT] External AHU call received: {payload_str}")
                    self.state.update_ahu_call(is_manual_cooling=False)
                # If an "OFF" message is desired, that logic would need to be added here.

        except json.JSONDecodeError:
            if self.debug: print(f"[MQTT_ERROR] JSON decode error for payload on topic {topic}: {payload_str}")
        except Exception as e:
            print(f"[MQTT_ERROR] Error processing message on topic {topic}: {e}")


    def publish_state(self, topic, value, retain=True, qos=1):
        """Helper to publish a single state value."""
        if not self.connected:
            if self.debug: print(f"[MQTT_WARN] Not connected, cannot publish to {topic}")
            return
        try:
            payload = str(value)
            self.client.publish(topic, payload, qos=qos, retain=retain)
            if self.debug:
                print(f"[MQTT_TX] Published to {topic}: {payload} (retain={retain})")
        except Exception as e:
            print(f"[MQTT_ERROR] Failed to publish to {topic}: {e}")

    def publish_availability(self, status="online"):
        """Publishes the system's availability (online/offline)."""
        self.publish_state(TOPIC_AVAILABILITY, status, retain=True)

    def publish_all_states(self):
        """Publishes all relevant states to MQTT. Called on connect and periodically."""
        if not self.connected:
            return

        if self.debug:
            print("[MQTT] Publishing all current states...")

        # Setpoint and Differential (control topics also act as state topics for HA numbers)
        self.publish_state(f"{CONTROL_TOPIC_BASE}/setpoint", self.state.setpoint)
        self.publish_state(f"{CONTROL_TOPIC_BASE}/differential", self.state.differential)

        # Overrides (control topics also act as state topics for HA selects)
        for device in ["pump", "chiller", "cooling"]:
            override_val = self.state.get_override(device) # "on", "off", or None
            publish_payload = "auto" if override_val is None else override_val
            self.publish_state(f"{CONTROL_TOPIC_BASE}/{device}_override", publish_payload)

        # Temperature Sensors
        temps = self.state.get_all_temperatures() # internal_key -> temp_value or None
        for key, temp_val in temps.items():
            if temp_val is not None: # Only publish if we have a valid reading
                self.publish_state(f"{TEMP_TOPIC_BASE}/{key}", f"{temp_val:.2f}", retain=False, qos=0) # Temps are not retained typically
            # else: HA sensor will show last value or become unavailable based on expire_after

        # Binary Status Sensors (from state.py, published as "ON" or "OFF")
        self.publish_state(f"{STATUS_TOPIC_BASE}/cooling_call_active", "ON" if self.state.is_ahu_calling else "OFF")
        self.publish_state(f"{STATUS_TOPIC_BASE}/pump_relay_state", "ON" if self.state.get_relay_state("pump") else "OFF")
        self.publish_state(f"{STATUS_TOPIC_BASE}/condenser_relay_state", "ON" if self.state.get_relay_state("condenser") else "OFF")
        self.publish_state(f"{STATUS_TOPIC_BASE}/critical_sensor_fault", "ON" if self.state.is_critical_sensor_fault() else "OFF")

        # Overall Availability
        self.publish_availability("online") # Re-affirm

        if self.debug:
            print("[MQTT] Finished publishing all current states.")


    def _periodic_status_publisher_loop(self):
        """Periodically publishes all states to keep HA in sync."""
        if self.debug:
            print("[MQTT] Starting periodic status publisher loop.")
        while not self._stop_status_loop and self.connected:
            if self.state.should_publish_status(): # Check if it's time via state.py timer
                self.publish_all_states()
            time.sleep(1) # Check every second if it's time to publish
        if self.debug:
            print("[MQTT] Exiting periodic status publisher loop.")


    # --- Specific publish methods for sensor updates (called by sensor_loop) ---
    def publish_temperature(self, internal_sensor_key, temp_value):
        """Publishes a single temperature reading."""
        if temp_value is not None and isinstance(temp_value, (float, int)):
            # Topic e.g., chiller/sensor/supply
            self.publish_state(f"{TEMP_TOPIC_BASE}/{internal_sensor_key}", f"{temp_value:.2f}", retain=False, qos=0)
        else:
            if self.debug:
                print(f"[MQTT] Invalid temp value for {internal_sensor_key}, not publishing: {temp_value}")