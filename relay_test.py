from gpiozero import LED
from time import sleep

# Adjust GPIO pins if needed
pump_relay = LED(17)      # GPIO 17
chiller_relay = LED(27)   # GPIO 27

print("Turning on relays...")
pump_relay.on()
sleep(5)
chiller_relay.on()
sleep(5)

print("Turning off relays...")
pump_relay.off()
chiller_relay.off()

print("Done.")
