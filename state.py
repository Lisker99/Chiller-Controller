# state.py

import time
import json
import os
import math
from config import (
    DEBUG_LOGGING_ENABLED,
    FALLBACK_SETPOINT_F,
    INITIAL_DIFFERENTIAL_F,
    SENSOR_IDS, 
    CRITICAL_SENSOR_KEYS, 
    SETPOINT_FILE,
    OPERATIONAL_PARAMS_FILE,
    OVERRIDES_FILE,
    AHU_CALL_TIMEOUT_SEC, 
    PUMP_POST_PURGE_DURATION_SEC,
    MQTT_FAILSAFE_TIMEOUT_SEC, # <--- ADD THIS LINE
    MQTT_STATUS_PUBLISH_INTERVAL_SEC # <--- THIS WAS ALREADY USED, SO GOOD TO KEEP IT EXPLICIT
)

print(f"[DEBUG_STATE_PY] SENSOR_IDS as seen by state.py: {SENSOR_IDS}, type: {type(SENSOR_IDS)}")

class SystemState:
    def __init__(self):
        self.debug = DEBUG_LOGGING_ENABLED

        # --- Operational State ---
        self.setpoint = FALLBACK_SETPOINT_F
        self.differential = INITIAL_DIFFERENTIAL_F

        # Override states (string "on", "off", or None for auto)
        self.manual_pump_override = None
        self.manual_chiller_override = None
        self.manual_cooling_override = None # This simulates an AHU call

        # Timestamps (seconds since epoch)
        self.last_ahu_call_time = 0         # Last time ANY AHU call (real or simulated) was active
        self.last_real_ahu_call_time = 0    # Last time a REAL AHU call was received (used for post-purge trigger)
        self.pump_post_purge_end_time = 0   # Timestamp when pump post-purge ends
        self.condenser_last_off_time = time.time() # Assume off at start for min_off_time
        self.last_mqtt_message_time = time.time() # Assume connected at start
        self.last_state_publish_time = 0 # For periodic status publishing

        # System Status Flags
        self.mqtt_comms_lost = False
        self.critical_sensor_fault = True # Assume fault until sensors read successfully
        self.is_ahu_calling = False # Flag to track current AHU call state (real or simulated)

        # --- Sensor Data ---
        # sensor_temps: Maps internal_key (e.g., "supply") -> latest temperature reading (float or None)
        self.sensor_temps = {}
        # sensor_readings_valid: Maps internal_key -> bool (True if last read was valid)
        self.sensor_readings_valid = {}


        # --- Relay State Cache ---
        # To track the *commanded* state of relays (True=ON, False=OFF)
        self._relay_states = {
            "pump": False,
            "condenser": False # Renamed from "chiller" to match config/gpio_settings
        }

        # --- Load Persistent State ---
        self.load_setpoint()
        self.load_operational_params() # For differential
        self.load_overrides()

        # Initialize sensor validity flags based on config, assume invalid initially
        if not SENSOR_IDS:
             if self.debug:
                 print("[STATE] No sensor IDs configured. Critical sensor fault is true.")
             self.critical_sensor_fault = True
        else:
            for key in SENSOR_IDS.keys():
                self.sensor_readings_valid[key] = False # Assume invalid until first successful read
            # Critical fault starts True if critical sensors are configured
            if any(key in SENSOR_IDS for key in CRITICAL_SENSOR_KEYS):
                 self.critical_sensor_fault = True
            else:
                 self.critical_sensor_fault = False # No critical sensors configured, assume no fault here


        if self.debug:
            print(f"[STATE] Initialized with setpoint={self.setpoint}°F, differential={self.differential}°F")
            print(f"[STATE] Initial overrides: Pump={self.manual_pump_override}, Chiller={self.manual_chiller_override}, Cooling={self.manual_cooling_override}")


    # --- Persistence Methods ---

    def load_setpoint(self):
        """Read saved setpoint from file."""
        if os.path.exists(SETPOINT_FILE):
            try:
                with open(SETPOINT_FILE, "r") as f:
                    data = json.load(f)
                    loaded_setpoint = data.get("setpoint")
                    if isinstance(loaded_setpoint, (int, float)):
                        self.setpoint = float(loaded_setpoint)
                        if self.debug:
                            print(f"[STATE] Loaded setpoint: {self.setpoint}°F from {SETPOINT_FILE}")
                    else:
                         if self.debug:
                              print(f"[STATE] Invalid setpoint value in {SETPOINT_FILE}. Using default {self.setpoint}°F.")
            except Exception as e:
                if self.debug:
                    print(f"[STATE] Failed to load setpoint from {SETPOINT_FILE}: {e}. Using default {self.setpoint}°F.")
        else:
            if self.debug:
                print(f"[STATE] {SETPOINT_FILE} not found. Using default setpoint {self.setpoint}°F.")

    def save_setpoint(self):
        """Write setpoint to disk."""
        try:
            # Ensure persistence directory exists if not writing to script dir
            # os.makedirs(os.path.dirname(SETPOINT_FILE), exist_ok=True) # Uncomment if using separate data dir
            with open(SETPOINT_FILE, "w") as f:
                json.dump({"setpoint": self.setpoint}, f, indent=2)
            if self.debug:
                print(f"[STATE] Saved setpoint {self.setpoint}°F to {SETPOINT_FILE}")
        except Exception as e:
            print(f"[STATE] Failed to save setpoint to {SETPOINT_FILE}: {e}")

    def load_operational_params(self):
        """Load saved operational parameters (like differential) from file."""
        if os.path.exists(OPERATIONAL_PARAMS_FILE):
            try:
                with open(OPERATIONAL_PARAMS_FILE, "r") as f:
                    data = json.load(f)
                    loaded_differential = data.get("differential")
                    if isinstance(loaded_differential, (int, float)):
                         self.differential = float(loaded_differential)
                         if self.debug:
                              print(f"[STATE] Loaded differential: {self.differential}°F from {OPERATIONAL_PARAMS_FILE}")
                    else:
                         if self.debug:
                              print(f"[STATE] Invalid differential value in {OPERATIONAL_PARAMS_FILE}. Using default {self.differential}°F.")

            except Exception as e:
                if self.debug:
                    print(f"[STATE] Failed to load operational parameters from {OPERATIONAL_PARAMS_FILE}: {e}. Using defaults.")
        else:
            if self.debug:
                print(f"[STATE] {OPERATIONAL_PARAMS_FILE} not found. Using default differential {self.differential}°F.")


    def save_operational_params(self):
        """Save current operational parameters (like differential) to disk."""
        try:
            # os.makedirs(os.path.dirname(OPERATIONAL_PARAMS_FILE), exist_ok=True) # Uncomment if using separate data dir
            with open(OPERATIONAL_PARAMS_FILE, "w") as f:
                json.dump({
                    "differential": self.differential
                }, f, indent=2)
            if self.debug:
                print(f"[STATE] Saved operational parameters to {OPERATIONAL_PARAMS_FILE}")
        except Exception as e:
            print(f"[STATE] Failed to save operational parameters to {OPERATIONAL_PARAMS_FILE}: {e}")

    def load_overrides(self):
        """Load saved manual override states from JSON file."""
        if os.path.exists(OVERRIDES_FILE):
            try:
                with open(OVERRIDES_FILE, "r") as f:
                    data = json.load(f)
                # Use get with explicit default=None for each override
                self.manual_pump_override = data.get("manual_pump_override", None)
                self.manual_chiller_override = data.get("manual_chiller_override", None)
                self.manual_cooling_override = data.get("manual_cooling_override", None)

                if self.debug:
                    print(f"[STATE] Loaded overrides from {OVERRIDES_FILE}: "
                          f"pump={self.manual_pump_override}, chiller={self.manual_chiller_override}, cooling={self.manual_cooling_override}")
            except Exception as e:
                if self.debug:
                    print(f"[STATE] Failed to load overrides from {OVERRIDES_FILE}: {e}. Using defaults (None).")
        else:
            if self.debug:
                print(f"[STATE] {OVERRIDES_FILE} not found, using defaults (None).")

    def save_overrides(self):
        """Save current manual override states to JSON file."""
        try:
            # os.makedirs(os.path.dirname(OVERRIDES_FILE), exist_ok=True) # Uncomment if using separate data dir
            with open(OVERRIDES_FILE, "w") as f:
                json.dump({
                    "manual_pump_override": self.manual_pump_override,
                    "manual_chiller_override": self.manual_chiller_override,
                    "manual_cooling_override": self.manual_cooling_override
                }, f, indent=2)
            if self.debug:
                print(f"[STATE] Saved overrides to {OVERRIDES_FILE}")
        except Exception as e:
            print(f"[STATE] Failed to save overrides to {OVERRIDES_FILE}: {e}")


    # --- Setpoint/Differential/Override Control Methods ---

    def set_setpoint(self, value):
        """Update and persist setpoint."""
        try:
            new_setpoint = float(value)
            if self.setpoint != new_setpoint:
                self.setpoint = new_setpoint
                self.save_setpoint()
                if self.debug:
                    print(f"[STATE] Setpoint updated to {self.setpoint}°F")
            else:
                if self.debug:
                     print(f"[STATE] Setpoint received {new_setpoint}°F, no change.")
        except (ValueError, TypeError):
            if self.debug:
                print(f"[STATE] Invalid setpoint value received: {value}")

    def set_differential(self, val):
        """Set differential temperature (from MQTT)."""
        try:
            new_differential = float(val)
            # Add bounds checking if needed, e.g. if not 0.0 <= new_differential <= 10.0: return
            if self.differential != new_differential:
                self.differential = new_differential
                self.save_operational_params() # Save differential with other params
                if self.debug:
                    print(f"[STATE] Differential set to {self.differential}°F")
            else:
                if self.debug:
                     print(f"[STATE] Differential received {new_differential}°F, no change.")
        except (ValueError, TypeError):
            if self.debug:
                print(f"[STATE] Invalid differential value received: {val}")

    def set_override(self, device, value):
        """
        Set manual override state for a device.
        Accepted values:
          - "on"  (string, case-insensitive)
          - "off" (string, case-insensitive)
          - "clear" (string, case-insensitive) or None mean 'auto'
        """
        if device not in ["pump", "chiller", "cooling"]:
            if self.debug:
                print(f"[STATE] Invalid override device: {device}")
            return

        # Map string values to internal state representation ("on", "off", None)
        parsed_value = None
        if isinstance(value, str):
            val_lower = value.strip().lower()
            if val_lower == "on":
                parsed_value = "on"
            elif val_lower == "off":
                parsed_value = "off"
            # "clear" or other strings result in None

        # Check if the state is actually changing before setting/saving
        current_override = getattr(self, f"manual_{device}_override")
        if current_override != parsed_value:
            setattr(self, f"manual_{device}_override", parsed_value)
            if self.debug:
                print(f"[STATE] Override set: {device} = {parsed_value}")
            self.save_overrides() # Only save if state changed


    def get_override(self, device):
        """Get manual override state for a device (returns "on", "off", or None)."""
        if device not in ["pump", "chiller", "cooling"]:
            return None
        return getattr(self, f"manual_{device}_override", None)

    def get_all_overrides(self):
        """Return dict of all override states with string "on", "off", or None."""
        return {
            "pump": self.manual_pump_override,
            "chiller": self.manual_chiller_override,
            "cooling": self.manual_cooling_override
        }

    # --- Call/Demand Methods ---

    def update_ahu_call(self, is_manual_cooling=False):
        """Record an AHU or manual cooling call time."""
        now = time.time()
        self.last_ahu_call_time = now # Tracks any kind of call
        if not is_manual_cooling:
            self.last_real_ahu_call_time = now # Tracks only real AHU calls

        # If a call starts (either type), cancel any pump post-purge
        # This needs to happen regardless of the previous state of is_ahu_calling
        self.pump_post_purge_end_time = 0

        if self.debug:
            call_type = "Manual Cooling Override" if is_manual_cooling else "Real AHU"
            print(f"[STATE] {call_type} call received/updated at {now}")

    def check_for_call_timeout(self):
        """Check if AHU call has timed out and update flag/post-purge."""
        now = time.time()
        # Determine if there is *currently* an active call signal (real or simulated)
        call_is_currently_signaled = False
        # 1. Check for recent real AHU call based on last_ahu_call_time (covers external_ahu_call_topic)
        if (now - self.last_ahu_call_time) < AHU_CALL_TIMEOUT_SEC:
             call_is_currently_signaled = True
             if self.debug: print(f"[STATE] AHU call active based on last_ahu_call_time ({now - self.last_ahu_call_time:.1f}s ago) < {AHU_CALL_TIMEOUT_SEC}s")
        # 2. Check for manual cooling override
        if self.get_override("cooling") == "on":
             call_is_currently_signaled = True
             if self.debug: print("[STATE] AHU call active based on manual cooling override 'on'.")


        # Update internal flag
        if self.is_ahu_calling != call_is_currently_signaled:
            if call_is_currently_signaled:
                 if self.debug: print("[STATE] AHU Calling State: Turned ON")
            else:
                 # Call state transitioned from ON to OFF - START POST-PURGE
                 self.pump_post_purge_end_time = now + PUMP_POST_PURGE_DURATION_SEC
                 if self.debug: print(f"[STATE] AHU Calling State: Turned OFF. Starting pump post-purge until {self.pump_post_purge_end_time:.0f}s.")
            self.is_ahu_calling = call_is_currently_signaled

        # Check if post-purge is currently active
        pump_post_purging_active = (now < self.pump_post_purge_end_time)
        if self.debug:
             if pump_post_purging_active:
                  print(f"[STATE] Pump Post-Purge Active: Yes (ends in {self.pump_post_purge_end_time - now:.1f}s)")
             else:
                  print("[STATE] Pump Post-Purge Active: No")

        # Overall call state for pump logic: AHU Call active OR Post-purge active
        overall_pump_demand = self.is_ahu_calling or pump_post_purging_active

        if self.debug:
             print(f"[STATE] is_ahu_calling: {self.is_ahu_calling} | pump_post_purging_active: {pump_post_purging_active} | Overall pump demand: {overall_pump_demand}")

        return overall_pump_demand # This is the signal the controller pump logic needs

    # --- Sensor Data Methods ---

    def update_temperatures(self, temps_by_key):
        """
        Update sensor temperatures from a dict keyed by internal_key (e.g., "supply").
        Also updates sensor validity status and critical sensor fault flag.
        """
        any_critical_sensor_invalid = False

        for key, val in temps_by_key.items():
            is_valid = isinstance(val, (int, float)) and not math.isnan(val)

            if is_valid:
                self.sensor_temps[key] = val
                self.sensor_readings_valid[key] = True
                if self.debug:
                     print(f"[STATE] Temp valid for '{key}': {val}°F")
            else:
                # Keep old value if invalid, just update validity flag
                self.sensor_readings_valid[key] = False
                if self.debug:
                    print(f"[STATE] Temp invalid for '{key}': {val}")

            if key in CRITICAL_SENSOR_KEYS and not self.sensor_readings_valid.get(key, False):
                any_critical_sensor_invalid = True
                if self.debug:
                     print(f"[STATE] Critical sensor '{key}' is invalid.")


        # Update critical sensor fault flag
        # Fault is true if any *configured* critical sensor is currently invalid
        if not CRITICAL_SENSOR_KEYS: # If no critical sensors defined, no fault
             self.critical_sensor_fault = False
        else:
            # Check specifically only the configured critical sensors
            critical_sensors_are_valid = all(
                self.sensor_readings_valid.get(key, False)
                for key in CRITICAL_SENSOR_KEYS
            )
            self.critical_sensor_fault = not critical_sensors_are_valid
            if self.debug:
                 print(f"[STATE] Critical sensor fault status: {self.critical_sensor_fault}")


    def get_sensor_temp(self, internal_key):
        """Get temperature by internal_key (e.g., "supply"), or None if unknown or invalid."""
        if not self.sensor_readings_valid.get(internal_key, False):
            return None # Return None if not valid
        return self.sensor_temps.get(internal_key) # Return the value if valid

    def get_all_temperatures(self):
        """Returns a copy of the current sensor temperatures (internal_key -> value). Includes invalid."""
        return dict(self.sensor_temps) # Or filter to return only valid ones if preferred

    def is_critical_sensor_fault(self):
        """Returns True if any critical sensor is currently invalid."""
        return self.critical_sensor_fault

    # --- Relay State Cache Methods ---

    def get_relay_state(self, device):
        """Returns last commanded relay state (True=ON, False=OFF) or None if unknown."""
        # Use "condenser" as key internally now
        if device == "chiller": device = "condenser"
        return self._relay_states.get(device, None)

    def set_relay_state(self, device, value):
        """Stores last commanded relay state for device (used to prevent flicker & update timers)."""
        # Use "condenser" as key internally now
        if device == "chiller": device = "condenser"

        if device not in ["pump", "condenser"]:
            if self.debug:
                print(f"[STATE] Invalid device key for set_relay_state: {device}")
            return

        old_state = self._relay_states.get(device)
        new_state = bool(value) # Ensure boolean

        if old_state != new_state:
            self._relay_states[device] = new_state
            if self.debug:
                print(f"[STATE] Relay state cached: {device} = {new_state}")

            # Special handling for condenser turning OFF
            if device == "condenser" and old_state is True and new_state is False:
                self.condenser_last_off_time = time.time()
                if self.debug:
                    print(f"[STATE] Condenser turned OFF. Last OFF time updated to {self.condenser_last_off_time:.0f}s.")
        # else: State didn't change, do nothing


    # --- MQTT Fail-Safe Methods ---

    def update_mqtt_timestamp(self):
        """Called by MQTT client on message receipt or connect."""
        now = time.time()
        self.last_mqtt_message_time = now
        if self.mqtt_comms_lost: # If we were previously lost, note recovery
             self.mqtt_comms_lost = False
             if self.debug:
                  print(f"[STATE] MQTT communication restored at {now:.0f}s.")
        # else: MQTT is fine, just update timestamp

    def check_mqtt_failsafe(self):
        """Called periodically to check if MQTT comms are lost."""
        now = time.time()
        if (now - self.last_mqtt_message_time) > MQTT_FAILSAFE_TIMEOUT_SEC:
            if not self.mqtt_comms_lost: # Only log when it transitions to lost
                self.mqtt_comms_lost = True
                if self.debug:
                    print(f"[STATE] MQTT communication lost! No messages for > {MQTT_FAILSAFE_TIMEOUT_SEC}s.")
        # else: MQTT is fine or was already lost


    # --- Periodic Status Publish Timer ---
    def should_publish_status(self):
        """Check if it's time to publish status updates periodically."""
        now = time.time()
        if (now - self.last_state_publish_time) >= MQTT_STATUS_PUBLISH_INTERVAL_SEC:
            self.last_state_publish_time = now
            return True
        return False