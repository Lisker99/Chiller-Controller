from gpiozero import OutputDevice

class ChillerControl:
    def __init__(self, pump_pin, chiller_pin):
        self.pump = OutputDevice(pump_pin, active_high=True, initial_value=False)
        self.chiller = OutputDevice(chiller_pin, active_high=True, initial_value=False)

    def set_state(self, run):
        # Legacy: control both pump and chiller at once
        self.pump.value = run
        self.chiller.value = run

    def set_pump(self, state):
        self.pump.value = state

    def set_chiller(self, state):
        self.chiller.value = state

    def shutdown(self):
        self.pump.off()
        self.chiller.off()
    
    @property
    def pump_on(self):
        return self.pump.value == 1

    @property
    def chiller_on(self):
        return self.chiller.value == 1
