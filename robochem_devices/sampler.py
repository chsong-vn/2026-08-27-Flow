"""
File: sampler.py
Author: Simone Pilon - Noël Research Group - 2023
GitHub: https://github.com/simone16

Description: Interface to the 3-axis machine control of the liquid handler.
"""

from typing import Any
import re
import copy
from bidict import bidict
from time import sleep
from serial import SerialException

from .device_arduino import (
    ArduinoDevice,
    ArduinoParameter,
    ArduinoValue,
    ArduinoValueRun,
    ArduinoValueOnOff,
)
from .device import (
    ParameterAccess,
    ParameterCachingPolicy,
    ParameterValue,
    DeviceSetupOption,
)
from .errors import (
    DeviceCommunicationError,
    ParameterCommError,
    ForbiddenMoveError,
    SoftLimitError,
    HardLimitError,
    AlarmLockError,
)
from .general import parse_with_units
from .syringe_pump import SwitchValveSetpointValue


class GrblPosition(ParameterValue, ArduinoValue):
    """
    Holds position information for the 3 axes machines controlled by Grbl.
    Each attribute represents a coordinate or None.
    When sending move commands to the machine, None can be used to keep the coordinate value unaltered (only the
    specified coordinates will be changed).

    Attributes:
        x: float | None  X axis position.
        y: float | None  Y axis position.
        z: float | None  Z axis position.
    """

    coordinate_names = ["X", "Y", "Z"]

    value: list[float | None, float | None, float | None]

    @property
    def x(self) -> float | None:
        """X coordinate or None for no change."""
        return self.value[0]

    @x.setter
    def x(self, value: float | None) -> None:
        """Set the value for the x coordinate."""
        self.value[0] = value

    @property
    def y(self) -> float | None:
        """Y coordinate or None for no change."""
        return self.value[1]

    @y.setter
    def y(self, value: float | None) -> None:
        """Set the value for the y coordinate."""
        self.value[1] = value

    @property
    def z(self) -> float | None:
        """Z coordinate or None for no change."""
        return self.value[2]

    @z.setter
    def z(self, value: float | None) -> None:
        """Set the value for the z coordinate."""
        self.value[2] = value

    def __init__(self, **kwargs):
        """
        Creates a position object with the coordinates specified.
        Some axes can be left out or None, to signify no movement along that direction.

        @keyword x: float
            X movement position.
        @keyword y: float
            Y movement position.
        @keyword z: float
            Z movement position.
        """
        ArduinoValue.__init__(self)
        ParameterValue.__init__(self)
        self.value = [None, None, None]

        for i in range(len(self.value)):
            name = self.coordinate_names[i].lower()
            if name in kwargs.keys():
                if isinstance(kwargs[name], (float, int)):
                    self.value[i] = round(float(kwargs[name]), 2)
                else:
                    raise TypeError(f"Invalid coordinate type '{type(kwargs[name])}'.")

    def to_serial_string(self) -> str:
        """
        Generate a string to transmit the value of this object over serial to the Arduino device.

        @return: str
            The string to be sent to serial (with necessary command / decorators).
        """
        command = " "
        for i in range(len(self.value)):
            if self.value[i] is not None:
                command += f"{self.coordinate_names[i]}{self.value[i]} "
        return command[:-1]

    def from_serial_string(self, message: str) -> None:
        """
        Parses a string representing the value of this object over serial from the Arduino device and stores it in
        this object.

        @param message: str
            The status string received via serial.
        """
        try:
            status = GrblStatus(message)
        except (ValueError, KeyError) as e:
            e.add_note("Error occurred while parsing position from status message.")
            raise
        self.value = copy.deepcopy(status.work_position.value)

    def coords(self) -> list[float | None, float | None, float | None]:
        """
        Return the coordinates as a list [x,y,z].

        @return: list[float | None, float | None, float | None]
            A list of coordinates.
        """
        return copy.deepcopy(self.value)

    def __str__(self) -> str:
        """
        Give a string description of the object.

        @return: str
            A string description.
        """
        command = ""
        for i in range(len(self.value)):
            if self.value[i] is not None:
                command += f"{self.coordinate_names[i]}={self.value[i]}, "
        command = "[" + command[:-2] + "]"
        return command

    def __eq__(self, other: "GrblPosition") -> bool:
        """
        Compares two GrblPosition objects.

        @return: bool
            Returns True if the two positions are equivalent.
        """
        for index in range(3):
            if self.value[index] is not None and other.value[index] is not None:
                if not self.value[index] == other.value[index]:
                    return False
        return True

    def __lt__(self, other: "GrblPosition") -> bool:
        """
        Compares two GrblPosition objects.

        @return: bool
            Returns True if all dimensions of self are lower than those of the other.
        """
        for index in range(3):
            if self.value[index] is not None and other.value[index] is not None:
                if self.value[index] >= other.value[index]:
                    return False
        return True

    def __gt__(self, other: "GrblPosition") -> bool:
        """
        Compares two GrblPosition objects.

        @return: bool
            Returns True if all dimensions of self are higher than those of the other.
        """
        for index in range(3):
            if self.value[index] is not None and other.value[index] is not None:
                if self.value[index] <= other.value[index]:
                    return False
        return True

    def __le__(self, other: "GrblPosition") -> bool:
        """
        Compares two GrblPosition objects.

        @return: bool
            Returns True if all dimensions of self are lower or equal to those of the other.
        """
        for index in range(3):
            if self.value[index] is not None and other.value[index] is not None:
                if self.value[index] > other.value[index]:
                    return False
        return True

    def __ge__(self, other: "GrblPosition") -> bool:
        """
        Compares two GrblPosition objects.

        @return: bool
            Returns True if all dimensions of self are higher or equal to those of the other.
        """
        for index in range(3):
            if self.value[index] is not None and other.value[index] is not None:
                if self.value[index] < other.value[index]:
                    return False
        return True


class GrblStatus(ParameterValue, ArduinoValue):
    """
    Holds all grbl status information.

    Attributes:
        state               String representation of machine state.
        machine_position    List of [x,y,z] position.
        work_position       List of [x,y,z] position. This is the position normally referred to by all commands,
                            unless explicitly specified.
    """

    state: str
    machine_position: GrblPosition
    work_position: GrblPosition

    status_pattern = re.compile(
        r"<(?P<status>[A-Za-z0-9]+),MPos:(?P<mpos>[+\-,.\dEe]+),WPos:(?P<wpos>[+\-,.\dEe]+)>"
    )
    position_pattern = re.compile(
        r"(?P<x>[+\-]?\d+\.?\d*),(?P<y>[+\-]?\d+\.?\d*),(?P<z>[+\-]?\d+\.?\d*)"
    )

    def __init__(self, status_string: str | None = None) -> None:
        """
        Constructs an object based on the string received by serial line.

        @param status_string: str
            The response of the grbl to the status request.
        """
        ArduinoValue.__init__(self)
        ParameterValue.__init__(self)
        self.value = status_string
        self.state = ""
        self.machine_position = GrblPosition()
        self.work_position = GrblPosition()
        if status_string is not None:
            self.from_serial_string(status_string)

    def from_serial_string(self, message: str) -> None:
        """
        Parses a string representing the value of this object over serial from the Arduino device and stores it in
        this object.

        @param message: str
            The status string received via serial.
        """
        status_match = GrblStatus.status_pattern.match(message)
        if status_match is None:
            raise ValueError(f"Invalid Status string received: '{message}'")
        self.state = status_match.group("status")
        position_match = GrblStatus.position_pattern.match(status_match.group("mpos"))
        if position_match is None:
            raise ValueError(f"Invalid Status machine position received: '{message}'")
        self.machine_position = GrblPosition()
        self.machine_position.x = float(position_match.group("x"))
        self.machine_position.y = float(position_match.group("y"))
        self.machine_position.z = float(position_match.group("z"))
        position_match = GrblStatus.position_pattern.match(status_match.group("wpos"))
        if position_match is None:
            raise ValueError(f"Invalid Status work position received: '{message}'")
        self.work_position = GrblPosition()
        self.work_position.x = float(position_match.group("x"))
        self.work_position.y = float(position_match.group("y"))
        self.work_position.z = float(position_match.group("z"))

    @classmethod
    def state_description(cls, name: str) -> str:
        """
        Long form description of the state.

        @param name: str
            The name of the state as read from GrblStatus.state.
        @return: str
            A long description of the state.
        """
        states = {
            "Alarm": "Homing enabled but homing cycle not run or error has been detected such as limit switch "
            "activated. Home or unlock to resume.",
            "Idle": "Waiting for any command.",
            "Jog": "Performing jog motion, no new commands until complete, except Jog commands.",
            "Homing": "Performing a homing cycle, won’t accept new commands until complete.",
            "Check": "Check mode is enabled; all commands accepted but will only be parsed, not executed.",
            "Cycle": "Running GCode commands, all commands accepted, will go to Idle when commands are complete.",
            "Hold": "Pause is in operation, resume to continue.",
            "Safety Door": "The safety door switch has been activated, similar to a Hold but will resume on closing "
            "the door. You probably don’t have a safety door on your machine!",
            "Sleep": "Sleep command has been received and executed, sometimes used at the end of a job. Reset or power "
            "cycle to continue.",
        }
        return states[name]

    def __str__(self):
        return f"<{self.state} at X{self.work_position.x}, Y{self.work_position.y}, Z{self.work_position.z}>"


class GrblAuxNeedle(ArduinoValueOnOff):
    """Value for Grbl control describing the position of the auxiliary needle."""

    _raw_values = bidict({"OFF": 9, "ON": 8})


class InjectionPort:
    """
    Injection port location on a sampler.
    """

    move: GrblPosition
    plunge: GrblPosition

    def __init__(self, position: GrblPosition):
        self.move = GrblPosition(x=position.x, y=position.y)
        self.plunge = GrblPosition(z=position.z)
        self.injection_valve_position = None


class VialHolder:
    """
    Vials holder location on a sampler.
    """

    move: GrblPosition

    def __init__(self, position: GrblPosition, shape: str):
        self.move = GrblPosition(x=position.x, y=position.y)
        self.position = position
        self.shape = shape


class Sampler(ArduinoDevice):
    """
    Handles communication with a CNC-based sampler.
    The device is documented in detail at https://github.com/Noel-Research-Group/liquid_handler_cnc .

    Usage:
        First, connect to the device with
        `self.open('COM4')`

        Then, change parameters of the device with
        `self['parameter_name'] = xyz`

        or read parameters with
        `xyz = self['parameter_name']`

        For a detailed list of available parameters, call
        `self.parameters()`

        For a human-readable list of parameters, call
        `print(self.usage())`

        Finally, close the device with
        `self.close()`

    Notes on Grbl settings:
        The user must take care that grbl is properly set up for the machine in use.
        In addition, homing cycle ($22) must be enabled. Therefore, the machine needs physical endstops.
        It is advised to also enable softlimits ($20) and hard limits ($21) and set accurate values for the
        maximum travel setting of each axis.

    This code has been written and tested with grbl v0.9j.
    Reference: https://honingmill.fandom.com/wiki/Configuring_Grbl_v0.9
    """

    locations: dict[str, [InjectionPort | VialHolder]]

    _welcome_message_pattern = re.compile(r"^Grbl (?P<version>\d+\.\d+[a-zA-Z])")
    _supported_grbl_versions = ["0.9j"]

    def __init__(self):
        ArduinoDevice.__init__(self)

        self.generic_name = "Liquid Handler Sampler"
        self._position_min = None
        self._position_max = None
        self._timeout_multiplier = (
            1.20  # Wait this many times the expected travel time before timing-out
        )
        self._needle_length = 51.0  # Needle length in mm.
        self._safe_z = 0.0  # Minimum z position for horizontal movement.
        self._ignore_safe_z = False  # Forbid xy movements below safe z.
        self._safe_z_margin = 2.0  # How far to keep from safe z when moving.
        self._vials_z = 0.0  # Z position of the top of the vials.
        self.locations = {}  # stored positions for the handler.
        self._injection_port = None
        self._sample_holders = {}
        self._sampling_pump_name = None
        self._moved_since_zero = True  # if false, the machine was just zeroed.

        # This is Arduino, but has no custom ID.
        self._clear_parameters()  # remove 'ID' parameter.

        self._parameters_motions = []

        parameter = ArduinoParameter(
            name="status",
            access_level=ParameterAccess.R,
            value_type=GrblStatus,
            internal_id="?",
        )
        parameter.description = "Current machine status"
        parameter.read_acknowledge = True
        parameter.acknowledge_value = "ok"
        parameter.acknowledge_timeout = 0.5
        self.add_parameter(parameter)
        self._parameter_status = parameter

        parameter = ArduinoParameter(
            name="position",
            access_level=ParameterAccess.R,
            value_type=GrblPosition,
            internal_id="?",
        )
        parameter.description = "Current machine position"
        parameter.read_acknowledge = True
        parameter.acknowledge_value = "ok"
        parameter.acknowledge_timeout = 0.5
        self.add_parameter(parameter, ignore_shared_id=True)

        parameter = ArduinoParameter(
            name="home",
            access_level=ParameterAccess.W,
            value_type=ArduinoValueRun,
            internal_id="$H",
        )
        parameter.description = "Run homing cycle"
        parameter.write_acknowledge = True
        parameter.acknowledge_value = "ok"
        parameter.acknowledge_timeout = 120.0
        self._parameter_home = parameter
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="unlock",
            access_level=ParameterAccess.W,
            value_type=ArduinoValueRun,
            internal_id="$X",
        )
        parameter.description = "Unlock grbl without zeroing"
        parameter.write_acknowledge = True
        parameter.acknowledge_value = ("[Caution: Unlocked]", "ok")
        parameter.acknowledge_timeout = 5.0
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="offset",
            access_level=ParameterAccess.W,
            value_type=GrblPosition,
            internal_id="G92",
        )
        parameter.description = "Set the working coordinates to the given value"
        parameter.write_acknowledge = True
        parameter.acknowledge_value = "ok"
        parameter.acknowledge_timeout = 0.5
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="feed",
            access_level=ParameterAccess.RW,
            value_type=float,
            internal_id="F",
        )
        parameter.description = "Set feed-rate"
        parameter.units = " mm/min"
        parameter.max_value = 2000.0  # Will be overridden with machine setting
        parameter.min_value = 0.0
        parameter.float_rounding = 1
        parameter.caching_policy = ParameterCachingPolicy.ALWAYS
        parameter.write_acknowledge = True
        parameter.acknowledge_value = "ok"
        parameter.acknowledge_timeout = 0.5
        self._parameter_feed = parameter
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="move",
            access_level=ParameterAccess.W,
            value_type=GrblPosition,
            internal_id="G1",
        )
        parameter.description = "Move to an absolute work position at the feed rate"
        parameter.write_acknowledge = True
        parameter.acknowledge_value = "ok"
        parameter.acknowledge_timeout = 10.0
        self.add_parameter(parameter)
        self._parameter_move = parameter
        self._parameters_motions.append(self._parameter_move)

        parameter = ArduinoParameter(
            name="fast_move",
            access_level=ParameterAccess.W,
            value_type=GrblPosition,
            internal_id="G0",
        )
        parameter.description = "Move to an absolute work position as fast as possible"
        parameter.write_acknowledge = True
        parameter.acknowledge_value = "ok"
        parameter.acknowledge_timeout = 10.0
        self.add_parameter(parameter)
        self._parameter_move = parameter
        self._parameters_motions.append(self._parameter_move)

        parameter = ArduinoParameter(
            name="aux_needle",
            access_level=ParameterAccess.W,
            value_type=GrblAuxNeedle,
            internal_id="M",
        )
        parameter.description = "Insert or retract the auxiliary needle"
        parameter.write_acknowledge = True
        parameter.acknowledge_value = "ok"
        parameter.acknowledge_timeout = 10.0
        self.add_parameter(parameter)
        self._parameter_aux_needle = parameter

        parameter = ArduinoParameter(
            name="units_mm",
            access_level=ParameterAccess.W,
            value_type=ArduinoValueRun,
            internal_id="G21",
        )
        parameter.description = "Set units to mm"
        parameter.write_acknowledge = True
        parameter.acknowledge_value = "ok"
        parameter.acknowledge_timeout = 0.5
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="absolute",
            access_level=ParameterAccess.W,
            value_type=ArduinoValueRun,
            internal_id="G90",
        )
        parameter.description = "All coordinates are absolute values from the origin"
        parameter.write_acknowledge = True
        parameter.acknowledge_value = "ok"
        parameter.acknowledge_timeout = 0.5
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="pause",
            access_level=ParameterAccess.W,
            value_type=float,
            internal_id="G4 P",
        )
        parameter.description = "Pause execution for this many seconds"
        parameter.max_value = 1000.0
        parameter.min_value = 0.01
        parameter.write_acknowledge = True
        parameter.acknowledge_value = "ok"
        parameter.acknowledge_timeout = 10.0
        self.add_parameter(parameter)
        self._parameter_pause = parameter

        # todo this stops also acknowledgments to be sent back, at the moment this is not supported and will give errors
        # unless the command is followed by a soft-reset (cancelling queued commands).
        parameter = ArduinoParameter(
            name="hold",
            access_level=ParameterAccess.W,
            value_type=ArduinoValueRun,
            internal_id="!",
        )
        parameter.description = (
            "Stops machine motion instantly. Queued motions can be resumed"
        )
        self.add_parameter(parameter)

        # todo this will trigger an 'ok' message for each command sent while on hold.
        # this is not supported at the moment.
        parameter = ArduinoParameter(
            name="resume",
            access_level=ParameterAccess.W,
            value_type=ArduinoValueRun,
            internal_id="~",
        )
        parameter.description = "Resume machine operations after 'hold' state"
        parameter.write_acknowledge = True
        parameter.acknowledge_value = "ok"
        parameter.acknowledge_timeout = 10.0
        self.add_parameter(parameter)

        # Note: this parameter generates responses over multiple lines. These are not handled by the parameter.
        parameter = ArduinoParameter(
            name="reset",
            access_level=ParameterAccess.W,
            value_type=ArduinoValueRun,
            internal_id=chr(24),
        )
        parameter.description = "Soft reset, machine position is preserved"
        self.add_parameter(parameter)

        # Parameters read all at once with '$$'. Filled in on open() by self.read_grbl_settings()
        self._grbl_settings = {
            0: ("step_pulse", " usec"),
            1: ("step_idle_delay", " msec"),
            2: ("step_port_invert_mask", ""),
            3: ("dir_port_invert_mask", ""),
            4: ("step_enable_invert", ""),
            5: ("limit_pins_invert", ""),
            6: ("probe_pin_invert", ""),
            10: ("status_report_mask", ""),
            11: ("junction_deviation", " mm"),
            12: ("arc_tolerance", " mm"),
            13: ("report_inches", ""),
            20: ("soft_limits", ""),
            21: ("hard_limits", ""),
            22: ("homing_cycle", ""),
            23: ("homing_dir_invert_mask", ""),
            24: ("homing_feed", " mm/min"),
            25: ("homing_seek", " mm/min"),
            26: ("homing_debounce", " msec"),
            27: ("homing_pull-off", " mm"),
            100: ("x_move", " step/mm"),
            101: ("y_move", " step/mm"),
            102: ("z_move", " step/mm"),
            110: ("x_max_rate", " mm/min"),
            111: ("y_max_rate", " mm/min"),
            112: ("z_max_rate", " mm/min"),
            120: ("x_accel", " mm/sec^2"),
            121: ("y_accel", " mm/sec^2"),
            122: ("z_accel", " mm/sec^2"),
            130: ("x_max_travel", " mm"),
            131: ("y_max_travel", " mm"),
            132: ("z_max_travel", " mm"),
        }

        for setting_number, setting_data in self._grbl_settings.items():
            parameter_value_type = float
            if setting_data[1] == "":
                parameter_value_type = int
                if "mask" not in setting_data[0]:
                    parameter_value_type = ArduinoValueOnOff

            parameter = ArduinoParameter(
                name=setting_data[0],
                access_level=ParameterAccess.RW,
                value_type=parameter_value_type,
                internal_id=f"${setting_number}=",
            )
            parameter.caching_policy = ParameterCachingPolicy.ALWAYS
            parameter.units = setting_data[1]
            parameter.write_acknowledge = True
            parameter.acknowledge_value = "ok"
            self.add_parameter(parameter)

        self._parameters_max_rate = [
            self.parameter_by_name("x_max_rate"),
            self.parameter_by_name("y_max_rate"),
            self.parameter_by_name("z_max_rate"),
        ]
        self._parameters_accel = [
            self.parameter_by_name("x_accel"),
            self.parameter_by_name("y_accel"),
            self.parameter_by_name("z_accel"),
        ]
        self._parameters_travel = [
            self.parameter_by_name("x_max_travel"),
            self.parameter_by_name("y_max_travel"),
            self.parameter_by_name("z_max_travel"),
        ]

        safe_z_option = DeviceSetupOption(name="safe_z", handler=self.setup_safe_z)
        safe_z_option.value_type = str
        safe_z_option.required = True
        safe_z_option.description = (
            "Z position below which the needle can hit vials or other objects."
            "Specify as a string including units (metric)."
        )
        self.add_setup_option(safe_z_option)

        vial_top_z_option = DeviceSetupOption(
            name="vial_top_z", handler=self.setup_vial_top_z
        )
        vial_top_z_option.value_type = str
        vial_top_z_option.required = True
        vial_top_z_option.description = (
            "Z position corresponding to the top of the vials."
            "Specify as a string including units (metric)."
        )
        self.add_setup_option(vial_top_z_option)

        needle_length_option = DeviceSetupOption(
            name="needle_length", handler=self.setup_needle_length
        )
        needle_length_option.value_type = str
        needle_length_option.required = True
        needle_length_option.description = (
            "Length of the needle mounted on the sampler."
            "Specify as a string including units (metric)."
        )
        self.add_setup_option(needle_length_option)

        ignore_safez_option = DeviceSetupOption(
            name="ignore_safe_z", handler=self.setup_ignore_safez
        )
        ignore_safez_option.value_type = bool
        ignore_safez_option.description = (
            "Set to True to allow horizontal movements below the safe height."
        )
        self.add_setup_option(ignore_safez_option)

        locations_option = DeviceSetupOption(
            name="locations", handler=self.setup_locations
        )
        locations_option.value_type = dict
        locations_option.description = """
            Locations specify special positions for the handler, such as vial holders and injection ports.
            This option must be specified in the config file as a dict of location_name -> data pairs.
            Data is a dictionary itself, which must specify:
             - type: "type"
                At the moment, "injection_port" and "holder" are supported.
             - position: {"x": "10.0mm", "y": "10.0mm" (, "z": "10.0mm")}
                Use SI units: mm, cm ecc...
                z position is only used for injection port.
             - shape: "shape"
                This is only for holders. It must match a holder type from the 'sample_holder_types' config file.
            Example:
            "setup": {
              "locations": {
                "injection_flow": {
                  "type": "injection_port",
                  "position": {
                    "x": "170.25mm",
                    "y": "278.5mm",
                    "z": "-60mm"
                  }
                },
                "holder_A": {
                  "type": "holder",
                  "position": {
                    "x": "132.5mm",
                    "y": "38.25mm"
                  },
                  "shape": "vial_GC_4ml_4x4"
                },
            """
        self.add_setup_option(locations_option)

        sampling_pump_option = DeviceSetupOption(
            "sampling_pump", self.setup_sampling_pump
        )
        sampling_pump_option.value_type = str
        sampling_pump_option.description = (
            "Identifier name of the syringe pump connected to this sampler."
        )
        self.add_setup_option(sampling_pump_option)

    def read_grbl_settings(self) -> None:
        """
        Issues a special command to read all grbl settings (accessed via the $$ command) at once.
        Unfortunately there is no way to read these one by one, so we have to initialize them all in one operation.
        Also sets max and min values for several parameters.
        """
        self._log_parameter_start("Reading grbl settings.")
        with self._serial_thread_lock:
            try:
                self.parse_error(self.flush_buffer_in(), parameter=None)
                command = "$$\n"
                self._serial_iface.write(command.encode("ascii"))
                response = []
                ack = False
                while not ack:
                    line = self._serial_iface.readline().decode("ascii").rstrip("\r\n")
                    if line == "ok":
                        ack = True
                    else:
                        response.append(line)
                line_pattern = re.compile(r"^\$(?P<number>\d+)=(?P<value>\d+.?\d*)")
                for line in response:
                    pattern_match = line_pattern.search(line)
                    if pattern_match is not None:
                        setting_number = int(pattern_match.group("number"))
                        setting_value = pattern_match.group("value")
                        parameter_name = self._grbl_settings[setting_number][0]
                        # noinspection PyTypeChecker
                        parameter: ArduinoParameter = self.parameter_by_name(
                            parameter_name
                        )
                        parameter.last_known_value = self._extract(
                            parameter=parameter, response=setting_value
                        )
                        self.log(
                            f"Read '{parameter_name}' <- {setting_value}{parameter.units}",
                            level="ok",
                        )
            except (SerialException, ValueError):
                error = DeviceCommunicationError(self, f"Failed to read grbl settings.")
                self.log_exception(error)
                raise error
        self._parameter_feed.max_value = min(
            self._parameters_max_rate[0].last_known_value,
            self._parameters_max_rate[1].last_known_value,
        )
        homing_pulloff = self.parameter_by_name("homing_pull-off").last_known_value
        self._position_min = GrblPosition()
        self._position_min.x = 0.0
        self._position_min.y = 0.0
        self._position_min.z = -(
            self._parameters_travel[2].last_known_value - homing_pulloff
        )
        self._position_max = GrblPosition()
        self._position_max.x = (
            self._parameters_travel[0].last_known_value - homing_pulloff
        )
        self._position_max.y = (
            self._parameters_travel[1].last_known_value - homing_pulloff
        )
        self._position_max.z = 0.0
        for parameter in self._parameters_motions:
            parameter.min_value = self._position_min
            parameter.max_value = self._position_max
        self._log_parameter_success("Read grbl settings.")

    @property
    def position_min(self) -> GrblPosition:
        """
        Minimum Work position on every axis.

        @return: GrblPosition
            The minimum value allowed for every axis.
        """
        return copy.deepcopy(self._position_min)

    @property
    def position_max(self) -> GrblPosition:
        """
        Maximum Work position on every axis.

        @return: GrblPosition
            The maximum value allowed for every axis.
        """
        # FIX (2026-07, 찬호): 원본은 self._position_min을 리턴하는 copy-paste 버그.
        return copy.deepcopy(self._position_max)

    @property
    def default_feedrate_xy(self) -> float:
        """
        Default feedrate for x,y movements.

        @return: float
            Default feedrate in mm/min.
        """
        max_rate = min(
            self._parameters_max_rate[0].last_known_value,
            self._parameters_max_rate[1].last_known_value,
        )
        return max_rate * 0.8

    @property
    def default_feedrate_z(self) -> float:
        """
        Default feedrate for z movements.

        @return: float
            Default feedrate in mm/min.
        """
        return self._parameters_max_rate[2].last_known_value * 0.8

    @property
    def moved_since_zero(self) -> bool:
        """
        Has the machine performed any motion since the last time it was homed / zeroed?
        If so, it can help to zero it again.

        @return: bool
            True if the machine performed any motions after it was initially zeroed.
        """
        return self._moved_since_zero

    def open(self, comport: str) -> None:
        """
        Connects to the device. The connection must open before data can be sent or received.

        @param comport: str
            Identifier of the serial port to which the device is connected.
        """
        ArduinoDevice.open(self, comport=comport)

        welcome_msg = self.flush_buffer_in().replace("\n", "").replace("\r", "")
        if not welcome_msg == "":
            match = self._welcome_message_pattern.search(welcome_msg)
            if match:
                self.log(welcome_msg, level="ok")
                if not match.group("version") in self._supported_grbl_versions:
                    self.log(
                        f"This software was tested on grbl versions: {self._supported_grbl_versions},"
                        f" but an untested version was detected: '{match.group('version')}'.",
                        level="warning",
                    )
            else:
                self.log(
                    f"Unexpected welcome message: '{welcome_msg}'", level="warning"
                )

    def return_to_home(self) -> None:
        """
        Re-home the machine. This is quicker than the regular 'home' command, but it requires the machine
        to have already been homed before.
        """
        if self._parameter_aux_needle.last_known_value == "ON":
            self.__setitem__("aux_needle", "OFF")
        self.__setitem__("feed", self.default_feedrate_z)
        self.__setitem__("move", GrblPosition(z=0.0))
        self.__setitem__("feed", self.default_feedrate_xy)
        self.__setitem__("move", GrblPosition(x=0.0, y=0.0))
        self.__setitem__("home", "RUN")

    def emergency_close(self) -> None:
        """
        Disconnect the device in an emergency situation.
        Stops any operation asynchronously.
        """
        self.__setitem__("reset", "RUN")
        # read in all the messages generated by this reset.
        timeout = 2.0
        # depending on what the machine was doing, it could be good to go (ok) or need to be unlocked.
        needs_unlock = False
        while timeout > 0:
            sleep(self._update_delay)
            timeout -= self._update_delay
            message = self.flush_buffer_in(whole_line=True)
            if message == "":
                continue
            self.log(f"Received '{message}' after reset.")
            if message == "ok":
                break
            if message == "['$H'|'$X' to unlock]":
                needs_unlock = True
                break
            # note, the welcome message and a blank line are also sent , as well as an alarm message if the machine
            # needs to be unlocked, but we can ignore these. We only look at the last line that is sent in either case.
        if needs_unlock:
            self.__setitem__("unlock", "RUN")
        # note: this will run the homing cycle.
        self.close()

    def prepare_for_close(self) -> None:
        """
        Ensure the device is in a safe state before closing the connection (and losing control).
        """
        self.return_to_home()

    def initialize(self) -> None:
        """
        Prepare the device for operation.
        Time intensive operations (movement, zeroing, syncing) are performed here.
        """
        ArduinoDevice.initialize(self)

        self.read_grbl_settings()
        self.__setitem__("unlock", "RUN")
        self.__setitem__("aux_needle", "OFF")
        if self.parameter_by_name("homing_cycle").last_known_value == 0:
            self.log(
                "Homing cycle is disabled: the sampler will not know its initial position!",
                level="warning",
            )
        else:
            self.__setitem__("home", "RUN")
            self.__setitem__("offset", GrblPosition(x=0.0, y=0.0, z=0.0))
        self.__setitem__("units_mm", "RUN")
        self.__setitem__("absolute", "RUN")
        self.__setitem__("feed", 1000.0)

    def setup_safe_z(self, z: str) -> None:
        """
        Setup handler for 'safe_z'.
        Sets the safe needle position for travel moves.

        @param z: str
            Z position above which it is always safe to move the needle without hitting vials with SI units
            (mm, cm, ecc...).
        """
        try:
            z, units = parse_with_units(z, "mm")
            self._safe_z = z
        except ValueError as e:
            e.add_note(self.exception_note())
            raise

    def setup_vial_top_z(self, z: str) -> None:
        """
        Setup handler for 'vial_top_z'.
        Sets the z position of the top of the vials. Vial max depth is indicated from this point.

        @param z: str
            Z position of the top of the cap of the vials with SI units (mm, cm, ecc...).
        """
        try:
            z, units = parse_with_units(z, "mm")
        except ValueError as e:
            e.add_note(self.exception_note())
            raise
        if z < self._safe_z:
            self._vials_z = z
            for data in self._sample_holders.values():
                data["position"].z = z
        else:
            error = ValueError(
                "Vials top Z position is higher than safe travel height."
            )
            error.add_note(self.exception_note())
            raise error

    def setup_needle_length(self, length: str) -> None:
        """
        Setup handler for 'needle_length'.
        Sets the needle length internally.

        @param length: str
            Length of the injection needle with SI units (mm, cm, ecc...).
        """
        try:
            length, units = parse_with_units(length, "mm")
            self._needle_length = length
        except ValueError as e:
            e.add_note(self.exception_note())
            raise

    def setup_ignore_safez(self, value: bool) -> None:
        """
        Setup handler for 'ignore_safe_z'.
        Allows movement of the needle below the safe height.

        @param value: bool
            The value of the setup option. Set to True to allow all movements.
        """
        self._ignore_safe_z = value
        if value:
            self.log("Ignoring safe height for xy movements.", level="warning")

    def setup_locations(self, locations: dict) -> None:
        """
        Setup handler for 'locations'.
        Enables injection of samples into a flow system by specifying the injection port location.

        @param locations: dict
            Dict of location_name -> data pairs.
            data is a dictionary itself, which must specify:
             - type: "type"
                At the moment, "injection_port" and "holder" are supported.
             - position: {"x": "10.0mm", "y": "10.0mm" (, "z": "10.0mm")}
                Use SI units: mm, cm ecc...
                z position is only used for injection port, it indicates the depth to reach for injection.
             - shape: "shape"
                This is only for holders. It must match a holder type from the 'sample_holder_types' config file.
             - injection_valve_position: "OFF"
                If specified, this is the position that the auxiliary switchvalve of the pump connected to the sampler
                will be set to when injecting in the injection port.
                This is only used by the injection port locations.
                By default, the switchvalve is not moved.
        """
        try:
            for location_name, location_data in locations.items():
                position = GrblPosition(
                    **{
                        name.lower(): parse_with_units(value, "mm")[0]
                        for name, value in location_data["position"].items()
                    }
                )
                location = None
                if location_data["type"] == "injection_port":
                    position.z += self._vials_z
                    location = InjectionPort(position)
                    valve_position = location_data.get("injection_valve_position", None)
                    if valve_position is not None:
                        location.injection_valve_position = SwitchValveSetpointValue(
                            valve_position
                        )
                elif location_data["type"] == "holder":
                    position.z = self._vials_z
                    location = VialHolder(position, location_data["shape"])
                self.locations[location_name] = location
        except (KeyError, ValueError) as e:
            e.add_note(
                "Make sure the configuration files correctly specify the locations."
            )
            e.add_note(self.exception_note())
            self.log(e)
            raise

    @property
    def sampling_pump_name(self) -> str | None:
        """
        Identifier for the syringe pump connected to the sampler.
        This pump is used to pump to and from vials and to set the position of the injection port switchvalve.

        @return: str | None
            The identifier of the syringe pump connected to the sampler.
            Returns None if no pump was configured.
        """
        return self._sampling_pump_name

    def setup_sampling_pump(self, name: str) -> None:
        """
        Setup handler for 'sampling_pump'.
        The name of the pump connected to the sampler, which is used to pump to and from vials.

        @param name: str
            Identifier as found in the config file.
        """
        self._sampling_pump_name = name

    @property
    def needle_length(self) -> float:
        """
        Returns the length of the needle mounted on the device in mm.
        The value is taken from the setup arguments in the device setup.

        @return: float
            Length of the needle in mm.
        """
        return self._needle_length

    # noinspection PyUnresolvedReferences
    @property
    def travel_position(self) -> GrblPosition:
        """
        This position can be used to ensure the needle is in a safe position before initiating a horizontal move.

        @return: GrblPosition
            The position to move to, to ensure the needle is out of the way.
        """
        position = GrblPosition(z=self._safe_z)
        position.z += self._safe_z_margin
        if position.z >= self._parameter_move.max_value.z:
            position.z = self._parameter_move.max_value.z
        return position

    @property
    def vials_top_z(self) -> float:
        """
        This is the position of the top of the vial caps. The max vial depth is calculated from this height.
        Note: Z is mostly negative and increases with height.

        @return: float
            The Z position of the top of the vials in this device.
        """
        return self._vials_z

    def parse_error(self, message: str, parameter: ArduinoParameter) -> None:
        """
        Handles unexpected messages from the device.

        @param message: str
            The unexpected message received.
        @param parameter: ArduinoParameter
            The parameter which was being handled when the message was received.
        @return: DeviceError | None
            If necessary, an error is returned which should be raised.
        """
        message = message.replace("\n", "").replace("\r", "")
        if message == "":
            return None
        if message == "ALARM: Soft limit":
            error = SoftLimitError(
                "Received soft limit alarm: attempt to move beyond the machine limits.",
                device=self,
                parameter=parameter,
            )
            self.log(error)
            raise error
        if message == "ALARM: Hard limit":
            error = HardLimitError(
                "Received hard limit alarm: endstop was unexpectedly triggered.",
                device=self,
                parameter=parameter,
            )
            self.log(error)
            raise error
        if message == "error: Alarm lock":
            error = AlarmLockError(
                "The device is locked due to an alarm (or was not zeroed)"
                " and cannot perform the requested operation.",
                device=self,
                parameter=parameter,
            )
            self.log(error)
            raise error
        message = f"Received unexpected data: '{message}'."
        if self.ignore_unexpected_data_warning:
            self.log(message, level="warning")
        else:
            error = ParameterCommError(message, device=self, parameter=parameter)
            self.log_exception(error)
            raise error
        return None

    def _command_write(self, parameter: ArduinoParameter, value: Any) -> str:
        """
        Computes the string which needs to be sent to write a value to a parameter.

        @param parameter: ArduinoParameter
            The parameter which needs to be written.
        @param value: Any
            The value of the parameter.
        @return: str
            The command string to send via serial.
        """
        if parameter.to_serial_string is not None:
            return parameter.internal_id + parameter.to_serial_string(value) + "\n"
        elif isinstance(value, ArduinoValue):
            return parameter.internal_id + value.to_serial_string() + "\n"
        else:
            return parameter.internal_id + str(value) + "\n"

    def _command_read(self, parameter: ArduinoParameter) -> str:
        """
        Computes the string which needs to be sent to read the value of a parameter.

        @param parameter: ArduinoParameter
            The parameter which needs to be read.
        @return: str
            The command string to send via serial.
        """
        return parameter.internal_id + "\n"

    def _estimated_time_to(
        self, start: GrblPosition, end: GrblPosition, quick_move: bool
    ) -> float:
        """
        Calculates how long it should take to complete a 'move' command with the given position.

        @param start: GrblPosition
            The starting point of the move command.
        @param end: GrblPosition
            The destination of the move command.
        @param quick_move: bool
            If true, the maximum rates are used instead of current feed (true for G0 commands).
        @return: float
            Estimated time for the move in S.
        """
        feed = self._parameter_feed.last_known_value

        start = start.coords()
        end = end.coords()

        travel = [0.0, 0.0, 0.0]
        distance = 0.0
        for index in range(3):
            if end[index] is not None:
                partial = abs(end[index] - start[index])
                travel[index] = partial
                distance += partial**2
        distance = distance**0.5

        speed = [0.0, 0.0, 0.0]
        for index in range(3):
            if not travel[index] == 0.0:
                if quick_move:
                    speed[index] = self._parameters_max_rate[index].last_known_value
                else:
                    speed[index] = feed / distance * travel[index]
                    if speed[index] > self._parameters_max_rate[index].last_known_value:
                        speed[index] = self._parameters_max_rate[index].last_known_value
                speed[index] /= 60  # in mm/S
        times = [0.0, 0.0, 0.0]
        for index in range(3):
            if not travel[index] == 0.0:
                acceleration = self._parameters_accel[index].last_known_value
                delta_t = travel[index] / speed[index] - speed[index] / acceleration
                if delta_t <= 0:
                    times[index] = 2.00 * (travel[index] / acceleration) ** 0.5
                else:
                    times[index] = (
                        speed[index] / acceleration + travel[index] / speed[index]
                    )
        # Calculation is generally ~1% faster than recorded values.
        # In addition, the pause command adds 0.1 S extra.
        return (max(times) + 0.1) * 1.015

    def _check_move(
        self, start: GrblPosition, end: GrblPosition, parameter: ArduinoParameter
    ) -> bool:
        """
        Groups some checks required before actually writing move commands to the device.

        @param start: GrblPosition
            The start position (current position) of the command.
        @param end: GrblPosition
            The destination of the move command.
        @param parameter: ArduinoParameter
            The parameter requesting the move.
        @return: bool
            Returns True if the move should be performed and false if not (same position).
        """
        d_xyz = [0.0, 0.0, 0.0]
        for i in range(3):
            if end.value[i] is not None:
                d_xyz[i] = start.value[i] - end.value[i]
                if abs(d_xyz[i]) <= 0.1:
                    d_xyz[i] = 0.0

        if all(v == 0.0 for v in d_xyz):
            return False

        # ensure both needles are up if move involves x or y.
        if any(not v == 0.0 for v in d_xyz[:1]):
            if not self._ignore_safe_z:
                if start.z < self._safe_z or (
                    end.z is not None and end.z < self._safe_z
                ):
                    error = ForbiddenMoveError(
                        f"Attempt to move sampler while below safe height!"
                        f" Safe height: {self._safe_z} mm, current: {start.z} mm.",
                        device=self,
                        parameter=parameter,
                    )
                    self.log_exception(error)
                    raise error

            if self._parameter_aux_needle.last_known_value == "ON":
                error = ForbiddenMoveError(
                    f"Attempt to move sampler while auxiliary needle is activated!",
                    device=self,
                    parameter=parameter,
                )
                self.log_exception(error)
                raise error

        return True

    def _write(self, parameter: ArduinoParameter, value: Any) -> None:
        """
        Low-level function to write data to the device.

        @param parameter: DeviceParameter
            The parameter which will be accessed.
        @param value:
            The value of the parameter to be written to the device.
        @raise: serial.SerialException, ValueError
            In case of issues with serial communication.
        """
        pause_gcode = False
        if parameter in self._parameters_motions:
            current_position = self.__getitem__("position")

            if not self._check_move(current_position, value, parameter):
                self.log(f"Completed '{parameter.name}' (same position).")
                return

            self._parameter_pause.acknowledge_timeout = (
                self._estimated_time_to(
                    current_position, value, parameter.internal_id == "G0"
                )
                * self._timeout_multiplier
                + 0.5  # Add some time to account for sending and receiving commands (prevent timeout for 0 distance).
            )
            pause_gcode = True
            self._moved_since_zero = True
        ArduinoDevice._write(self, parameter, value)
        if pause_gcode:
            self.__setitem__("pause", 0.1)
            self.log(f"Completed '{parameter.name}'.")
        if parameter is self._parameter_aux_needle:
            sleep(0.5)
        elif parameter is self._parameter_home:
            self._moved_since_zero = False


class DummySampler(Sampler):
    """
    Dummy Sampler device which can be initialized without connecting to a real device.
    This is for debugging purposes only.
    """

    def open(self, comport: str) -> None:
        pass

    def is_open(self) -> bool:
        return True

    def read_grbl_settings(self) -> None:
        self._log_parameter_start("Reading grbl settings.")
        with self._serial_thread_lock:
            try:
                response = [
                    "$0=10 (step pulse, usec)",
                    "$1=25 (step idle delay, msec)",
                    "$2=0 (step port invert mask:00000000)",
                    "$3=0 (dir port invert mask:00000000)",
                    "$4=0 (step enable invert, bool)",
                    "$5=0 (limit pins invert, bool)",
                    "$6=0 (probe pin invert, bool)",
                    "$10=3 (status report mask:00000011)",
                    "$11=0.010 (junction deviation, mm)",
                    "$12=0.002 (arc tolerance, mm)",
                    "$13=0 (report inches, bool)",
                    "$20=1 (soft limits, bool)",
                    "$21=1 (hard limits, bool)",
                    "$22=1 (homing cycle, bool)",
                    "$23=3 (homing dir invert mask:00000000)",
                    "$24=25.000 (homing feed, mm/min)",
                    "$25=2000.000 (homing seek, mm/min)",
                    "$26=250 (homing debounce, msec)",
                    "$27=1.000 (homing pull-off, mm)",
                    "$100=400.000 (x, step/mm)",
                    "$101=400.000 (y, step/mm)",
                    "$102=400.000 (z, step/mm)",
                    "$110=6000.000 (x max rate, mm/min)",
                    "$111=6000.000 (y max rate, mm/min)",
                    "$112=1000.000 (z max rate, mm/min)",
                    "$120=100.000 (x accel, mm/sec^2)",
                    "$121=100.000 (y accel, mm/sec^2)",
                    "$122=50.000 (z accel, mm/sec^2)",
                    "$130=173.000 (x max travel, mm)",
                    "$131=291.000 (y max travel, mm)",
                    "$132=77.500  (z max travel, mm)",
                ]
                line_pattern = re.compile(r"^\$(?P<number>\d+)=(?P<value>\d+.?\d*)")
                for line in response:
                    pattern_match = line_pattern.search(line)
                    if pattern_match is not None:
                        setting_number = int(pattern_match.group("number"))
                        setting_value = pattern_match.group("value")
                        parameter_name = self._grbl_settings[setting_number][0]
                        # noinspection PyTypeChecker
                        parameter: ArduinoParameter = self.parameter_by_name(
                            parameter_name
                        )
                        parameter.last_known_value = self._extract(
                            parameter=parameter, response=setting_value
                        )
                        self.log(
                            f"Read '{parameter_name}' <- {setting_value}{parameter.units}",
                            level="ok",
                        )
            except (SerialException, ValueError):
                error = DeviceCommunicationError(self, f"Failed to read grbl settings.")
                self.log_exception(error)
                raise error
        self._parameter_feed.max_value = min(
            self._parameters_max_rate[0].last_known_value,
            self._parameters_max_rate[1].last_known_value,
        )
        homing_pulloff = self.parameter_by_name("homing_pull-off").last_known_value
        self._position_min = GrblPosition()
        self._position_min.x = 0.0
        self._position_min.y = 0.0
        self._position_min.z = -(
            self._parameters_travel[2].last_known_value - homing_pulloff
        )
        self._position_max = GrblPosition()
        self._position_max.x = (
            self._parameters_travel[0].last_known_value - homing_pulloff
        )
        self._position_max.y = (
            self._parameters_travel[1].last_known_value - homing_pulloff
        )
        self._position_max.z = 0.0
        for parameter in self._parameters_motions:
            parameter.min_value = self._position_min
            parameter.max_value = self._position_max
        self._log_parameter_success("Read grbl settings.")

    def initialize(self) -> None:
        self.read_grbl_settings()
        self._initialized = True

    def __getitem__(self, item):
        if item == "position":
            return GrblPosition(x=0, y=0, z=0)

    def prepare_for_close(self) -> None:
        pass
