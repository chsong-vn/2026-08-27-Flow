"""
File: gpio.py
Author: Simone Pilon - Noël Research Group - 2023
GitHub: https://github.com/simone16

Description: Device for direct access to Arduino GPIOs, PWM and analog inputs. Find more information on the device
    at https://github.com/Noel-Research-Group/Array_Devices_Omniplatypus .
"""

# todo modify arduino code to allow digital inputs and outputs to be inverted (1 -> LOW).


from bidict import bidict
from .device_arduino import (
    ArduinoParameter,
    ParameterCachingPolicy,
    ParameterAccess,
    ArduinoError,
    ArduinoValueRun,
    ArduinoValueEnum,
)
from .array_arduino import (
    ProxyArduinoDevice,
    ArrayArduinoDevice,
)
from .errors import GpioError


class PortModeValue(ArduinoValueEnum):
    """Pin Mode for digital Arduino GPIOs."""

    _raw_values = bidict({"OUTPUT": 0, "INPUT": 1, "PWM": 2})


class DigitalPortValue(ArduinoValueEnum):
    """Digital value for Arduino GPIO port."""

    _raw_values = bidict({"OFF": 0, "ON": 1})


class GpioArrayError(ArduinoError):
    """GPIO array specific error parameter value."""

    _error_types = {
        0: "No error",
        1: "Communication error",
        2: "GPIO error",
    }

    _error_values = {
        2: {
            0: "No error",
            1: "Digital pin has no PWM",
            2: "Value for digital pin out or range",
            3: "Value for pwm pin out or range",
            4: "Attempt to write to an input pin",
            5: "Attempt to read from an output pin",
            6: "Invalid mode identifier used",
            7: "Attempt to use a function on a pin without setting the mode correctly",
        }
    }

    _error_exceptions = {
        2: GpioError,
    }

    _parameter_error_id = 1
    _no_error_id = 0


class DigitalPort(ProxyArduinoDevice):
    """
    Read and write digital values to a Port. Some ports support writing PWM values as well.
    """

    _parent: "GpioArray"

    def __init__(
        self,
        parent: "GpioArray",
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
        self.generic_name = "Digital Port"

        base_variable_number = self.base_variable_number()

        parameter = ArduinoParameter(
            name="mode",
            access_level=ParameterAccess.RW,
            value_type=PortModeValue,
            internal_id=base_variable_number,
        )
        parameter.description = "Set the port mode"
        parameter.caching_policy = ParameterCachingPolicy.TRY
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="read",
            access_level=ParameterAccess.R,
            value_type=DigitalPortValue,
            internal_id=base_variable_number + 1,
        )
        parameter.description = "Read the port value as a digital input"
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="write",
            access_level=ParameterAccess.RW,
            value_type=DigitalPortValue,
            internal_id=base_variable_number + 2,
        )
        parameter.description = (
            "Write the port value as a digital output, reads set-value"
        )
        parameter.caching_policy = ParameterCachingPolicy.TRY
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="pwm",
            access_level=ParameterAccess.RW,
            value_type=int,
            internal_id=base_variable_number + 3,
        )
        parameter.max_value = 255
        parameter.min_value = 0
        parameter.description = "Write the port value as a PWM value, reads set-value"
        parameter.caching_policy = ParameterCachingPolicy.TRY
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="error",
            access_level=ParameterAccess.R,
            value_type=GpioArrayError,
            internal_id=4,
        )
        parameter.description = "Error register"
        self._error_parameter = parameter
        self.add_parameter(parameter)


class AnalogPort(ProxyArduinoDevice):
    """
    Read analog values from a Port.
    """

    _parent: "GpioArray"

    def __init__(
        self,
        parent: "GpioArray",
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
        self.generic_name = "Analog Port"

        base_variable_number = self.base_variable_number()

        parameter = ArduinoParameter(
            name="read",
            access_level=ParameterAccess.R,
            value_type=int,
            internal_id=base_variable_number,
        )
        parameter.max_value = 1024
        parameter.min_value = 0
        parameter.description = "Read analog port value"
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="error",
            access_level=ParameterAccess.R,
            value_type=GpioArrayError,
            internal_id=4,
        )
        parameter.description = "Error register"
        self._error_parameter = parameter
        self.add_parameter(parameter)


class SolenoidValveValue(ArduinoValueEnum):
    """Value for driving solenoid valve operation."""

    _raw_values = bidict({"CLOSE": 0, "OPEN": 1})


class SolenoidValve(DigitalPort):
    """
    Control a solenoid valve via a DigitalPort.
    This is functionally equivalent to a DigitalPort which comes pre-configured as a digital output to drive a solenoid
    valve using an external power stage (mosfet, relay, ecc...).
    """

    def __init__(
        self,
        parent: "GpioArray",
        serial_interface,
        serial_lock,
        array_index: int,
    ) -> None:
        DigitalPort.__init__(
            self,
            parent=parent,
            serial_interface=serial_interface,
            serial_lock=serial_lock,
            array_index=array_index,
        )

        self.generic_name = "Solenoid Valve"

        # Add intuitive shorthand for valve open and close
        parameter = ArduinoParameter(
            name="valve",
            access_level=ParameterAccess.RW,
            value_type=SolenoidValveValue,
            # internal_id=self.parameter_by_name("write").internal_id,
            internal_id=self.base_variable_number() + 2,
        )
        parameter.description = "Actuate the solenoid valve"
        parameter.caching_policy = ParameterCachingPolicy.TRY
        self.add_parameter(parameter, ignore_shared_id=True)

    def initialize(self) -> None:
        """
        Prepare the device for operation.
        Time intensive operations (movement, zeroing, syncing) are performed here.
        """
        DigitalPort.initialize(self)
        self.__setitem__("mode", "OUTPUT")
        self.__setitem__("valve", "CLOSE")

    def prepare_for_close(self) -> None:
        """
        Ensure the device is in a safe state before closing the connection (and losing control).
        """
        self.__setitem__("valve", "CLOSE")

class Fan(DigitalPort):
    """
    Control a fan via a DigitalPort.
    This is functionally equivalent to a DigitalPort which comes pre-configured as a digital output to drive a fan
     using an external power stage (mosfet, relay, ecc...).
    """

    def __init__(
            self,
            parent: "GpioArray",
            serial_interface,
            serial_lock,
            array_index: int,
    ) -> None:
        DigitalPort.__init__(
            self,
            parent=parent,
            serial_interface=serial_interface,
            serial_lock=serial_lock,
            array_index=array_index,
        )

        self.generic_name = "Fan"

        # Add intuitive shorthand for valve open and close
        parameter = ArduinoParameter(
            name="fan",
            access_level=ParameterAccess.RW,
            value_type=DigitalPortValue,
            # internal_id=self.parameter_by_name("write").internal_id,
            internal_id=self.base_variable_number() + 2,
        )
        parameter.description = "Actuate the solenoid valve"
        parameter.caching_policy = ParameterCachingPolicy.TRY
        self.add_parameter(parameter, ignore_shared_id=True)

    def initialize(self) -> None:
        """
        Prepare the device for operation.
        Time intensive operations (movement, zeroing, syncing) are performed here.
        """
        DigitalPort.initialize(self)
        self.__setitem__("mode", "OUTPUT")
        self.__setitem__("fan", "OFF")

    def prepare_for_close(self) -> None:
        """
        Ensure the device is in a safe state before closing the connection (and losing control).
        """
        self.__setitem__("fan", "OFF")


class GpioArray(ArrayArduinoDevice):
    """Control an Arduino device acting as an array of digital and analog ports."""

    _proxy_types = {
        "digital": DigitalPort,
        "analog": AnalogPort,
        "solenoid_valve": SolenoidValve,
        "fan": Fan,
    }
    _default_proxy = DigitalPort

    def __init__(self):
        ArrayArduinoDevice.__init__(self)
        self.generic_name = "Phase Sensors Array"
        self.default_timeout = 180.0
        self._max_digital_ports = 10
        self._max_analog_ports = 6
        self._max_proxies = self._max_digital_ports + self._max_analog_ports

        parameter = ArduinoParameter(
            name="max_digital",
            access_level=ParameterAccess.R,
            value_type=int,
            internal_id=2,
        )
        parameter.description = "Maximum number of digital ports supported by array"
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="max_analog",
            access_level=ParameterAccess.R,
            value_type=int,
            internal_id=3,
        )
        parameter.description = "Maximum number of analog ports supported by array"
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="error",
            access_level=ParameterAccess.R,
            value_type=GpioArrayError,
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
            self._max_digital_ports = self.__getitem__("max_digital")
            self._max_analog_ports = self.__getitem__("max_analog")
            self._max_proxies = self._max_digital_ports + self._max_analog_ports
        finally:
            self._initialized = initialization

    def setup_array(self, subdevices: dict) -> None:
        """
        Setup handler for 'array'.
        The 'array' setup option populates ArrayDevices with their subdevices.

        @param subdevices: dict
            Dict of name: str -> data: dict pairs defining the identifier for each subdevice and its attributes within
            the array. 'index' must be an attribute, optionally, 'class' can be given to specify the proxy class to use
            (must be in _proxy_types).
        """
        # Vanilla setup_array requires numerical indexes.
        # Convert pin names here, then pass to base implementation.
        for name in subdevices.keys():
            if "index" in subdevices[name].keys():
                index = subdevices[name]["index"]
                if isinstance(index, str):
                    index = self.arduino_uno_pin_number(index)
                    subdevices[name]["index"] = index
        ArrayArduinoDevice.setup_array(self, subdevices)

    def _add_proxy(self, name: str, index: int, proxy_type: type) -> None:
        """
        Add a proxy sub-device to this array.

        @param name: str
            The unique identifier which will be used for the sub-device.
        @param index: int
            The index of the sub-device within the array.
        @param proxy_type: type
            The class type to use for the proxy device.
        """
        index -= 2  # Convert from Arduino pin number to device index
        error = ValueError(
            f"Invalid index '{index}' for Gpio sub-device type '{proxy_type}'."
        )
        error.add_note(self.exception_note())
        if index < 0:
            raise error
        if issubclass(proxy_type, DigitalPort):
            if index > self._max_digital_ports:
                raise error
        elif issubclass(proxy_type, AnalogPort):
            if index <= self._max_digital_ports:
                raise error
        else:
            error = TypeError(
                f"Gpio sub-device type '{proxy_type}' does not subclass DigitalPort or AnalogPort."
            )
            error.add_note(self.exception_note())
            raise error
        ArrayArduinoDevice._add_proxy(
            self, name=name, index=index, proxy_type=proxy_type
        )
