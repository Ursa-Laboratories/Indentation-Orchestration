"""Vention rail / controller / light driver.

Vendored from keeper_pc/device_drivers/vention_railway.py (Supawat
Pitaknarongphorn, BU KABLab, 2025-05-22) so polymer_indent doesn't need
the keeper_pc repo on PYTHONPATH. Edit here.

Wraps `machinelogic.Machine` for the Vention controller at the workcell's
rail IP. The arm worker uses `.actuator.move_absolute` (via this class's
`move_absolute`) and `.actuator.home` directly.
"""

from machinelogic import Machine


class VentionRailway:
    def __init__(self, ip="10.210.29.15"):
        self.machine = Machine(ip)
        self.controller = self.machine.get_machine_motion("controller")
        self.actuator = self.machine.get_actuator("actuator")
        self.light = self.machine.get_output("light")

    def move_absolute(self, position, timeout=5, speed=100):  # speed is in mm/s
        self.actuator.set_speed(speed=speed)
        self.actuator.move_absolute(position=position, timeout=timeout)
        self.actuator.wait_for_move_completion(timeout=timeout + 5)

    def move_relative(self, distance, timeout=3, speed=100):
        self.actuator.set_speed(speed=speed)
        self.actuator.move_relative(distance=distance, timeout=timeout)
        self.actuator.wait_for_move_completion(timeout=timeout + 5)

    def get_position(self):
        return self.actuator.state.position
