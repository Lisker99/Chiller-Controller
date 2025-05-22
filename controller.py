# controller.py

import time
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    # print("[CONTROLLER_WARN] RPi.GPIO library not found. GPIO operations will be simulated.") # Less verbose

from config import (
    DEBUG_LOGGING_ENABLED,
    PUMP_PIN,
    CONDENSER_PIN,
    RELAY_ACTIVE_HIGH,
    CONDENSER_MIN_OFF_TIME_SEC,
    CONTROLLER_LOOP_INTERVAL_SEC
)
# SystemState is passed as an argument

_gpio_initialized = False

def _setup_gpio():
    global _gpio_initialized
    if not GPIO_AVAILABLE or _gpio_initialized:
        return GPIO_AVAILABLE

    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False) 
        initial_relay_state = GPIO.LOW if RELAY_ACTIVE_HIGH else GPIO.HIGH
        GPIO.setup(PUMP_PIN, GPIO.OUT, initial=initial_relay_state)
        GPIO.setup(CONDENSER_PIN, GPIO.OUT, initial=initial_relay_state)
        _gpio_initialized = True
        if DEBUG_LOGGING_ENABLED:
            print(f"[CONTROLLER_GPIO] GPIO initialized. Pump Pin: {PUMP_PIN}, Condenser Pin: {CONDENSER_PIN}. Initial state: {'LOW (OFF)' if RELAY_ACTIVE_HIGH else 'HIGH (OFF)'}")
        return True
    except Exception as e:
        print(f"[CONTROLLER_GPIO_ERROR] Failed to initialize GPIO: {e}")
        _gpio_initialized = False
        return False

def _set_gpio_state(pin, desired_state_on):
    if not GPIO_AVAILABLE or not _gpio_initialized:
        return

    try:
        actual_gpio_signal = (GPIO.HIGH if RELAY_ACTIVE_HIGH else GPIO.LOW) if desired_state_on else (GPIO.LOW if RELAY_ACTIVE_HIGH else GPIO.HIGH)
        GPIO.output(pin, actual_gpio_signal)
    except Exception as e:
        print(f"[CONTROLLER_GPIO_ERROR] Failed to set GPIO pin {pin}: {e}")


def cleanup_gpio():
    global _gpio_initialized
    if GPIO_AVAILABLE and _gpio_initialized:
        if DEBUG_LOGGING_ENABLED:
            print("[CONTROLLER_GPIO] Cleaning up GPIO...")
        _set_gpio_state(PUMP_PIN, False)
        _set_gpio_state(CONDENSER_PIN, False)
        GPIO.cleanup()
        _gpio_initialized = False
        if DEBUG_LOGGING_ENABLED:
            print("[CONTROLLER_GPIO] GPIO cleanup complete.")
    elif not GPIO_AVAILABLE and DEBUG_LOGGING_ENABLED: # Only log if debug enabled
        # print("[CONTROLLER_GPIO] GPIO not available, no cleanup needed.") # Less verbose
        pass


def control_loop(system_state, stop_event):
    if not _setup_gpio() and GPIO_AVAILABLE: 
        print("[CONTROLLER_ERROR] GPIO setup failed. Controller loop cannot safely operate relays.")

    if DEBUG_LOGGING_ENABLED:
        print("[CONTROLLER_LOOP] Control loop started.")

    while not stop_event.is_set():
        now = time.time()
        current_debug_session_prints = [] 

        # --- 1. Update SystemState internal timers/flags ---
        system_state.check_mqtt_failsafe() 
        # Ambient lockout status is updated in state.py when ambient temp is received or lockout setpoint changes.
        # We will retrieve the current ambient_lockout_active flag below.

        # --- 2. Get Current State, Overrides, and Sensor Data ---
        manual_pump_override = system_state.get_override("pump") 
        manual_condenser_override = system_state.get_override("chiller") 
        
        supply_temp_f = system_state.get_sensor_temp("supply")
        # Ambient temp is not directly used here for decisions yet, but state.py uses it for lockout
        # We call update_ambient_lockout_status in state.py when sensors update.
        # Here, we just need to *read* the result:
        is_ambient_lockout_active = system_state.ambient_lockout_active # Read the pre-calculated flag

        if is_ambient_lockout_active:
             current_debug_session_prints.append("[CTL_LOCKOUT] AMBIENT LOCKOUT ACTIVE.")


        # --- 3. Pump Control Logic ---
        demand_for_pump_via_call = system_state.check_for_call_timeout() # True if AHU call or post-purge
        pump_on_decision = False

        if manual_pump_override == "on":
            pump_on_decision = True
            system_state.pump_post_purge_end_time = 0 
            current_debug_session_prints.append("[CTL_PUMP] Manual override ON.")
        elif manual_pump_override == "off":
            pump_on_decision = False
            system_state.pump_post_purge_end_time = 0
            current_debug_session_prints.append("[CTL_PUMP] Manual override OFF.")
        else: # Auto mode for pump
            if is_ambient_lockout_active: # <-- AMBIENT LOCKOUT CHECK FOR PUMP
                pump_on_decision = False # If ambient lockout, no pump for auto cooling calls
                if demand_for_pump_via_call: # Log if there was demand but lockout prevented it
                    current_debug_session_prints.append("[CTL_PUMP] Auto: Demand active, but AMBIENT LOCKOUT prevents pump. Pump OFF.")
                else:
                    current_debug_session_prints.append("[CTL_PUMP] Auto: No demand. Pump OFF (Ambient lockout also active).")
            elif demand_for_pump_via_call:
                pump_on_decision = True
                current_debug_session_prints.append(f"[CTL_PUMP] Auto: Demand active (call or post-purge). Pump ON.")
            else:
                pump_on_decision = False
                current_debug_session_prints.append("[CTL_PUMP] Auto: No demand. Pump OFF.")

        # --- 4. Condenser Control Logic ---
        condenser_on_decision = False
        is_condenser_currently_commanded_off = not system_state.get_relay_state("condenser")

        if manual_condenser_override == "on":
            if pump_on_decision: # Condenser manual ON only if pump is (or will be) ON
                condenser_on_decision = True
                current_debug_session_prints.append("[CTL_COND] Manual override ON (Pump is ON). Ambient lockout BYPASSED by manual override.")
            else:
                condenser_on_decision = False
                current_debug_session_prints.append("[CTL_COND] Manual override ON attempt, but Pump is OFF. Condenser OFF.")
        elif manual_condenser_override == "off":
            condenser_on_decision = False
            current_debug_session_prints.append("[CTL_COND] Manual override OFF.")
        # Auto mode for condenser (only if not manually overridden)
        elif not pump_on_decision:
            condenser_on_decision = False
            current_debug_session_prints.append("[CTL_COND] Auto: Pump OFF, so Condenser OFF.")
        elif is_ambient_lockout_active: # <-- AMBIENT LOCKOUT CHECK FOR CONDENSER (AUTO)
            condenser_on_decision = False
            current_debug_session_prints.append("[CTL_COND] Auto: AMBIENT LOCKOUT active. Condenser OFF.")
        elif system_state.is_critical_sensor_fault():
            condenser_on_decision = False
            current_debug_session_prints.append(f"[CTL_COND] Auto: Critical sensor fault. Condenser OFF.")
        elif supply_temp_f is None: 
            condenser_on_decision = False
            current_debug_session_prints.append("[CTL_COND] Auto: Supply temperature is None. Condenser OFF.")
        else: # Auto mode, pump on, no lockout, sensors okay
            setpoint = system_state.setpoint
            differential = system_state.differential
            turn_on_threshold = setpoint + differential
            turn_off_threshold = setpoint
            
            current_debug_session_prints.append(f"[CTL_COND] Auto: Supply={supply_temp_f:.1f}°F, Setpoint={setpoint:.1f}°F, Diff={differential:.1f}°F")
            current_debug_session_prints.append(f"[CTL_COND] Auto: OnThresh={turn_on_threshold:.1f}°F, OffThresh={turn_off_threshold:.1f}°F")

            if not is_condenser_currently_commanded_off: # Condenser was (or should be) ON
                if supply_temp_f <= turn_off_threshold:
                    condenser_on_decision = False
                    current_debug_session_prints.append(f"[CTL_COND] Auto: Running, temp ({supply_temp_f:.1f}) <= off_thresh. Turning OFF.")
                else:
                    condenser_on_decision = True 
                    current_debug_session_prints.append(f"[CTL_COND] Auto: Running, temp ({supply_temp_f:.1f}) > off_thresh. Staying ON.")
            else: # Condenser was (or should be) OFF
                if supply_temp_f >= turn_on_threshold:
                    time_since_last_off = now - system_state.condenser_last_off_time
                    if time_since_last_off >= CONDENSER_MIN_OFF_TIME_SEC:
                        condenser_on_decision = True
                        current_debug_session_prints.append(f"[CTL_COND] Auto: OFF, temp ({supply_temp_f:.1f}) >= on_thresh AND min_off_time met. Turning ON.")
                    else:
                        condenser_on_decision = False 
                        current_debug_session_prints.append(f"[CTL_COND] Auto: OFF, temp high, but min_off_time not met (waiting {CONDENSER_MIN_OFF_TIME_SEC - time_since_last_off:.0f}s). Staying OFF.")
                else:
                    condenser_on_decision = False 
                    current_debug_session_prints.append(f"[CTL_COND] Auto: OFF, temp ({supply_temp_f:.1f}) < on_thresh. Staying OFF.")
        
        # --- 5. Actuate Relays & Update State Cache ---
        if system_state.get_relay_state("pump") != pump_on_decision:
            _set_gpio_state(PUMP_PIN, pump_on_decision)
            system_state.set_relay_state("pump", pump_on_decision) 
            current_debug_session_prints.append(f"[CTL_RELAY] Pump relay commanded {'ON' if pump_on_decision else 'OFF'}.")
        
        if system_state.get_relay_state("condenser") != condenser_on_decision:
            _set_gpio_state(CONDENSER_PIN, condenser_on_decision)
            system_state.set_relay_state("condenser", condenser_on_decision) 
            current_debug_session_prints.append(f"[CTL_RELAY] Condenser relay commanded {'ON' if condenser_on_decision else 'OFF'}.")

        if DEBUG_LOGGING_ENABLED and current_debug_session_prints: # Only print header if there's content
            print(f"--- Controller Loop Iteration ({time.strftime('%H:%M:%S', time.localtime(now))}) ---")
            for msg in current_debug_session_prints:
                print(msg)
            print(f"Relay States (Commanded): Pump={system_state.get_relay_state('pump')}, Condenser={system_state.get_relay_state('condenser')}")
            print(f"Ambient Lockout Active: {is_ambient_lockout_active}")
            print(f"Condenser Last Off: {time.strftime('%H:%M:%S', time.localtime(system_state.condenser_last_off_time)) if system_state.condenser_last_off_time > 0 else 'N/A'}")
            print(f"Pump Post-Purge Ends: {time.strftime('%H:%M:%S', time.localtime(system_state.pump_post_purge_end_time)) if system_state.pump_post_purge_end_time > now else 'N/A'}")
            print(f"AHU Call Active (Flag): {system_state.is_ahu_calling}")
            print(f"Supply Temp: {supply_temp_f if supply_temp_f is not None else 'N/A'}, Critical Fault: {system_state.is_critical_sensor_fault()}")
            print("--- End Iteration ---")

        for _ in range(int(CONTROLLER_LOOP_INTERVAL_SEC)):
            if stop_event.is_set():
                break
            time.sleep(1)

    if DEBUG_LOGGING_ENABLED:
        print("[CONTROLLER_LOOP] Control loop stopped.")
    cleanup_gpio()