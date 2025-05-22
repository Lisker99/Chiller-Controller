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
    MQTT_FAILSAFE_TIMEOUT_SEC,
    MQTT_STATUS_PUBLISH_INTERVAL_SEC,
    # Ambient Lockout Config Constants
    INITIAL_AMBIENT_LOCKOUT_SETPOINT_F,         # <-- NEW IMPORT
    AMBIENT_LOCKOUT_DEADBAND_F,                 # <-- NEW IMPORT
    AMBIENT_LOCKOUT_DEBOUNCE_DURATION_SEC       # <-- NEW IMPORT
)

class SystemState:
    def __init__(self):
        self.debug = DEBUG_LOGGING_ENABLED

        # --- Operational State ---
        self.setpoint = FALLBACK_SETPOINT_F
        self.differential = INITIAL_DIFFERENTIAL_F
        self.ambient_lockout_setpoint = INITIAL_AMBIENT_LOCKOUT_SETPOINT_F # <-- NEW state variable

        # Override states (string "on", "off", or None for auto)
        self.manual_pump_override = None
        self.manual_chiller_override = None 
        self.manual_cooling_override = None 

        # Timestamps (seconds since epoch)
        self.last_ahu_call_time = 0         
        self.last_real_ahu_call_time = 0    
        self.pump_post_purge_end_time = 0   
        self.condenser_last_off_time = time.time() 
        self.last_mqtt_message_time = time.time() 
        self.last_state_publish_time = 0 

        # System Status Flags
        self.mqtt_comms_lost = False
        self.critical_sensor_fault = True 
        self.is_ahu_calling = False 
        self.ambient_lockout_active = False # <-- NEW state flag
        
        # Timestamps for ambient lockout debounce logic
        self.ambient_temp_below_lockout_setpoint_since = 0  # <-- NEW: Unix timestamp
        self.ambient_temp_above_lockout_release_since = 0 # <-- NEW: Unix timestamp


        # --- Sensor Data ---
        self.sensor_temps = {}
        self.sensor_readings_valid = {}


        # --- Relay State Cache ---
        self._relay_states = {
            "pump": False,
            "condenser": False 
        }

        # --- Load Persistent State ---
        self.load_setpoint()
        self.load_operational_params() # For differential AND ambient_lockout_setpoint
        self.load_overrides()

        if not SENSOR_IDS:
             if self.debug:
                 print("[STATE] No sensor IDs configured. Critical sensor fault is true.")
             self.critical_sensor_fault = True
        else:
            for key in SENSOR_IDS.keys():
                self.sensor_readings_valid[key] = False 
            if any(key in SENSOR_IDS for key in CRITICAL_SENSOR_KEYS):
                 self.critical_sensor_fault = True
            else:
                 self.critical_sensor_fault = False 


        if self.debug:
            print(f"[STATE] Initialized with setpoint={self.setpoint}°F, differential={self.differential}°F")
            print(f"[STATE] Initial Ambient Lockout Setpoint: {self.ambient_lockout_setpoint}°F") # <-- NEW LOG
            print(f"[STATE] Initial overrides: Pump={self.manual_pump_override}, Chiller={self.manual_chiller_override}, Cooling={self.manual_cooling_override}")


    # --- Persistence Methods ---

    def load_setpoint(self):
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
        try:
            with open(SETPOINT_FILE, "w") as f:
                json.dump({"setpoint": self.setpoint}, f, indent=2)
            if self.debug:
                print(f"[STATE] Saved setpoint {self.setpoint}°F to {SETPOINT_FILE}")
        except Exception as e:
            print(f"[STATE] Failed to save setpoint to {SETPOINT_FILE}: {e}")

    def load_operational_params(self):
        if os.path.exists(OPERATIONAL_PARAMS_FILE):
            try:
                with open(OPERATIONAL_PARAMS_FILE, "r") as f:
                    data = json.load(f)
                
                loaded_differential = data.get("differential")
                if isinstance(loaded_differential, (int, float)):
                        self.differential = float(loaded_differential)
                        if self.debug:
                            print(f"[STATE] Loaded differential: {self.differential}°F from {OPERATIONAL_PARAMS_FILE}")
                else: # Use initial if not found or invalid in file
                        self.differential = INITIAL_DIFFERENTIAL_F
                        if self.debug:
                            print(f"[STATE] Invalid/missing differential in {OPERATIONAL_PARAMS_FILE}. Using initial default {self.differential}°F.")
                
                # Load Ambient Lockout Setpoint <-- NEW
                loaded_ambient_lockout = data.get("ambient_lockout_setpoint")
                if isinstance(loaded_ambient_lockout, (int, float)):
                    self.ambient_lockout_setpoint = float(loaded_ambient_lockout)
                    if self.debug:
                        print(f"[STATE] Loaded ambient lockout setpoint: {self.ambient_lockout_setpoint}°F from {OPERATIONAL_PARAMS_FILE}")
                else: # Use initial if not found or invalid in file
                    self.ambient_lockout_setpoint = INITIAL_AMBIENT_LOCKOUT_SETPOINT_F
                    if self.debug:
                        print(f"[STATE] Invalid/missing ambient lockout setpoint in {OPERATIONAL_PARAMS_FILE}. Using initial default {self.ambient_lockout_setpoint}°F.")

            except Exception as e:
                if self.debug:
                    print(f"[STATE] Failed to load operational parameters from {OPERATIONAL_PARAMS_FILE}: {e}. Using defaults.")
                    self.differential = INITIAL_DIFFERENTIAL_F # Ensure defaults on error
                    self.ambient_lockout_setpoint = INITIAL_AMBIENT_LOCKOUT_SETPOINT_F
        else:
            if self.debug:
                print(f"[STATE] {OPERATIONAL_PARAMS_FILE} not found. Using default differential {self.differential}°F and ambient lockout {self.ambient_lockout_setpoint}°F.")
            self.differential = INITIAL_DIFFERENTIAL_F # Ensure defaults if file not found
            self.ambient_lockout_setpoint = INITIAL_AMBIENT_LOCKOUT_SETPOINT_F


    def save_operational_params(self):
        try:
            with open(OPERATIONAL_PARAMS_FILE, "w") as f:
                json.dump({
                    "differential": self.differential,
                    "ambient_lockout_setpoint": self.ambient_lockout_setpoint # <-- NEW
                }, f, indent=2)
            if self.debug:
                print(f"[STATE] Saved operational parameters to {OPERATIONAL_PARAMS_FILE}")
        except Exception as e:
            print(f"[STATE] Failed to save operational parameters to {OPERATIONAL_PARAMS_FILE}: {e}")

    def load_overrides(self):
        if os.path.exists(OVERRIDES_FILE):
            try:
                with open(OVERRIDES_FILE, "r") as f:
                    data = json.load(f)
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
        try:
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


    # --- Setpoint/Differential/Override/Lockout Control Methods ---

    def set_setpoint(self, value):
        try:
            new_setpoint = float(value)
            if self.setpoint != new_setpoint:
                self.setpoint = new_setpoint
                self.save_setpoint()
                if self.debug:
                    print(f"[STATE] Setpoint updated to {self.setpoint}°F")
            # else: No change, no log needed unless verbose debug
        except (ValueError, TypeError):
            if self.debug:
                print(f"[STATE] Invalid setpoint value received: {value}")

    def set_differential(self, val):
        try:
            new_differential = float(val)
            if self.differential != new_differential:
                self.differential = new_differential
                self.save_operational_params() 
                if self.debug:
                    print(f"[STATE] Differential set to {self.differential}°F")
        except (ValueError, TypeError):
            if self.debug:
                print(f"[STATE] Invalid differential value received: {val}")

    # <-- NEW METHOD for Ambient Lockout Setpoint -->
    def set_ambient_lockout_setpoint(self, value):
        """Update and persist ambient lockout setpoint."""
        try:
            new_lockout_setpoint = float(value)
            # Add reasonable bounds if desired, e.g., 30 to 80 °F
            # if not (30.0 <= new_lockout_setpoint <= 80.0):
            #     if self.debug: print(f"[STATE] Ambient lockout setpoint {new_lockout_setpoint} out of bounds.")
            #     return

            if self.ambient_lockout_setpoint != new_lockout_setpoint:
                self.ambient_lockout_setpoint = new_lockout_setpoint
                self.save_operational_params() # Persist it
                if self.debug:
                    print(f"[STATE] Ambient Lockout Setpoint updated to {self.ambient_lockout_setpoint}°F")
                # Reset debounce timers when setpoint changes to re-evaluate immediately
                self.ambient_temp_below_lockout_setpoint_since = 0
                self.ambient_temp_above_lockout_release_since = 0
                self.update_ambient_lockout_status() # Re-evaluate lockout status now

        except (ValueError, TypeError):
            if self.debug:
                print(f"[STATE] Invalid ambient lockout setpoint value received: {value}")


    def set_override(self, device, value):
        if device not in ["pump", "chiller", "cooling"]:
            if self.debug:
                print(f"[STATE] Invalid override device: {device}")
            return

        parsed_value = None
        if isinstance(value, str):
            val_lower = value.strip().lower()
            if val_lower == "on":
                parsed_value = "on"
            elif val_lower == "off":
                parsed_value = "off"

        current_override = getattr(self, f"manual_{device}_override")
        if current_override != parsed_value:
            setattr(self, f"manual_{device}_override", parsed_value)
            if self.debug:
                print(f"[STATE] Override set: {device} = {parsed_value}")
            self.save_overrides() 


    def get_override(self, device):
        if device not in ["pump", "chiller", "cooling"]:
            return None
        return getattr(self, f"manual_{device}_override", None)

    def get_all_overrides(self):
        return {
            "pump": self.manual_pump_override,
            "chiller": self.manual_chiller_override,
            "cooling": self.manual_cooling_override
        }

    # --- Call/Demand Methods ---

    def update_ahu_call(self, is_manual_cooling=False):
        now = time.time()
        self.last_ahu_call_time = now 
        if not is_manual_cooling:
            self.last_real_ahu_call_time = now 

        self.pump_post_purge_end_time = 0

        if self.debug:
            call_type = "Manual Cooling Override" if is_manual_cooling else "Real AHU"
            print(f"[STATE] {call_type} call received/updated at {now:.0f}")

    def check_for_call_timeout(self):
        now = time.time()
        call_is_currently_signaled = False
        if (now - self.last_ahu_call_time) < AHU_CALL_TIMEOUT_SEC:
             call_is_currently_signaled = True
        if self.get_override("cooling") == "on":
             call_is_currently_signaled = True

        if self.is_ahu_calling != call_is_currently_signaled:
            if call_is_currently_signaled:
                 if self.debug: print("[STATE_CALL] AHU Calling State: Turned ON")
            else:
                 self.pump_post_purge_end_time = now + PUMP_POST_PURGE_DURATION_SEC
                 if self.debug: print(f"[STATE_CALL] AHU Calling State: Turned OFF. Starting pump post-purge until {self.pump_post_purge_end_time:.0f}")
            self.is_ahu_calling = call_is_currently_signaled

        pump_post_purging_active = (now < self.pump_post_purge_end_time)
        overall_pump_demand = self.is_ahu_calling or pump_post_purging_active
        
        if self.debug:
            # This can be very verbose, print only on change or less frequently
            # print(f"[STATE_CALL_DEBUG] is_ahu_calling: {self.is_ahu_calling} | post_purge_active: {pump_post_purging_active} | Overall pump demand: {overall_pump_demand}")
            pass

        return overall_pump_demand 

    # --- Sensor Data Methods ---

    def update_temperatures(self, temps_by_key):
        any_critical_sensor_invalid = False
        ambient_temp_updated = False # Flag to check if ambient temp was in this update

        for key, val in temps_by_key.items():
            is_valid = isinstance(val, (int, float)) and not math.isnan(val)
            if key == "ambient" and is_valid: # Check if ambient was updated
                ambient_temp_updated = True

            if is_valid:
                self.sensor_temps[key] = val
                self.sensor_readings_valid[key] = True
            else:
                self.sensor_readings_valid[key] = False

            if key in CRITICAL_SENSOR_KEYS and not self.sensor_readings_valid.get(key, False):
                any_critical_sensor_invalid = True

        if not CRITICAL_SENSOR_KEYS: 
             self.critical_sensor_fault = False
        else:
            critical_sensors_are_valid = all(
                self.sensor_readings_valid.get(key, False)
                for key in CRITICAL_SENSOR_KEYS
            )
            new_fault_state = not critical_sensors_are_valid
            if self.critical_sensor_fault != new_fault_state:
                self.critical_sensor_fault = new_fault_state
                if self.debug: print(f"[STATE] Critical sensor fault status changed to: {self.critical_sensor_fault}")
        
        # If ambient temp was part of this update, or if critical sensor status changed, re-evaluate lockout
        if ambient_temp_updated or (self.critical_sensor_fault != new_fault_state):
            self.update_ambient_lockout_status()


    def get_sensor_temp(self, internal_key):
        if not self.sensor_readings_valid.get(internal_key, False):
            return None 
        return self.sensor_temps.get(internal_key) 

    def get_all_temperatures(self):
        return dict(self.sensor_temps) 

    def is_critical_sensor_fault(self):
        return self.critical_sensor_fault

    # --- Ambient Lockout Logic ---
    def update_ambient_lockout_status(self):
        """
        Updates the ambient_lockout_active flag based on current ambient temperature,
        setpoint, deadband, and debounce duration.
        Called when ambient temp is updated or lockout setpoint changes.
        """
        now = time.time()
        ambient_temp = self.get_sensor_temp("ambient")
        
        # If ambient sensor is invalid, we cannot determine lockout status safely.
        # Default to NOT locked out to allow operation if ambient sensor fails,
        # unless a different fail-safe behavior is desired for ambient sensor failure.
        if ambient_temp is None:
            if self.ambient_lockout_active: # If it was active, turn it off due to sensor fault
                if self.debug: print("[STATE_LOCKOUT] Ambient sensor failed, disabling ambient lockout.")
                self.ambient_lockout_active = False
            # Reset debounce timers
            self.ambient_temp_below_lockout_setpoint_since = 0
            self.ambient_temp_above_lockout_release_since = 0
            return self.ambient_lockout_active

        lockout_target = self.ambient_lockout_setpoint
        release_target = lockout_target + AMBIENT_LOCKOUT_DEADBAND_F

        current_lockout_state = self.ambient_lockout_active
        new_lockout_state = current_lockout_state # Assume no change initially

        if self.debug:
             print(f"[STATE_LOCKOUT_DBG] AmbTemp:{ambient_temp:.1f}, LkSet:{lockout_target:.1f}, RlsSet:{release_target:.1f}, Debounce:{AMBIENT_LOCKOUT_DEBOUNCE_DURATION_SEC}s")

        # Check for entering lockout
        if ambient_temp < lockout_target:
            if self.ambient_temp_below_lockout_setpoint_since == 0: # Just crossed below
                self.ambient_temp_below_lockout_setpoint_since = now
                if self.debug: print(f"[STATE_LOCKOUT] Ambient ({ambient_temp:.1f}°F) below lockout setpoint ({lockout_target:.1f}°F). Starting debounce.")
            self.ambient_temp_above_lockout_release_since = 0 # Reset other timer

            if (now - self.ambient_temp_below_lockout_setpoint_since) >= AMBIENT_LOCKOUT_DEBOUNCE_DURATION_SEC:
                new_lockout_state = True # Debounce met, activate lockout
        
        # Check for exiting lockout
        elif ambient_temp > release_target:
            if self.ambient_temp_above_lockout_release_since == 0: # Just crossed above
                self.ambient_temp_above_lockout_release_since = now
                if self.debug: print(f"[STATE_LOCKOUT] Ambient ({ambient_temp:.1f}°F) above release setpoint ({release_target:.1f}°F). Starting debounce.")
            self.ambient_temp_below_lockout_setpoint_since = 0 # Reset other timer

            if (now - self.ambient_temp_above_lockout_release_since) >= AMBIENT_LOCKOUT_DEBOUNCE_DURATION_SEC:
                new_lockout_state = False # Debounce met, deactivate lockout
        
        # If temperature is within the deadband (between lockout_target and release_target)
        else: # lockout_target <= ambient_temp <= release_target
            # Reset timers if in deadband, maintaining current lockout state until one of the thresholds is crossed and held.
            self.ambient_temp_below_lockout_setpoint_since = 0
            self.ambient_temp_above_lockout_release_since = 0
            if self.debug: print(f"[STATE_LOCKOUT] Ambient ({ambient_temp:.1f}°F) in deadband. Maintaining current lockout: {self.ambient_lockout_active}. Timers reset.")


        if self.ambient_lockout_active != new_lockout_state:
            self.ambient_lockout_active = new_lockout_state
            if self.debug:
                print(f"[STATE_LOCKOUT] Ambient Lockout Active status changed to: {self.ambient_lockout_active}")
        
        return self.ambient_lockout_active


    # --- Relay State Cache Methods ---

    def get_relay_state(self, device):
        if device == "chiller": device = "condenser"
        return self._relay_states.get(device, None)

    def set_relay_state(self, device, value):
        if device == "chiller": device = "condenser"
        if device not in ["pump", "condenser"]:
            if self.debug:
                print(f"[STATE] Invalid device key for set_relay_state: {device}")
            return

        old_state = self._relay_states.get(device)
        new_state = bool(value) 

        if old_state != new_state:
            self._relay_states[device] = new_state
            if self.debug:
                print(f"[STATE_RELAY] Relay state CACHED: {device} = {new_state}")

            if device == "condenser" and old_state is True and new_state is False:
                self.condenser_last_off_time = time.time()
                if self.debug:
                    print(f"[STATE_RELAY] Condenser turned OFF. Last OFF time updated to {self.condenser_last_off_time:.0f}")


    # --- MQTT Fail-Safe Methods ---

    def update_mqtt_timestamp(self):
        now = time.time()
        self.last_mqtt_message_time = now
        if self.mqtt_comms_lost: 
             self.mqtt_comms_lost = False
             if self.debug:
                  print(f"[STATE_MQTT] MQTT communication restored at {now:.0f}")

    def check_mqtt_failsafe(self):
        now = time.time()
        if (now - self.last_mqtt_message_time) > MQTT_FAILSAFE_TIMEOUT_SEC:
            if not self.mqtt_comms_lost: 
                self.mqtt_comms_lost = True
                if self.debug:
                    print(f"[STATE_MQTT] MQTT communication lost! No messages for > {MQTT_FAILSAFE_TIMEOUT_SEC}s.")


    # --- Periodic Status Publish Timer ---
    def should_publish_status(self):
        now = time.time()
        if (now - self.last_state_publish_time) >= MQTT_STATUS_PUBLISH_INTERVAL_SEC:
            self.last_state_publish_time = now
            return True
        return False