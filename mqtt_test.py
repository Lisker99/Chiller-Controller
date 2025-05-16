import paho.mqtt.client as mqtt

def on_message(client, userdata, msg):
    print(f"Received: {msg.topic} {msg.payload.decode()}")

client = mqtt.Client()
client.on_message = on_message

client.connect("192.168.68.104", 1883, 60)  # Replace with your desktop IP
client.subscribe("chiller/ahu/call")

client.loop_forever()
