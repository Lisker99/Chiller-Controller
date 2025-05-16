import time
import json
from sensors import read_all_sensors
from control import ChillerControl
from mqtt_handler import CallTracker

with open("config.json") as f:
    config = json.load(f)

ctrl = ChillerControl(config["gpio_pump"], config["gpio_chiller"])
mqtt = CallTracker(
    config["mqtt_broker"],
    config["mqtt_port"],
    config["mqtt_topic"],
    config["call_timeout_sec"],
    debug=True
)

differential = config.get("differential_f", 5.0)  # default fallback

# Track last known good state (init from mqtt)
last_setpoint = mqtt.setpoint
last_manual_pump = mqtt.manual_pump
last_manual_chiller = mqtt.manual_chiller
last_ahu_call_active = False

try:
    while True:
        temps_c = read_all_sensors(config["sensor_ids"])

        # Convert temps to F
        temps_f = {label: round(c * 9/5 + 32, 2) if c is not None else None for label, c in temps_c.items()}

        print(f"Temps (F): {temps_f}")

        supply_temp = temps_f.get("Supply") or temps_f.get("supply")

        # Check MQTT connection status - you'll need to add a flag/property for this in CallTracker
        if mqtt.client.is_connected():
            # Update cached states only if connected
            last_setpoint = mqtt.setpoint
            last_manual_pump = mqtt.manual_pump
            last_manual_chiller = mqtt.manual_chiller
            ahu_call_active = mqtt.should_run()
            last_ahu_call_active = ahu_call_active
        else:
            # MQTT down: fallback to last known good states
            ahu_call_active = last_ahu_call_active
            # mqtt.setpoint and manual override variables unchanged, using last cached

        # Determine if cooling needed
        cooling_requested = False
        if ahu_call_active and supply_temp is not None and last_setpoint is not None:
            if supply_temp > last_setpoint + differential:
                cooling_requested = True

        # Pump control
        if mqtt.manual_pump is not None:
            ctrl.set_pump(mqtt.manual_pump)
        else:
            ctrl.set_pump(ahu_call_active or cooling_requested)

        # Chiller control
        if mqtt.manual_chiller is not None:
            ctrl.set_chiller(mqtt.manual_chiller)
        else:
            ctrl.set_chiller(cooling_requested and ctrl.pump_on)


        # Only publish status if MQTT connected
        if mqtt.client.is_connected():
            mqtt.client.publish("chiller/status/temps", json.dumps(temps_f))
            mqtt.client.publish("chiller/status/setpoint", str(last_setpoint) if last_setpoint is not None else "Unset")
            mqtt.client.publish("chiller/status/differential", str(differential))
            mqtt.client.publish("chiller/status/running", "Cooling Requested" if cooling_requested else "Cooling Off")
            mqtt.client.publish("chiller/status/ahu_call", "Active" if ahu_call_active else "Inactive")
            mqtt.client.publish("chiller/status/pump", "Pump On" if ctrl.pump_on else "Pump Off")
            mqtt.client.publish("chiller/status/chiller", "Chiller On" if ctrl.chiller_on else "Chiller Off")

            mqtt.client.publish("chiller/status/manual_override_pump", "Active" if last_manual_pump is not None else "Inactive")
            mqtt.client.publish("chiller/status/manual_override_chiller", "Active" if last_manual_chiller is not None else "Inactive")
            mqtt.client.publish("chiller/status/manual_override_ahu", "Active" if mqtt.manual_cooling is not None else "Inactive")

        time.sleep(5)

except KeyboardInterrupt:
    ctrl.shutdown()
