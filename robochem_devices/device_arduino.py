"""
File: device_arduino.py
Author: Simone Pilon - Noël Research Group - 2023
GitHub: https://github.com/simone16

Description: Extends device.py with additional behaviour to control custom arduino-based devices.
"""

import serial
import pause
import re
from threading import Lock
from time import sleep
from typing import Any, Callable, Optional
from bidict import bidict

from .device import (
    BaseDevice,
    DeviceSetupOption,
    DeviceParameter,
    ParameterAccess,
    ParameterCachingPolicy,
    ParameterValue,
    ParameterValueRun,
    ParameterEnumValue,
)
from .errors import (
    DeviceCommunicationError,
    DeviceError,
    ParameterParsingError,
    ParameterCommError,
    ParameterTimeoutError,
    ParameterAcknowledgeError,
)


class ArduinoValue:
    """
    Interface for Arduino parameter values. In case a custom object is used as value for a parameter, it should
    inherit from this and a fitting ParameterValue.
    Note: inheriting from this alone will not work!
    """

    def to_serial_string(self) -> str:
        """
        Generate a string to transmit the value of this object over serial to the Arduino device.

        @return: str
            The string to be sent to serial (with necessary command / decorators).
        """
        raise NotImplementedError()

    def from_serial_string(self, message: str) -> None:
        """
        Parses a string representing the value of this object over serial from the Arduino device and stores it in
        this object.

        @param message: str
            The string received via serial.
        """
        raise NotImplementedError()


class ArduinoValueRun(ParameterValueRun, ArduinoValue):
    """Value for ArduinoParameters which trigger a command with no argument."""

    _keyword = "RUN"

    def __init__(self, value: str) -> None:
        """
        To prevent accidental triggering, a valid value must be created from the string 'run' (case-insensitive).

        @param value: str
            Set to 'run' to obtain a valid value.
        """
        ArduinoValue.__init__(self)
        ParameterValueRun.__init__(self, value=value)

    def to_serial_string(self) -> str:
        """
        Generate a string to transmit the value of this object over serial to the Arduino device.

        @return: str
            The string to be sent to serial (with necessary command / decorators).
        """
        return ""


class ArduinoValueEnum(ParameterEnumValue, ArduinoValue):
    """
    Base value for Enumeration-type values (a set of named values identified by an integer number).
    To declare Enums for Arduino parameters, simply subclass and define class attribute _raw_values.
    """

    value: int
    _raw_values: bidict[str, int]

    def __init__(self, value: int | str | None = None):
        ArduinoValue.__init__(self)
        if value is not None:
            if not isinstance(value, (str, int, type(self))):
                raise TypeError(
                    f"Invalid type '{type(value)}' for {type(self)} constructor."
                )
        ParameterEnumValue.__init__(self, value)

    def to_serial_string(self) -> str:
        """
        Generate a string to transmit the value of this object over serial to the Arduino device.

        @return: str
            The string to be sent to serial (with necessary command / decorators).
        """
        if self.value is None:
            raise ValueError("Cannot send invalid value over serial.")
        return str(self.value)

    def from_serial_string(self, message: str) -> None:
        """
        Parses a string representing the value of this object over serial from the Arduino device and stores it in
        this object.

        @param message: str
            The string received via serial.
        """
        try:
            self.value = int(message)
        except ValueError:
            raise ValueError(f"Invalid value '{message}' for {type(self)}.")
        if self.value not in self._raw_values.inverse.keys():
            raise ValueError(f"Invalid value '{self.value}' for {type(self)}.")

    def __str__(self) -> str:
        return self._raw_values.inverse[self.value]


class ArduinoValueOnOff(ArduinoValueEnum):
    """Value for ArduinoParameters describing a boolean ON / OFF value."""

    _raw_values = bidict({"OFF": 0, "ON": 1})


class ArduinoError(ParameterValue, ArduinoValue):
    """Error register of our custom Arduino devices."""

    _error_pattern = re.compile(r"(?P<type>\d+)-(?P<value>\d+)")
    _error_types = {0: "No error"}
    _error_values = {}
    _error_exceptions = {}
    _parameter_error_id = 1
    _no_error_id = 0

    value: list[int]

    def __init__(self, value: list[int] | None = None):
        ArduinoValue.__init__(self)
        error = None
        if value is not None:
            if not isinstance(value, list):
                error = TypeError(f"Invalid type {type(value)} for {type(self)}.")
            elif not len(value) == 2:
                error = ValueError(
                    f"{type(self)} requires a list of exactly 2 integers."
                )
            elif not isinstance(value[0], int) or not isinstance(value[1], int):
                error = ValueError(
                    f"{type(self)} requires a list of exactly 2 integers."
                )
            if error is not None:
                raise error
        ParameterValue.__init__(self, value)

    def is_error(self) -> bool:
        """
        Checks whether the value of this object corresponds to an error condition or not.

        @return: bool
            Returns True if the current value describes an error condition.
        """
        if self.value is None:
            raise ValueError(f"Invalid value for '{type(self)}'")
        return not self.value[0] == self._no_error_id

    def from_serial_string(self, message: str) -> None:
        """
        Parses a string representing the value of this object over serial from the Arduino device and stores it in
        this object.

        @param message: str
            The string received via serial.
        """
        match = ArduinoError._error_pattern.match(message)
        if match is None:
            raise ValueError(f"Invalid error register value received '{message}'.")
        self.value = [0, 0]
        self.value[0] = int(match.group("type"))
        self.value[1] = int(match.group("value"))

    def exception(self, device: "ArduinoDevice") -> DeviceError | None:
        """
        Create a DeviceError exception matching this error value. Returns None if no error was found.

        @param device: ArduinoDevice
            The device which originated the error.
        @return: DeviceError | None
            An exception matching the error value or None.
        """
        if self.value is None:
            raise ValueError(f"Invalid value for '{type(self)}'")
        if self.value[0] == self._no_error_id:
            return None
        if self.value[0] == self._parameter_error_id:
            parameter = device._parameter_by_id(self.value[1])
            if parameter is None:
                name = str(self.value[1])
            else:
                name = parameter.name
            return DeviceCommunicationError(
                f"Invalid value for parameter '{name}'.", device=device
            )
        if self.value[0] in self._error_exceptions.keys():
            message = self._error_types[self.value[0]]
            if (
                self.value[0] in self._error_values.keys()
                and self.value[1] in self._error_values[self.value[0]].keys()
            ):
                message = self._error_values[self.value[0]][self.value[1]]
            return self._error_exceptions[self.value[0]](message, device=device)
        return DeviceError(f"Error code '{self}'", device=device)

    def __str__(self):
        """
        Give a string description of the object.

        @return: str
            A string description.
        """
        if self.value is None:
            return "Invalid value"
        comment = ""
        if self.value[0] in self._error_types.keys():
            comment = f" ({self._error_types[self.value[0]]})"
        return f"{self.value[0]}-{self.value[1]}" + comment


class ArduinoParameter(DeviceParameter):
    """
    Holds information on a single parameter monitored or affected by an Arduino device.
    This extends DeviceParameter with specific Arduino-devices data.

    Attributes:
        write_acknowledge               True if data is expected after a write operation.
        read_acknowledge                True if data is excepted after a read operation.
        acknowledge_timeout             Max write response timeout in S.
        acknowledge_value               The expected value(s) for the acknowledgment.
        to_serial_string                A callable function which takes the parameter value (object) and transforms it into
                                        the string to send to serial. Defaults to None and default processing is used.
        from_serial_string              A callable function which takes the string received from serial and transforms it
                                        into a parameter value (object) to be returned. Defaults to None and default
                                        processing is used.
        float_rounding                  Number of digit after comma to preserve when sending floating point numbers.
        ignore_timeout_warning          If False, exception is raised upon parameter acknowledge timeout.
        ignore_acknowledge_warning      If False, exception is raised upon receiving invalid acknowledge value.
        stop_command                    Command to send to the device to stop execution of this parameter
                                        asynchronously. This is set to None by default, which is the case for most
                                        parameters, as they cannot be stopped. If a parameter can be stopped before it
                                        produces an acknowledge value, this string is what will be sent to stop it.
                                        The device is still excepted to produce the original acknowledge value after the
                                        stop command is issued, but not an additional one.

        internal_id                The internal ID for Arduino parameters is either a variable number (int)
                                   or a raw command (str).

    Serial message processing:
        Data sent and received over serial is transformed into the parameter data type (value_type) according to one of
        3 methods, evaluated in this order:
            1. If the parameter has a from_serial_string (or to_serial_string for writing), this is called to process the
                data.
            2. If the value_type is a subclass of ArduinoValue, its ArduinoValue.from/to_serial_string method is used.
            3. If none of the above is available, the serial message is fed into the constructor of the value_type
                (reading) or its __str__ representation is used (writing).
    """

    write_acknowledge: bool
    read_acknowledge: bool
    acknowledge_timeout: Optional[float]
    acknowledge_value: Optional[str | list[str]]
    to_serial_string: Optional[Callable[[Any], str]]
    from_serial_string: Optional[Callable[[str], Any]]
    float_rounding: int
    ignore_timeout_warning: bool
    ignore_acknowledge_warning: bool
    stop_command: Optional[str]

    internal_id: int | str

    def __init__(
        self,
        name: str,
        access_level: ParameterAccess,
        value_type: type,
        internal_id: int | str,
    ):
        DeviceParameter.__init__(self, name, access_level, value_type, internal_id)

        self.write_acknowledge = False
        self.read_acknowledge = False
        self.acknowledge_timeout = None
        self.acknowledge_value = None
        self.to_serial_string = None
        self.from_serial_string = None
        self.float_rounding = 3
        self.ignore_timeout_warning = False
        self.ignore_acknowledge_warning = False
        self.stop_command = None

        if self.value_type is float:
            self.to_serial_string = self.float_to_serial_string

    def float_to_serial_string(self, value: float) -> str:
        """
        Default method for generating a string representation of a float for serial transmission.
        Limits the number of decimals to the requested amount.

        @param value: float
            The value to be converted to serial string.
        @return: str
            The string representation of the float to be sent through serial.
        """
        return str(round(value, self.float_rounding))

    def to_string(self, value: Any) -> str:
        """
        Return a human-readable version of the parameter with the given value. Adds units and translates numbers
        to strings.

        @param value: Any
            The value of this parameter.
        @return: str
            A string representing the value.
        """
        if self.value_type is float:
            return str(round(value, self.float_rounding)) + self.units
        else:
            return DeviceParameter.to_string(self, value)

    def describe_valid_values(self) -> str:
        """
        Human-readable description of which values are supported for this parameter.

        @return: str
            A description of the valid values range.
        """
        description = ""
        if issubclass(self.value_type, ArduinoValueRun):
            description += f"   value = {ArduinoValueRun.keyword()}{self.units}\n"
            return description
        return DeviceParameter.describe_valid_values(self)


class ArduinoDevice(BaseDevice):
    """
    Handles communication with an arduino-based device via serial port.
    To be able to fully take advantage of this class, the arduino device must comply with the following protocol:

        Support the following commands:
            `Sx=y`  Where x is an integer identifier for a variable/command and y a value of type integer,
                    floating point or string. In case y is of type string, a newline character must terminate the
                    command, in the other cases a newline can terminate the command, but it does not have to.
                    No response is expected after this command is sent.
            `Rx`    Where x is an integer identifier for a variable. The arduino device must respond with the value of x
                    or provide some other response. The response must terminate with a newline character.
                    Any trailing whitespace is removed from the response.
        All device functionalities should be accessible by reading or writing one of the variables (identified by the
        integer x). The user can extend the class with additional methods to handle communications outside of this
        protocol.

        General variables:
            Some variables are expected to be the same across all devices:
                Name    Number 	Access 	    Type 	        Description
                -       0 	    RESERVED 	NONE 	        Do not use x=0.
                ID      1 	    READ/WRITE 	STRING [20] 	Device identifier.

        Usage:
            It is recommended to create a child class for every Arduino device which inherits from this.
            In the constructor, add a DeviceParameter to self._parameters (inherited from BaseDevice) for each variable
            that you want to support. Note: store the variable number in DeviceParameter.internal_ID.

    Thread-safe: multiple threads can access this class and write at the same time, to keep track of which response is
    meant for which thread, a lock prevents concurrent access (the thread will pause until the previous thread receives
    a response).

        Attributes:
            ignore_unexpected_data_warning  If False, exception is raised upon receiving unexpected data.
            _error_parameter    Parameter corresponding to the device error register.
            _internal_buffer        Stores the buffer read by flush_buffer_in when the line mode is set. It is erased
                                    once a newline is read.
    """

    ignore_unexpected_data_warning: bool

    _parameter_type = ArduinoParameter

    _error_parameter: ArduinoParameter | None
    _internal_buffer: str

    def __init__(self, serial_interface=None, serial_lock=None):
        BaseDevice.__init__(self)

        self.generic_name = "Arduino"
        self._error_parameter = None
        self._internal_buffer = ""
        self.ignore_unexpected_data_warning = False
        self._update_delay = 0.1  #  time interval between serial reads [S]
        self._comport = None

        if serial_interface is None:
            self._serial_iface = serial.Serial()
            self._serial_iface.baudrate = 9600
            self._serial_iface.timeout = 1
            self._serial_iface.write_timeout = 1
        else:
            self._serial_iface = serial_interface

        if serial_lock is None:
            self._serial_thread_lock = Lock()
        else:
            self._serial_thread_lock = serial_lock

        parameter_id = ArduinoParameter(
            name="ID", access_level=ParameterAccess.RW, value_type=str, internal_id=1
        )
        parameter_id.description = "Device Identifier"
        parameter_id.caching_policy = ParameterCachingPolicy.TRY
        self.add_parameter(parameter_id)

        ignore_unexpected_data = DeviceSetupOption(
            name="ignore_unexpected_data", handler=self.setup_ignore_unexpected_data
        )
        ignore_unexpected_data.description = (
            "Receiving unexpected data via serial communication is treated as a fatal"
            "error by default, this option allows to only issue a warning when this happens."
        )
        ignore_unexpected_data.value_type = bool
        self.add_setup_option(ignore_unexpected_data)

    def open(self, comport: str) -> None:
        """
        Connects to the device. The connection must open before data can be sent or received.

        @param comport: str
            Identifier of the serial port to which the device is connected.

        @raise: serial.SerialException, ValueError
            If connection fails.
        """
        self.close()
        with self._serial_thread_lock:
            self._serial_iface.port = comport
            try:
                self._serial_iface.open()
                # Opening the serial communication with Arduino causes a reset of the arduino board.
                # As a result, immediately trying to read/write locks up everything. This is a known issue
                # which seems to have no other solution than waiting for the reset to complete (or preventing the
                # reset line from activating, but this prevents writing to program memory).
                pause.seconds(2.0)
                self.log(f"Connected on port '{comport}'.", level="ok")
                self._comport = comport
            except (serial.SerialException, ValueError):
                error = DeviceCommunicationError(
                    f"Failed to connect on port '{comport}'.", device=self
                )
                self.log_exception(error)
                raise error

    def reopen(self):
        """
        Connects to the device after the connection was made a first time.
        Does not require to re-input the connection data.
        """
        if self._comport is None:
            raise RuntimeError(
                f"{self.complete_name}: connection cannot re-open before being opened."
            )
        self.open(self._comport)

    def is_open(self) -> bool:
        """
        Check whether the connection is open.

        @return: bool
            True if the connection is open.
        """
        return self._serial_iface.is_open

    def close(self) -> None:
        """
        Disconnects the device.
        If you need to run some operations before closing, override self.prepare_for_close(),
        rather than this.

        @raise: serial.SerialException, ValueError
            If disconnection fails.
        """
        if self._serial_iface.is_open:
            try:
                self.prepare_for_close()
            finally:
                try:
                    with self._serial_thread_lock:
                        self._serial_iface.close()
                    self.log("Disconnected.", level="ok")
                except (serial.SerialException, ValueError):
                    error = DeviceCommunicationError(
                        f"Failed to close connection on port '{self._serial_iface.port}'.",
                        device=self,
                    )
                    self.log_exception(error)
                    raise error

    def flush_buffer_in(self, whole_line: bool = False) -> str:
        """
        Read everything from the incoming buffer and return it as a string.
        If whole_line is True, only one line is returned and the rest is stored internally and returned at the
        next call. If less than one line is available, nothing will be returned until one line is complete or the
        function is called with whole_line=False.

        @param whole_line: bool = False
            If True, returns the buffer only once a whole line has been read.
            Requires multiple calls until the end of line character is received.
        @return: str
            The contents of the incoming data buffer.
        """
        bytes_to_read = self._serial_iface.in_waiting
        if bytes_to_read > 0:
            self._internal_buffer += self._serial_iface.read(bytes_to_read).decode(
                "ascii"
            )
        result = ""
        if whole_line:
            if "\n" in self._internal_buffer:
                result, self._internal_buffer = self._internal_buffer.split("\n", 1)
                result = result.rstrip("\r")
        else:
            result = self._internal_buffer
            self._internal_buffer = ""
        return result

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
            value_string = parameter.to_serial_string(value)
        elif isinstance(value, ArduinoValue):
            value_string = value.to_serial_string()
        else:
            value_string = str(value)
        return f"S{parameter.internal_id}=" + value_string + "\n"

    def _command_read(self, parameter: ArduinoParameter) -> str:
        """
        Computes the string which needs to be sent to read the value of a parameter.

        @param parameter: ArduinoParameter
            The parameter which needs to be read.
        @return: str
            The command string to send via serial.
        """
        return f"R{parameter.internal_id}\n"

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
        try:
            if parameter.from_serial_string is not None:
                return parameter.from_serial_string(response.rstrip("\r\n"))
            elif issubclass(parameter.value_type, ArduinoValue):
                value = parameter.value_type()
                value.from_serial_string(response)
                return value
            else:
                return parameter.value_type(response.rstrip("\r\n"))
        except ValueError:
            error = ParameterParsingError(
                f"Could not convert device response '{response}' to required type '{parameter.value_type}'.",
                device=self,
                parameter=parameter,
            )
            self.log_exception(error)
            raise error

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
        if message == "":
            return None
        message = f"Received unexpected data: '{message}'."
        if self.ignore_unexpected_data_warning:
            self.log(message, level="warning")
        else:
            error = ParameterCommError(message, device=self, parameter=parameter)
            self.log_exception(error)
            raise error
        return None

    def _receive_acknowledge(self, parameter: ArduinoParameter) -> None:
        """
        Waits for the given parameter to produce an acknowledgment value.
        Blocks the thread until the ack signal is received or timeout occurs.
        The timeout should be set in the parameter itself.

        @param parameter: ArduinoParameter
            The parameter that was accessed.
        """
        stopped = False
        timeout = parameter.acknowledge_timeout
        response = []
        expected_lines = 1
        if isinstance(parameter.acknowledge_value, (list, tuple)):
            expected_lines = len(parameter.acknowledge_value)
        while timeout > 0.0:
            if stopped:
                sleep(self._update_delay)
            else:
                if parameter.stop.wait(self._update_delay):
                    if parameter.stop_command is not None:
                        self._serial_iface.write(parameter.stop_command.encode("ascii"))
                        stopped = True
                        self.log(f"Stopped '{parameter}' asynchronously.")
            timeout -= self._update_delay
            last_response = self.flush_buffer_in(whole_line=True)
            if not last_response == "":
                response.append(last_response)
                if len(response) >= expected_lines:
                    break
        if timeout <= 0:
            message = f"Expected acknowledge after '{parameter.name}', but timed out."
            if parameter.ignore_timeout_warning:
                self.log(message, level="warning")
            else:
                error = ParameterTimeoutError(
                    message,
                    device=self,
                    parameter=parameter,
                )
                self.log_exception(error)
                raise error
            return
        valid_ack = False
        if parameter.acknowledge_value is not None:
            if isinstance(parameter.acknowledge_value, (list, tuple)):
                valid_ack = True
                for i in range(len(response)):
                    if not response[i] == parameter.acknowledge_value[i]:
                        valid_ack = False
                        break
            else:
                valid_ack = response[0] == parameter.acknowledge_value
        if not valid_ack:
            self.parse_error(response[0], parameter)
            message = f"Bad acknowledge '{response}' after '{parameter.name}'."
            if parameter.ignore_acknowledge_warning:
                self.log(message, level="warning")
            else:
                error = ParameterAcknowledgeError(
                    message, device=self, parameter=parameter
                )
                self.log_exception(error)
                raise error
            return

    def _read(self, parameter: ArduinoParameter) -> Any:
        """
        Low-level function to read data from the device.

        @param parameter: DeviceParameter
            The parameter which will be accessed.
        @return:
            The value of the parameter read from the device.
        @raise: serial.SerialException, ValueError
            In case of issues with serial communication.
        """
        try:
            with self._serial_thread_lock:
                try:
                    self.parse_error(self.flush_buffer_in(), parameter)
                    command = self._command_read(parameter)
                    self._serial_iface.write(command.encode("ascii"))
                    response = self._serial_iface.readline().decode("ascii")
                    if parameter.read_acknowledge:
                        self._receive_acknowledge(parameter)
                    return response
                except (serial.SerialException, ValueError):
                    error = DeviceCommunicationError(
                        f"Failed to read variable '{parameter.name}'.", device=self
                    )
                    self.log_exception(error)
                    raise error
        finally:
            if (self._error_parameter is not None) and (
                not parameter.internal_id == self._error_parameter.internal_id
            ):
                self._verify_error_register()

    def _verify_error_register(self) -> None:
        """
        Read the device error register (if defined as self._error_parameter) and raise an exception if
        an error is found. This should be called after every write operation to ensure it was successful.

        @raise: DeviceError
            Specific device-dependent errors.
        """
        if self._error_parameter is not None:
            self._log_parameter_start("Verifying error register.")
            error = self._extract(
                self._error_parameter, self._read(self._error_parameter)
            )
            log_message = f"Read '{self._error_parameter.name}' <- {self._error_parameter.to_string(error)}"
            if error.is_error():
                self._log_parameter_fail(log_message)
                raise error.exception(self)
            else:
                self._log_parameter_success(log_message)

    def _write(self, parameter: ArduinoParameter, value: Any) -> None:
        """
        Low-level function to write data to the device.

        @param parameter: DeviceParameter
            The parameter which will be accessed.
        @param value:
            The value of the parameter to be written to the device.
        @raise: serial.SerialException, ValueError
            In case of issues with serial communication.
        @raise: DeviceError
            Specific device-dependent errors.
        """
        try:
            with self._serial_thread_lock:
                try:
                    self.parse_error(self.flush_buffer_in(), parameter)
                    command = self._command_write(parameter, value)
                    self._serial_iface.write(command.encode("ascii"))
                    if parameter.write_acknowledge:
                        self._receive_acknowledge(parameter)
                except (serial.SerialException, ValueError):
                    error = DeviceCommunicationError(
                        f"Failed to write variable '{parameter.name}'.", device=self
                    )
                    self.log_exception(error)
                    raise error
        finally:
            self._verify_error_register()

    def _setup_ignore_timeout(self, parameter: ArduinoParameter, value: bool) -> None:
        """
        Generic handler for parameter_ignore_timeout setup options.

        @param parameter: ArduinoParameter
            The parameter affected.
        @param value: bool
            The value to be passed to the ignore option.
        """
        parameter.ignore_timeout_warning = value
        if value:
            self.log(f"Ignoring timeout on '{parameter}'.", level="warning")

    def _setup_ignore_acknowledge(
        self, parameter: ArduinoParameter, value: bool
    ) -> None:
        """
        Generic handler for parameter_ignore_badack setup options.

        @param parameter: ArduinoParameter
            The parameter affected.
        @param value: bool
            The value to be passed to the ignore option.
        """
        parameter.ignore_acknowledge_warning = value
        if value:
            self.log(
                f"Ignoring bad acknowledge values on '{parameter}'.", level="warning"
            )

    def _generate_setup_options(self, parameter: ArduinoParameter) -> None:
        """
        Generate and add standard setup options for a parameter.
        This is automatically called by add_parameter().

        @param parameter: DeviceParameter
            The parameter for which the setup options should be generated.
        """
        if parameter.acknowledge_value is not None:

            def handler_timeout(value: bool):
                self._setup_ignore_timeout(parameter, value)

            ignore_timeout = DeviceSetupOption(
                name=parameter.name + "_ignore_timeout", handler=handler_timeout
            )
            ignore_timeout.value_type = bool
            ignore_timeout.description = (
                f"By default, timing out on parameter {parameter.name} raises an exception. "
                f"If this option is set to True, only a warning is issued."
            )
            self.add_setup_option(ignore_timeout)

            def handler_badack(value: bool):
                self._setup_ignore_acknowledge(parameter, value)

            ignore_badack = DeviceSetupOption(
                name=parameter.name + "_ignore_badack", handler=handler_badack
            )
            ignore_badack.value_type = bool
            ignore_badack.description = (
                f"By default, receiving a bad acknowledge value from {parameter.name} raises an exception. "
                f"If this option is set to True, only a warning is issued."
            )
            self.add_setup_option(ignore_badack)

    def setup_ignore_unexpected_data(self, value: bool) -> None:
        """
        Handler for ignore_unexpected_data setup option.

        @param value: bool
            The value to assign.
        """
        self.ignore_unexpected_data_warning = value
        if value:
            self.log(f"Ignoring unexpected data.", level="warning")

    @staticmethod
    def arduino_uno_pin_number(pin_name: str) -> int | None:
        """
        Convert pin names for the arduino UNO board into pin numbers.

        @param pin_name: str
            The name of the pin on the board, e.g. D13 or A0.
        @return: int | None
            The pin number [0-14] or None if not recognized.
        """
        pin_name = pin_name.upper()
        pattern = re.compile(r"(?P<letters>AN|A|D)?(?P<number>\d+)")
        match = pattern.match(pin_name)
        if match is None:
            return None
        letters = match.group("letters")
        if letters is None or letters == "D":
            number = int(match.group("number"))
        elif letters == "A" or letters == "AN":
            number = 14 + int(match.group("number"))
        else:
            return None
        if number > 19 or number < 0:
            return None
        return number
