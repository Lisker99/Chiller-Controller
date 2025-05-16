import paho.mqtt.client as mqtt
import json

config = {
    "mqtt_broker": "192.168.68.68",  # your RPI5 IP here
    "mqtt_port": 1883,
    "mqtt_topic": "chiller/ahu/call"
}

client = mqtt.Client()
client.connect(config["mqtt_broker"], config["mqtt_port"], 60)
client.loop_start()

test_message = json.dumps({"test": "hello from chiller controller"})
client.publish(config["mqtt_topic"], test_message)

print(f"Published test message to topic {config['mqtt_topic']}")

client.loop_stop()
client.disconnect()
