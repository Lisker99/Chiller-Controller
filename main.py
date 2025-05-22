# main.py

import time
import threading
from config import DEBUG_LOGGING_ENABLED
from state import SystemState
from mqtt_client import MQTTClient
from sensors import sensor_loop
from controller import control_loop, cleanup_gpio # cleanup_gpio might be called if loop doesn't start

# Create a global stop event for all threads
stop_event = threading.Event()

def main():
    if DEBUG_LOGGING_ENABLED:
        print("[MAIN] Initializing Chiller Controller Application...")

    # --- 1. Initialize System State ---
    try:
        system_state = SystemState()
        if DEBUG_LOGGING_ENABLED:
            print("[MAIN] SystemState initialized.")
    except Exception as e:
        print(f"[MAIN_CRITICAL_ERROR] Failed to initialize SystemState: {e}")
        return # Cannot proceed

    # --- 2. Initialize MQTT Client ---
    # MQTTClient now takes state
    try:
        mqtt_client = MQTTClient(system_state)
        if DEBUG_LOGGING_ENABLED:
            print("[MAIN] MQTTClient initialized.")
    except Exception as e:
        print(f"[MAIN_CRITICAL_ERROR] Failed to initialize MQTTClient: {e}")
        return # Cannot proceed


    # --- 3. Create Threads for background tasks ---
    threads = []

    # Sensor Loop Thread
    try:
        sensor_thread = threading.Thread(
            target=sensor_loop,
            args=(system_state, mqtt_client, stop_event),
            name="SensorLoopThread"
        )
        threads.append(sensor_thread)
        if DEBUG_LOGGING_ENABLED:
            print("[MAIN] SensorLoopThread created.")
    except Exception as e:
        print(f"[MAIN_CRITICAL_ERROR] Failed to create SensorLoopThread: {e}")
        # Decide if you can proceed without sensors, or exit
        # For a chiller, sensors are critical, so likely exit or run in a very limited mode.
        # For now, we'll let it try to continue if other threads can start.

    # Control Loop Thread
    try:
        controller_thread = threading.Thread(
            target=control_loop,
            args=(system_state, stop_event),
            name="ControlLoopThread"
        )
        threads.append(controller_thread)
        if DEBUG_LOGGING_ENABLED:
            print("[MAIN] ControlLoopThread created.")
    except Exception as e:
        print(f"[MAIN_CRITICAL_ERROR] Failed to create ControlLoopThread: {e}")
        # Controller is critical, likely exit.
        # For now, continue to allow MQTT and sensors if they started.


    # --- 4. Start MQTT Connection (and its internal loops) ---
    # MQTTClient.connect() starts its own loop and the periodic publisher thread.
    # It's better to start this *before* the sensor and controller threads
    # so that HA discovery can happen early and states can be published.
    try:
        mqtt_client.connect() # This is a blocking call until connected or timeout, or runs async if connect_async
                              # Paho's connect() is blocking, then loop_start() makes it non-blocking.
        if DEBUG_LOGGING_ENABLED:
            print("[MAIN] MQTTClient connection process initiated.")
            # Wait a moment for connection to establish before starting other threads
            # This helps ensure MQTT is ready for initial publishes from other loops.
            time.sleep(2) # Adjust as needed, or implement a more robust "wait for connected"
            if not mqtt_client.connected:
                 print("[MAIN_WARN] MQTT client did not connect within the initial wait. Check broker.")

    except Exception as e:
        print(f"[MAIN_CRITICAL_ERROR] Error during MQTTClient connect initiation: {e}")
        # MQTT is critical for HA integration. Decide if to proceed.
        # For now, we'll let other threads start if they were created.

    # --- 5. Start Background Threads ---
    for thread in threads:
        try:
            if thread: # Ensure thread object was created
                thread.start()
                if DEBUG_LOGGING_ENABLED:
                    print(f"[MAIN] Started thread: {thread.name}")
        except Exception as e:
            print(f"[MAIN_ERROR] Failed to start thread {thread.name if thread else 'Unknown'}: {e}")


    # --- 6. Keep Main Thread Alive & Handle Shutdown ---
    if DEBUG_LOGGING_ENABLED:
        print("[MAIN] Application running. Press Ctrl+C to exit.")

    try:
        while not stop_event.is_set():
            # The main loop can also periodically check health of threads
            # For now, just sleep and let stop_event handle termination
            # Example: check if threads are alive
            # for t in threads:
            #     if not t.is_alive() and t in threading.enumerate(): # Check if it was started and died
            #         print(f"[MAIN_WARN] Thread {t.name} is no longer alive!")
            #         # Potentially try to restart it, or signal a critical error
            time.sleep(1)
    except KeyboardInterrupt:
        if DEBUG_LOGGING_ENABLED:
            print("\n[MAIN_SHUTDOWN] Ctrl+C received. Initiating shutdown sequence...")
    except Exception as e:
        print(f"[MAIN_ERROR] An unexpected error occurred in main loop: {e}")
    finally:
        if DEBUG_LOGGING_ENABLED:
            print("[MAIN_SHUTDOWN] Setting stop event for all threads...")
        stop_event.set()

        # Disconnect MQTT client first
        if 'mqtt_client' in locals() and mqtt_client:
            if DEBUG_LOGGING_ENABLED:
                print("[MAIN_SHUTDOWN] Disconnecting MQTT client...")
            mqtt_client.disconnect()

        # Wait for threads to finish
        if DEBUG_LOGGING_ENABLED:
            print("[MAIN_SHUTDOWN] Waiting for threads to join...")
        for thread in threads:
            if thread and thread.is_alive(): # Check if thread object exists and is alive
                if DEBUG_LOGGING_ENABLED:
                    print(f"[MAIN_SHUTDOWN] Joining thread: {thread.name}")
                thread.join(timeout=5) # Wait up to 5 seconds for each thread
                if thread.is_alive():
                    print(f"[MAIN_SHUTDOWN_WARN] Thread {thread.name} did not terminate gracefully.")
        
        # GPIO cleanup is now handled by the controller_loop's finally block when it exits.
        # If controller_thread might not have started or run, an explicit call might be needed here as a fallback.
        # However, if controller_loop started, its cleanup is preferred.
        # If controller_loop never started, _setup_gpio might not have run.
        # For safety, if controller_loop might not run, an explicit cleanup is good.
        # But if it *does* run, avoid double cleanup.
        # Given controller_loop now has its own finally: cleanup_gpio(), we can trust it.
        # If a critical error prevented controller_loop from starting, _gpio_initialized would be False.
        if DEBUG_LOGGING_ENABLED:
            if 'controller_thread' in locals() and controller_thread and not controller_thread.is_alive():
                print("[MAIN_SHUTDOWN] Controller thread has finished, GPIO cleanup should have occurred.")
            else:
                # This case is if controller_thread creation failed or it's stuck.
                # A direct cleanup attempt here might be a last resort.
                # However, controller.py's cleanup_gpio checks _gpio_initialized.
                print("[MAIN_SHUTDOWN] Ensuring GPIO cleanup as a fallback (if controller loop didn't run/finish its cleanup).")
                cleanup_gpio() # Call the cleanup from controller.py directly as a safeguard.

        if DEBUG_LOGGING_ENABLED:
            print("[MAIN_SHUTDOWN] Shutdown complete. Exiting.")

if __name__ == "__main__":
    main()