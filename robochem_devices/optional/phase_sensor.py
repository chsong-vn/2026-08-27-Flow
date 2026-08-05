"""
File: phase_sensor.py
Author: Simone Pilon - Noël Research Group - 2023
GitHub: https://github.com/simone16

Description: Device for the control of an array of phase sensor via Arduino board. Find more information on the device
    at https://github.com/Noel-Research-Group/Array_Devices_Omniplatypus .
"""

from typing import Any, Tuple
import re
import pause
from bidict import bidict

from ..device import ParameterValue
from ..device_arduino import (
    ArduinoParameter,
    ParameterAccess,
    ArduinoValue,
    ArduinoError,
    ArduinoValueRun,
    ArduinoValueEnum,
)
from ..array_arduino import (
    ProxyArduinoDevice,
    ArrayArduinoDevice,
)
from ..errors import (
    SensorError,
    DeviceError,
    DeviceTimeoutError,
)


class PhaseValue(ArduinoValueEnum):
    """The result of the phase detection."""

    _raw_values = bidict({"ERROR": 0, "CLEAR_LIQUID": 1, "OPAQUE_LIQUID": 2, "GAS": 3})

    def is_liquid(self) -> bool:
        """Returns true if the phase is liquid, false if it is gas.

        @return: bool
            True if liquid phase is being detected."""
        if self.value is None:
            raise ValueError("Cannot check if invalid value is liquid.")
        return not self.value == 3


class PhaseMonitoringValue(ArduinoValueEnum):
    """Value for ArduinoParameters describing how a phase sensor should be monitored."""

    _raw_values = bidict({"NEVER": 0, "ONCE": 1, "ALWAYS": 2})


class PhaseSensorArrayError(ArduinoError):
    """Phase sensor array specific error parameter value."""

    _error_types = {
        0: "No error",
        1: "Communication error",
    }

    _parameter_error_id = 1
    _no_error_id = 0


class PhaseSensor(ProxyArduinoDevice):
    """
    Read and monitor the state of a single phase sensor. These are grouped in arrays and cannot be instantiated as
    stand alone.
    """

    _parent: "PhaseSensorArray"

    def __init__(
        self,
        parent: "PhaseSensorArray",
        serial_interface,
        serial_lock,
        array_index: int,
    ) -> None:
        ProxyArduinoDevice.__init__(
            self,
            parent=parent,
            serial_interface=serial_interface,
            serial_lock=serial_lock,
            array_index=array_index,
        )
        self._array_parameter_begin = 10
        self._array_parameter_period = 10
        self.generic_name = "Phase Sensor"

        base_variable_number = self.base_variable_number()

        parameter = ArduinoParameter(
            name="phase",
            access_level=ParameterAccess.R,
            value_type=PhaseValue,
            internal_id=base_variable_number,
        )
        parameter.description = "Read the phase at the sensor digitally"
        self.add_parameter(parameter)
        self._parameter_phase = parameter

        parameter = ArduinoParameter(
            name="analog",
            access_level=ParameterAccess.R,
            value_type=int,
            internal_id=base_variable_number + 1,
        )
        parameter.description = "Read the phase at the sensor analogically"
        parameter.min_value = 0
        parameter.max_value = 1023
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="monitor",
            access_level=ParameterAccess.RW,
            value_type=PhaseMonitoringValue,
            internal_id=base_variable_number + 2,
        )
        parameter.description = "Generate messages upon phase change"
        self.add_parameter(parameter)
        self._parameter_monitor = parameter

        parameter = ArduinoParameter(
            name="calibrate",
            access_level=ParameterAccess.W,
            value_type=ArduinoValueRun,
            internal_id=base_variable_number + 3,
        )
        parameter.description = "Calibrate sensor"
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="error",
            access_level=ParameterAccess.R,
            value_type=PhaseSensorArrayError,
            internal_id=4,
        )
        parameter.description = "Error register"
        self._error_parameter = parameter
        self.add_parameter(parameter)

    def read_event(self) -> PhaseValue | None:
        """
        Read messages generated from the phase sensor when monitoring.

        @return: Phase | None
            Returns the newly detected phase.
            If no message was received, returns None.
        """
        result = self._parent.read_event(origin_filter=self)
        if result is not None:
            # If monitoring was set to once, it should now be never.
            if self._parameter_monitor.last_known_value == 1:
                self._parameter_monitor.last_known_value = 0
            return result[1]
        return None

    def wait(self, timeout: float | None = None) -> PhaseValue | None:
        """
        Wait for a phase change condition to occur.

        @parameter timeout: float | None
            If provided, the function times out after the given time [S]. If not the default timeout is used.
        @return: Phase | None
            Returns the phase resulting after the event.

        @raise: SensorError
            If the sensor is disconnected.
        @raise: DeviceTimeoutError
            If the function times out.
        """
        if (
            self._parameter_monitor.last_known_value is None
            or self._parameter_monitor.last_known_value == 0
        ):
            error = DeviceError(
                "Attempting to wait on a device with monitoring disabled.", device=self
            )
            self.log_exception(error)
            raise error
        result = self._parent.wait(timeout=timeout, origin_filter=self)
        if result is not None:
            # If monitoring was set to once, it should now be never.
            if self._parameter_monitor.last_known_value == 1:
                self._parameter_monitor.last_known_value = 0
            return result[1]

    def _extract(self, parameter: ArduinoParameter, response: str) -> Any:
        """
        Low-level function to translate the device response into the parameter value.
        Ensures the correct type is returned.

        @param parameter: DeviceParameter
            The parameter which was read and produced the response.
        @param response: str
            The response of the device.
        @return: Any
            The value of the parameter fulfilling the type requirement of the parameter.
        """
        result = ProxyArduinoDevice._extract(self, parameter, response)
        if isinstance(result, PhaseValue):
            if result.value == 0:
                error = SensorError(f"Sensor appears to be disconnected.", device=self)
                self.log_exception(error)
                raise error
        return result


class PhaseList(ParameterValue, ArduinoValue):
    """Special list with values for several phase sensors."""

    def __init__(self):
        """Creates a dict based on the serial message received by the device."""
        ArduinoValue.__init__(self)
        self.value = []

    def from_serial_string(self, message: str) -> None:
        """
        Parses a string representing the value of this object over serial from the Arduino device and stores it in
        this object.

        @param message: str
            The string received via serial.
        """
        self.value = []
        for char in message:
            if char == ";":
                break
            p = PhaseValue()
            p.from_serial_string(char)
            self.value.append(p)

    def __str__(self):
        result = "["
        for element in self.value:
            result += str(element) + ", "
        result = result[:-2]
        result += "]"
        return result


class PhaseSensorArray(ArrayArduinoDevice):
    """
    Control an array of phase sensors.

    Attributes:
        default_timeout: float  Default time in seconds before the wait parameter or function returns.
    """

    default_timeout: float

    _event_message_pattern = re.compile(r"(?P<sensor_id>\d+)=(?P<phase>[0-3])\r?\n?")
    _proxy_types = {"phase_sensor": PhaseSensor}
    _default_proxy = PhaseSensor

    def __init__(self):
        ArrayArduinoDevice.__init__(self)
        self.generic_name = "Phase Sensors Array"
        self.default_timeout = 180.0
        self._max_proxies = 4

        parameter = ArduinoParameter(
            name="max_sensors",
            access_level=ParameterAccess.R,
            value_type=int,
            internal_id=2,
        )
        parameter.description = "Maximum number of sensors supported by array"
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="read_all",
            access_level=ParameterAccess.R,
            value_type=PhaseList,
            internal_id=3,
        )
        parameter.description = "Read all sensors within array"
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="error",
            access_level=ParameterAccess.R,
            value_type=PhaseSensorArrayError,
            internal_id=4,
        )
        parameter.description = "Error register"
        self._error_parameter = parameter
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="save",
            access_level=ParameterAccess.W,
            value_type=ArduinoValueRun,
            internal_id=5,
        )
        parameter.description = "Save parameters as default"
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="reset",
            access_level=ParameterAccess.W,
            value_type=ArduinoValueRun,
            internal_id=6,
        )
        parameter.description = "Restore parameters to factory"
        self.add_parameter(parameter)

    @staticmethod
    def parse_phase_list(message: str) -> list:
        """
        Parses the message generated when reading all sensors at once and creates a list of values.

        @param message: str
            The message received via serial.
        @return: list
            A list of Phase values for each sensor.
        """
        result = []
        for char in message:
            if char == ";":
                break
            p = PhaseValue()
            p.from_serial_string(char)
            result.append(p)
        return result

    def open(self, comport: str) -> None:
        """
        Connects to the device. The connection must open before data can be sent or received.

        @param comport: str
            Identifier of the serial port to which the device is connected.

        @raise: serial.SerialException, ValueError
            If connection fails.
        """
        ArrayArduinoDevice.open(self, comport=comport)
        # Suppress non-initialized warning.
        initialization = self._initialized
        self._initialized = True
        try:
            self._max_proxies = self.__getitem__("max_sensors")
        finally:
            self._initialized = initialization

    def read_event(
        self, origin_filter: PhaseSensor | None = None
    ) -> Tuple[PhaseSensor, PhaseValue] | None:
        """
        Read messages generated from the phase sensors when monitoring.

        @parameter origin_filter: PhaseSensor | None
            If provided, only events generated by the phase sensor provided are returned.
        @return: (PhaseSensor, Phase) | None
            Returns the phase sensor which generated the event and the resulting phase measured by it.
            If no message was received, returns None.
        """
        response = self.flush_buffer_in(whole_line=True)
        if response != "":
            match = self._event_message_pattern.match(response)
            if match is None:
                # noinspection PyTypeChecker
                self.parse_error(response, self.parameter_by_name("monitor"))
            else:
                sensor_id = int(match.group("sensor_id"))
                phase = PhaseValue(int(match.group("phase")))
                # noinspection PyTypeChecker
                origin: PhaseSensor = None
                for name, proxy in self._proxies.items():
                    if proxy.array_index == sensor_id:
                        # noinspection PyTypeChecker
                        origin = proxy
                        break
                if origin is None:
                    self.log(
                        f"Received event from unregistered sensor '{sensor_id}'.",
                        level="warning",
                    )
                else:
                    if phase.value == 0:
                        error = SensorError(
                            f"Sensor appears to be disconnected.", device=origin
                        )
                        origin.log_exception(error)
                        raise error
                    origin.log(f"Received phase change event ({phase}).", level="ok")
                    if origin_filter is not None and origin_filter is not origin:
                        self.log(
                            f"Received unexpected change event ({phase}) from '{origin}'.",
                            level="warning",
                        )
                    else:
                        return origin, phase
        return None

    def wait(
        self, timeout: float | None = None, origin_filter: PhaseSensor | None = None
    ) -> (PhaseSensor, PhaseValue):
        """
        Wait for a phase change condition to occur.

        @parameter timeout: float | None
            If provided, the function times out after the given time [S]. If not the default timeout is used.
        @parameter origin_filter: PhaseSensor | None
            If provided, only events generated by the phase sensor provided are returned.
        @return: PhaseSensor, Phase
            Returns the phase sensor which generated the event and the resulting phase measured by it.

        @raise: SensorError
            If the sensor is disconnected.
        @raise: DeviceTimeoutError
            If the function times out.
        """
        if origin_filter is not None:
            if not isinstance(origin_filter, PhaseSensor):
                error = TypeError("Origin must be of type 'PhaseSensor'.")
                error.add_note(self.exception_note())
                self.log_exception(error)
                raise error
            if origin_filter not in self._proxies.values():
                error = ValueError(
                    f"origin '{origin_filter}' is not a sub-device of this array."
                )
                error.add_note(self.exception_note())
                self.log_exception(error)
                raise error
        if timeout is None:
            timeout = self.default_timeout
        response = ""
        while timeout > 0.0:
            pause.seconds(0.1)
            timeout -= 0.1
            response += self.flush_buffer_in()
            if response != "":
                match = PhaseSensorArray._event_message_pattern.match(response)
                if match is None:
                    self.log("Received unexpected data:", level="warning")
                    self.log(response, decorate=False)
                else:
                    sensor_id = int(match.group("sensor_id"))
                    phase = PhaseValue(int(match.group("phase")))
                    origin = None
                    for name, proxy in self._proxies.items():
                        if proxy.array_index == sensor_id:
                            origin = proxy
                            break
                    if origin is None:
                        self.log(
                            f"Received event from unregistered sensor '{sensor_id}'.",
                            level="warning",
                        )
                    else:
                        if phase.value == 0:
                            error = SensorError(
                                f"Sensor appears to be disconnected.", device=origin
                            )
                            origin.log_exception(error)
                            raise error
                        origin.log(
                            f"Received phase change event ({phase}).", level="ok"
                        )
                        if origin_filter is not None and origin_filter is not origin:
                            self.log(
                                f"Received unexpected change event ({phase}) from '{origin}'.",
                                level="warning",
                            )
                        else:
                            return origin, phase
        if timeout <= 0:
            error = DeviceTimeoutError(
                "Waiting for phase change event timed-out.", device=self
            )
            self.log_exception(error)
            raise error
