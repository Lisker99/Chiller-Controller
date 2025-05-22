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
    MQTT_STATUS_PUBLISH_INTERVAL_SEC 
)
from state import SystemState 
from mqtt_discovery import DiscoveryPublisher

class MQTTClient:
    def __init__(self, state: SystemState):
        self.state = state
        self.debug = DEBUG_LOGGING_ENABLED
        self.client_id = MQTT_CLIENT_ID
        self.discovery_publisher = None 

        self.client = mqtt.Client(client_id=self.client_id)
        if MQTT_USERNAME and MQTT_PASSWORD:
            self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        self.client.will_set(TOPIC_AVAILABILITY, payload="offline", qos=1, retain=True)

        self.connected = False
        self._stop_status_loop = False
        self.status_thread = None


    def connect(self):
        if self.debug:
            print(f"[MQTT] Attempting to connect to broker {MQTT_BROKER_ADDRESS}:{MQTT_BROKER_PORT} as {self.client_id}")
        try:
            self.client.connect(MQTT_BROKER_ADDRESS, MQTT_BROKER_PORT, 60)
            self.client.loop_start() 
        except Exception as e:
            print(f"[MQTT_ERROR] Connection failed: {e}")

    def disconnect(self):
        if self.debug:
            print("[MQTT] Disconnecting from broker...")
        self._stop_status_loop = True
        if self.status_thread and self.status_thread.is_alive():
            self.status_thread.join(timeout=5) 
        self.publish_availability("offline") 
        self.client.loop_stop() 
        self.client.disconnect()
        if self.debug:
            print("[MQTT] Disconnected.")

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            if self.debug:
                print(f"[MQTT] Connected to broker successfully (rc={rc})")
            self.state.update_mqtt_timestamp() 

            self.client.subscribe(f"{CONTROL_TOPIC_BASE}/setpoint/set", qos=1)
            self.client.subscribe(f"{CONTROL_TOPIC_BASE}/differential/set", qos=1)
            self.client.subscribe(f"{CONTROL_TOPIC_BASE}/ambient_lockout_setpoint/set", qos=1) # <-- NEW SUBSCRIPTION
            self.client.subscribe(f"{CONTROL_TOPIC_BASE}/pump_override/set", qos=1)
            self.client.subscribe(f"{CONTROL_TOPIC_BASE}/chiller_override/set", qos=1) 
            self.client.subscribe(f"{CONTROL_TOPIC_BASE}/cooling_override/set", qos=1)
            self.client.subscribe(f"{CONTROL_TOPIC_BASE}/resend_discovery", qos=1)
            self.client.subscribe(TOPIC_EXTERNAL_AHU_CALL, qos=1) 

            if self.debug:
                print("[MQTT] Subscribed to command topics.")

            self.publish_availability("online")

            if not self.discovery_publisher:
                 self.discovery_publisher = DiscoveryPublisher(self.client)
            self.discovery_publisher.publish_all()

            self.publish_all_states()

            if self.status_thread is None or not self.status_thread.is_alive():
                self._stop_status_loop = False
                import threading 
                self.status_thread = threading.Thread(target=self._periodic_status_publisher_loop, daemon=True)
                self.status_thread.start()

        else:
            self.connected = False
            print(f"[MQTT_ERROR] Connection failed with code {rc}. Check MQTT broker settings and credentials.")

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        if rc != 0:
            print(f"[MQTT_WARNING] Unexpectedly disconnected from broker (rc={rc}). Will attempt to reconnect automatically.")
        else:
            if self.debug:
                 print("[MQTT] Disconnected cleanly.")

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload_str = msg.payload.decode('utf-8')
            if self.debug:
                print(f"[MQTT_RX] Received message on topic '{topic}': {payload_str}")
            self.state.update_mqtt_timestamp() 

            if topic == f"{CONTROL_TOPIC_BASE}/setpoint/set":
                self.state.set_setpoint(payload_str)
                self.publish_state(f"{CONTROL_TOPIC_BASE}/setpoint", self.state.setpoint) 

            elif topic == f"{CONTROL_TOPIC_BASE}/differential/set":
                self.state.set_differential(payload_str)
                self.publish_state(f"{CONTROL_TOPIC_BASE}/differential", self.state.differential)
            
            # <-- NEW HANDLER for Ambient Lockout Setpoint -->
            elif topic == f"{CONTROL_TOPIC_BASE}/ambient_lockout_setpoint/set":
                self.state.set_ambient_lockout_setpoint(payload_str)
                self.publish_state(f"{CONTROL_TOPIC_BASE}/ambient_lockout_setpoint", self.state.ambient_lockout_setpoint)

            elif topic.endswith("_override/set"):
                device_key = topic.split('/')[-2].replace("_override", "") 
                effective_payload = None if payload_str.lower() == "auto" else payload_str
                self.state.set_override(device_key, effective_payload)
                current_override_state = self.state.get_override(device_key)
                publish_payload = "auto" if current_override_state is None else current_override_state
                self.publish_state(f"{CONTROL_TOPIC_BASE}/{device_key}_override", publish_payload)


            elif topic == f"{CONTROL_TOPIC_BASE}/resend_discovery":
                if payload_str.upper() == "PRESS": 
                    if self.debug: print("[MQTT] Resend discovery command received.")
                    if self.discovery_publisher:
                        self.discovery_publisher.publish_all()
                    self.publish_availability("online") 
                    self.publish_all_states() 

            elif topic == TOPIC_EXTERNAL_AHU_CALL:
                if payload_str: 
                    if self.debug: print(f"[MQTT] External AHU call received: {payload_str}")
                    self.state.update_ahu_call(is_manual_cooling=False)

        except json.JSONDecodeError:
            if self.debug: print(f"[MQTT_ERROR] JSON decode error for payload on topic {topic}: {payload_str}")
        except Exception as e:
            print(f"[MQTT_ERROR] Error processing message on topic {topic}: {e}")


    def publish_state(self, topic, value, retain=True, qos=1):
        if not self.connected:
            # if self.debug: print(f"[MQTT_WARN] Not connected, cannot publish to {topic}") # Can be spammy
            return
        try:
            payload = str(value) # Ensure payload is string
            # For boolean values that need to be "ON"/"OFF" for binary_sensors
            if isinstance(value, bool):
                payload = "ON" if value else "OFF"

            self.client.publish(topic, payload, qos=qos, retain=retain)
            if self.debug:
                print(f"[MQTT_TX] Published to {topic}: {payload} (retain={retain})")
        except Exception as e:
            print(f"[MQTT_ERROR] Failed to publish to {topic}: {e}")

    def publish_availability(self, status="online"):
        self.publish_state(TOPIC_AVAILABILITY, status, retain=True)

    def publish_all_states(self):
        if not self.connected:
            return

        if self.debug:
            print("[MQTT] Publishing all current states...")

        self.publish_state(f"{CONTROL_TOPIC_BASE}/setpoint", f"{self.state.setpoint:.1f}")
        self.publish_state(f"{CONTROL_TOPIC_BASE}/differential", f"{self.state.differential:.1f}")
        self.publish_state(f"{CONTROL_TOPIC_BASE}/ambient_lockout_setpoint", f"{self.state.ambient_lockout_setpoint:.1f}") # <-- NEW PUBLISH

        for device in ["pump", "chiller", "cooling"]:
            override_val = self.state.get_override(device) 
            publish_payload = "auto" if override_val is None else override_val
            self.publish_state(f"{CONTROL_TOPIC_BASE}/{device}_override", publish_payload)

        temps = self.state.get_all_temperatures() 
        for key, temp_val in temps.items():
            if temp_val is not None: 
                self.publish_state(f"{TEMP_TOPIC_BASE}/{key}", f"{temp_val:.2f}", retain=False, qos=0) 

        # Binary Status Sensors - publish_state handles bool to "ON"/"OFF"
        self.publish_state(f"{STATUS_TOPIC_BASE}/cooling_call_active", self.state.is_ahu_calling)
        self.publish_state(f"{STATUS_TOPIC_BASE}/pump_relay_state", self.state.get_relay_state("pump"))
        self.publish_state(f"{STATUS_TOPIC_BASE}/condenser_relay_state", self.state.get_relay_state("condenser"))
        self.publish_state(f"{STATUS_TOPIC_BASE}/critical_sensor_fault", self.state.is_critical_sensor_fault())
        self.publish_state(f"{STATUS_TOPIC_BASE}/ambient_lockout_active", self.state.ambient_lockout_active) # <-- NEW PUBLISH

        self.publish_availability("online") 

        if self.debug:
            print("[MQTT] Finished publishing all current states.")


    def _periodic_status_publisher_loop(self):
        if self.debug:
            print("[MQTT] Starting periodic status publisher loop.")
        while not self._stop_status_loop and self.connected:
            if self.state.should_publish_status(): 
                self.publish_all_states()
            time.sleep(1) 
        if self.debug:
            print("[MQTT] Exiting periodic status publisher loop.")


    def publish_temperature(self, internal_sensor_key, temp_value):
        if temp_value is not None and isinstance(temp_value, (float, int)):
            self.publish_state(f"{TEMP_TOPIC_BASE}/{internal_sensor_key}", f"{temp_value:.2f}", retain=False, qos=0)
        # else: No need to log invalid temp here, state.py or sensors.py might do it.