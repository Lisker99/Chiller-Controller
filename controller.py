# controller.py

import time
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("[CONTROLLER_WARN] RPi.GPIO library not found. GPIO operations will be simulated.")

from config import (
    DEBUG_LOGGING_ENABLED,
    PUMP_PIN,
    CONDENSER_PIN,
    RELAY_ACTIVE_HIGH,
    CONDENSER_MIN_OFF_TIME_SEC,
    CONTROLLER_LOOP_INTERVAL_SEC
)
# SystemState is passed as an argument
# from state import SystemState


# --- GPIO Setup and Control ---
# Store GPIO state to avoid re-initializing if loop restarts (though not typical here)
_gpio_initialized = False

def _setup_gpio():
    global _gpio_initialized
    if not GPIO_AVAILABLE or _gpio_initialized:
        return GPIO_AVAILABLE

    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False) # Suppress warnings about channels already in use if re-running

        # Determine initial state based on active_high
        # For active high, OFF is LOW. For active low, OFF is HIGH.
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
    """
    Sets the GPIO pin state respecting RELAY_ACTIVE_HIGH.
    :param pin: The GPIO pin number.
    :param desired_state_on: Boolean, True if the component should be ON, False for OFF.
    """
    if not GPIO_AVAILABLE or not _gpio_initialized:
        if DEBUG_LOGGING_ENABLED:
            # print(f"[CONTROLLER_GPIO_SIM] Simulating GPIO for Pin {pin}: {'ON' if desired_state_on else 'OFF'}")
            pass # Avoid spamming if GPIO is not there
        return

    try:
        if desired_state_on: # We want the component ON
            actual_gpio_signal = GPIO.HIGH if RELAY_ACTIVE_HIGH else GPIO.LOW
        else: # We want the component OFF
            actual_gpio_signal = GPIO.LOW if RELAY_ACTIVE_HIGH else GPIO.HIGH
        
        # Only change if current state is different (minor optimization, read can be slow)
        # current_signal = GPIO.input(pin) # Reading state can be complex if pin is not set to IN first
        # For simplicity, just set it. The relay itself won't flicker if state is same.
        GPIO.output(pin, actual_gpio_signal)

        # if DEBUG_LOGGING_ENABLED: # This can be very verbose
        #     print(f"[CONTROLLER_GPIO] Pin {pin} set to {'HIGH' if actual_gpio_signal == GPIO.HIGH else 'LOW'} "
        #           f"(Component {'ON' if desired_state_on else 'OFF'})")
    except Exception as e:
        print(f"[CONTROLLER_GPIO_ERROR] Failed to set GPIO pin {pin}: {e}")


def cleanup_gpio():
    global _gpio_initialized
    if GPIO_AVAILABLE and _gpio_initialized:
        if DEBUG_LOGGING_ENABLED:
            print("[CONTROLLER_GPIO] Cleaning up GPIO...")
        # Turn off relays before cleanup
        _set_gpio_state(PUMP_PIN, False)
        _set_gpio_state(CONDENSER_PIN, False)
        GPIO.cleanup()
        _gpio_initialized = False
        if DEBUG_LOGGING_ENABLED:
            print("[CONTROLLER_GPIO] GPIO cleanup complete.")
    elif not GPIO_AVAILABLE:
        if DEBUG_LOGGING_ENABLED:
            print("[CONTROLLER_GPIO] GPIO not available, no cleanup needed.")


# --- Control Logic ---

def control_loop(system_state, stop_event):
    """
    Main control loop for the chiller.
    :param system_state: The shared SystemState instance.
    :param stop_event: A threading.Event() to signal when to stop the loop.
    """
    if not _setup_gpio() and GPIO_AVAILABLE: # Attempt to setup GPIO, if it fails and GPIO should be avail, log error
        print("[CONTROLLER_ERROR] GPIO setup failed. Controller loop cannot safely operate relays.")
        # Optionally, could prevent the loop from running if GPIO is critical and failed.
        # For now, it will run and log simulation messages if GPIO_AVAILABLE is False.

    if DEBUG_LOGGING_ENABLED:
        print("[CONTROLLER_LOOP] Control loop started.")

    while not stop_event.is_set():
        now = time.time()
        current_debug_session_prints = [] # For grouping debug prints per loop iteration

        # --- 1. Update Timers and Fail-Safes in SystemState ---
        # (These are checked here but updated by their respective modules or SystemState itself)
        system_state.check_mqtt_failsafe() # Updates state.mqtt_comms_lost
        # state.check_for_call_timeout() is now called within the pump logic decision directly

        # --- 2. Get Current State and Overrides ---
        manual_pump_override = system_state.get_override("pump") # "on", "off", or None
        manual_condenser_override = system_state.get_override("chiller") # "on", "off", or None ("chiller" is UI term)
        
        # Get supply temperature for condenser logic (None if invalid)
        supply_temp_f = system_state.get_sensor_temp("supply")

        # --- 3. Pump Control Logic ---
        # SOO: Pump Turns ON if any AHU calls for cooling or manual cooling override is ON
        # SOO: Stays ON for 1 minute after AHU call ends
        # SOO: Turns OFF immediately if only manual override ends (covered by manual_pump_override check)

        # system_state.check_for_call_timeout() now returns True if pump should run due to call or post-purge
        demand_for_pump = system_state.check_for_call_timeout()

        pump_on_decision = False
        if manual_pump_override == "on":
            pump_on_decision = True
            # If pump manually turned ON, cancel any pending post-purge (it's now explicitly ON)
            system_state.pump_post_purge_end_time = 0 
            current_debug_session_prints.append("[CTL_PUMP] Manual override ON.")
        elif manual_pump_override == "off":
            pump_on_decision = False
            # If pump manually turned OFF, cancel any pending post-purge
            system_state.pump_post_purge_end_time = 0
            current_debug_session_prints.append("[CTL_PUMP] Manual override OFF.")
        else: # Auto mode for pump
            if demand_for_pump:
                pump_on_decision = True
                current_debug_session_prints.append(f"[CTL_PUMP] Auto: Demand active (call or post-purge). Pump ON.")
            else:
                pump_on_decision = False
                current_debug_session_prints.append("[CTL_PUMP] Auto: No demand. Pump OFF.")

        # --- 4. Condenser Control Logic ---
        # SOO: Can only run if Pump is ON
        # SOO: Turns ON if Supply Temp ≥ Setpoint + Differential
        # SOO: Turns OFF if Supply Temp ≤ Setpoint
        # SOO: Will not run if Supply Temp sensor fails (critical_sensor_fault)
        # SOO: Enforces a 2-minute minimum OFF time to prevent short cycling

        condenser_on_decision = False
        is_chiller_currently_running_hw = system_state.get_relay_state("condenser") # Last commanded state

        if not pump_on_decision:
            condenser_on_decision = False
            current_debug_session_prints.append("[CTL_COND] Pump OFF, so Condenser OFF.")
        elif manual_condenser_override == "on":
            condenser_on_decision = True
            current_debug_session_prints.append("[CTL_COND] Manual override ON (Pump is ON).")
        elif manual_condenser_override == "off":
            condenser_on_decision = False
            current_debug_session_prints.append("[CTL_COND] Manual override OFF.")
        else: # Auto mode for condenser
            if system_state.is_critical_sensor_fault():
                condenser_on_decision = False
                current_debug_session_prints.append(f"[CTL_COND] Auto: Critical sensor fault ({system_state.critical_sensor_fault}). Condenser OFF.")
            elif supply_temp_f is None: # Should be covered by critical_sensor_fault if supply is critical
                condenser_on_decision = False
                current_debug_session_prints.append("[CTL_COND] Auto: Supply temperature is None. Condenser OFF.")
            else:
                setpoint = system_state.setpoint
                differential = system_state.differential
                turn_on_threshold = setpoint + differential
                turn_off_threshold = setpoint
                
                current_debug_session_prints.append(f"[CTL_COND] Auto: Supply={supply_temp_f:.1f}°F, Setpoint={setpoint:.1f}°F, Diff={differential:.1f}°F")
                current_debug_session_prints.append(f"[CTL_COND] Auto: OnThreshold={turn_on_threshold:.1f}°F, OffThreshold={turn_off_threshold:.1f}°F")

                if is_chiller_currently_running_hw:
                    if supply_temp_f <= turn_off_threshold:
                        condenser_on_decision = False
                        current_debug_session_prints.append(f"[CTL_COND] Auto: Running, temp ({supply_temp_f:.1f}) <= off_thresh ({turn_off_threshold:.1f}). Turning OFF.")
                    else:
                        condenser_on_decision = True # Stay ON
                        current_debug_session_prints.append(f"[CTL_COND] Auto: Running, temp ({supply_temp_f:.1f}) > off_thresh. Staying ON.")
                else: # Chiller is currently OFF
                    if supply_temp_f >= turn_on_threshold:
                        # Check minimum off time
                        time_since_last_off = now - system_state.condenser_last_off_time
                        if time_since_last_off >= CONDENSER_MIN_OFF_TIME_SEC:
                            condenser_on_decision = True
                            current_debug_session_prints.append(f"[CTL_COND] Auto: OFF, temp ({supply_temp_f:.1f}) >= on_thresh ({turn_on_threshold:.1f}) AND min_off_time ({time_since_last_off:.0f}s >= {CONDENSER_MIN_OFF_TIME_SEC}s) met. Turning ON.")
                        else:
                            condenser_on_decision = False # Keep OFF, waiting for min_off_time
                            current_debug_session_prints.append(f"[CTL_COND] Auto: OFF, temp high, but min_off_time not met (waiting {CONDENSER_MIN_OFF_TIME_SEC - time_since_last_off:.0f}s). Staying OFF.")
                    else:
                        condenser_on_decision = False # Keep OFF
                        current_debug_session_prints.append(f"[CTL_COND] Auto: OFF, temp ({supply_temp_f:.1f}) < on_thresh. Staying OFF.")
        
        # --- 5. Actuate Relays & Update State Cache ---
        # Only change GPIO if the decision is different from the cached state
        # And update system_state.condenser_last_off_time if condenser turns off

        # Pump
        if system_state.get_relay_state("pump") != pump_on_decision:
            _set_gpio_state(PUMP_PIN, pump_on_decision)
            system_state.set_relay_state("pump", pump_on_decision) # Update cache
            current_debug_session_prints.append(f"[CTL_RELAY] Pump relay commanded {'ON' if pump_on_decision else 'OFF'}.")
        
        # Condenser
        if system_state.get_relay_state("condenser") != condenser_on_decision:
            _set_gpio_state(CONDENSER_PIN, condenser_on_decision)
            # set_relay_state in SystemState now automatically updates condenser_last_off_time
            system_state.set_relay_state("condenser", condenser_on_decision) # Update cache
            current_debug_session_prints.append(f"[CTL_RELAY] Condenser relay commanded {'ON' if condenser_on_decision else 'OFF'}.")
            # Note: system_state.condenser_last_off_time is updated within system_state.set_relay_state("condenser", False)

        if DEBUG_LOGGING_ENABLED and current_debug_session_prints:
            print(f"--- Controller Loop Iteration ({time.strftime('%H:%M:%S', time.localtime(now))}) ---")
            for msg in current_debug_session_prints:
                print(msg)
            print(f"Relay States: Pump={system_state.get_relay_state('pump')}, Condenser={system_state.get_relay_state('condenser')}")
            print(f"Condenser Last Off: {time.strftime('%H:%M:%S', time.localtime(system_state.condenser_last_off_time)) if system_state.condenser_last_off_time else 'N/A'}")
            print(f"Pump Post-Purge Ends: {time.strftime('%H:%M:%S', time.localtime(system_state.pump_post_purge_end_time)) if system_state.pump_post_purge_end_time > now else 'N/A'}")
            print(f"AHU Call Active (Flag): {system_state.is_ahu_calling}")
            print("--- End Iteration ---")


        # --- 6. Loop Delay ---
        # Check stop_event frequently during sleep to allow faster shutdown
        for _ in range(int(CONTROLLER_LOOP_INTERVAL_SEC)):
            if stop_event.is_set():
                break
            time.sleep(1)

    if DEBUG_LOGGING_ENABLED:
        print("[CONTROLLER_LOOP] Control loop stopped.")
    # Cleanup GPIO on exit from loop
    cleanup_gpio()