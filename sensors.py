from w1thermsensor import W1ThermSensor

def read_all_sensors(sensor_ids):
    temps = {}
    for label, sensor_id in sensor_ids.items():
        try:
            sensor = W1ThermSensor(sensor_id=sensor_id)
            temps[label] = round(sensor.get_temperature(), 2)
        except Exception as e:
            temps[label] = None
    return temps
