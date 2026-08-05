"""
File: device.py
Author: Simone Pilon - Noël Research Group - 2023
GitHub: https://github.com/simone16

Description: Each physical device within the automation platform should be controlled via an object.
    Here the base interface of all devices is defined. All device classes should inherit from BaseDevice and every
    part of the platform code can expect to control any device via the same common interface.
"""

from typing import Any, Callable, ItemsView
from enum import Enum
from bidict import bidict
from threading import Event

from .logger import Logger
from .errors import (
    DeviceError,
    ParameterAccessError,
    ParameterDisabledError,
    ParameterValueError,
    DeviceNotOpenError,
    DeviceNotInitializedError,
    UnrecognizedParameterName,
)


class ParameterAccess(Enum):
    """
    Defines the available access levels for each parameter of a device.

    R : read only
    W : write only
    RW : read and write
    """

    R = 1
    W = 2
    RW = 3


class ParameterCachingPolicy(Enum):
    """
    Defines the available caching policies for each parameter of the device.

    NEVER : never cache, always read parameter from device.
    TRY : parameter is read for first access, then cached value is used.
    ALWAYS : the parameter is never actually read from the device as is. (e.g.: is read upon initialization only).
    """

    NEVER = 1
    TRY = 2
    ALWAYS = 3


class ParameterValue:
    """Base class for the value a parameter can have. Subclasses should be implemented for each parameter."""

    value: Any

    def __init__(self, value: Any = None):
        if value is not None and isinstance(value, type(self)):
            self.value = value.value
        else:
            self.value = None

    def __str__(self) -> str:
        """
        Give a string description of the object.

        @return: str
            A string description.
        """
        return str(self.value)

    def __eq__(self, other: Any) -> bool:
        """
        Compares a ParameterValue object to a value or another ParameterValue object.

        @return: bool
            Returns True if the two values are equivalent.
        """
        if not isinstance(other, type(self)):
            other = type(self)(other)
        return self.value == other.value

    def __ne__(self, other: Any) -> bool:
        """
        Compares a ParameterValue object to a value or another ParameterValue object.

        @return: bool
            Returns True if the two values are not equivalent.
        """
        return not self.__eq__(other)

    def __lt__(self, other: Any) -> bool:
        """
        Compares a ParameterValue object to a value or another ParameterValue object.

        @return: bool
            Returns True if this value is less than the other.
        """
        if not isinstance(other, type(self)):
            other = type(self)(other)
        return self.value < other.value

    def __gt__(self, other: Any) -> bool:
        """
        Compares a ParameterValue object to a value or another ParameterValue object.

        @return: bool
            Returns True if this value is greater than the other.
        """
        if not isinstance(other, type(self)):
            other = type(self)(other)
        return self.value > other.value

    def __le__(self, other: Any) -> bool:
        """
        Compares a ParameterValue object to a value or another ParameterValue object.

        @return: bool
            Returns True if this value is less than or equal to the other.
        """
        if not isinstance(other, type(self)):
            other = type(self)(other)
        return self.value <= other.value

    def __ge__(self, other: Any) -> bool:
        """
        Compares a ParameterValue object to a value or another ParameterValue object.

        @return: bool
            Returns True if this value is greater than or equal to the other.
        """
        if not isinstance(other, type(self)):
            other = type(self)(other)
        return self.value >= other.value


class ParameterValueRun(ParameterValue):
    """Value for Parameters which trigger a command with no argument."""

    _keyword = "RUN"

    def __init__(self, value: str) -> None:
        """
        To prevent accidental triggering, a valid value must be created from the string 'run' (case-insensitive).

        @param value: str
            Set to 'run' to obtain a valid value.
        """
        ParameterValue.__init__(self, value=value)

        if self.value is None:
            if isinstance(value, str):
                if not value.upper() == self._keyword:
                    raise ValueError(
                        f"Invalid value '{value}' for {self.__class__.__name__}."
                    )
            else:
                raise TypeError(
                    f"Constructor argument must be 'str', not '{type(value)}'."
                )

    def __str__(self) -> str:
        """
        Give a string description of the object.

        @return: str
            A string description.
        """
        return self._keyword

    @classmethod
    def keyword(cls):
        return cls._keyword


class ParameterEnumValue(ParameterValue):
    """
    Base class for the value a parameter can have of Enum type. This is for values which can assume a discrete
    number of values, identified by strings. Subclasses should be implemented for each parameter.
    Note: string identifiers must be all-caps!
    """

    _raw_values: bidict[str, Any]
    _raw_values = bidict()

    def __init__(self, value: Any = None):
        ParameterValue.__init__(self, value=value)

        if value is None:
            return
        if self.value is None:
            if isinstance(value, str):
                try:
                    self.value = self._raw_values[value.upper()]
                    return
                except KeyError as e:
                    e.add_note(f"Valid values for {type(self)}: '{self.names()}'")
                    raise
            else:
                self.value = value
                if self.value not in self._raw_values.inverse.keys():
                    raise ValueError(f"Invalid value '{self.value}' for {type(self)}.")
                return

    @classmethod
    def names(cls) -> list[str]:
        """
        A list of valid names corresponding to values.

        @return: list[str]
            A list of string values which are valid identifiers.
        """
        return list(cls._raw_values.keys())

    @classmethod
    def items(cls) -> ItemsView[str, Any]:
        """
        Returns all valid name -> value pairs for this enum.

        @return: dict_items[str, Any]
        """
        return cls._raw_values.items()

    def __str__(self) -> str:
        return self._raw_values[self.value]


class DeviceParameter:
    """
    Holds information on a single parameter monitored or affected by a device.
    This class is just a container to store information about parameters.

    Attributes:
        name            Identifier used outside of device to access parameter.
        units           If the parameter has physical units, register that here (add leading space if needed).
        enabled         If false, access will be prevented (both read and write). True by default.
        access_level    What operations are possible with this parameter? See enum ParameterAccess.
        value_type      The type of the parameter values.
        max_value       maximum value possible for the parameter (only for numeric values, self.values must be None).
        min_value       minimum value possible for the parameter (only for numeric values, self.values must be None).
        caching_policy  Defines whether the last known value should be used when reading a parameter.
                        by default is set to NEVER, and the value is always read from the device.
        last_known_value    Can store the parameter value to allow access without reading from the device.
        description     long description of the parameter.

        stop            Set this event to prevent this parameter from being written. Supports
                        asynchronous stopping from different threads. **It needs to be set and cleared**.
        internal_id     identifier used internally by the device class to access the parameter.

    Note: keep in mind that more attributes can be added at runtime, if needed.
    """

    name: str
    units: str
    enabled: bool
    access_level: ParameterAccess
    value_type: type
    max_value: int | float | ParameterValue | None
    min_value: int | float | ParameterValue | None
    caching_policy: ParameterCachingPolicy
    last_known_value: int | float | None
    description: str
    stop: Event
    internal_id: Any

    def __init__(
        self,
        name: str,
        access_level: ParameterAccess,
        value_type: type,
        internal_id: Any,
    ) -> None:
        self.name = name
        self.units = ""
        self.enabled = True
        self.access_level = access_level
        self.value_type = value_type
        self.max_value = None
        self.min_value = None
        self.caching_policy = ParameterCachingPolicy.NEVER
        self.last_known_value = None
        self.description = ""

        self.stop = Event()
        self.internal_id = internal_id

    def validate(self, value: Any) -> tuple[Any, bool]:
        """
        Take a parameter value and gives a valid raw value or None if the value is invalid for this parameter.
        Some parameters can take string values which are translated to raw values by parameter.values.

        @param value: Any
            The value to validate for this parameter.
        @return: tuple[Any, bool]
            A validated value which can be fed to device._read and a boolean which is true if the input value
            was valid.
        """
        if isinstance(value, int) and self.value_type is float:
            value = float(value)
        if not isinstance(value, self.value_type):
            try:
                value = self.value_type(value)
            except (ValueError, TypeError):
                return value, False
        if self.min_value is not None:
            if value < self.min_value:
                return value, False
        if self.max_value is not None:
            if value > self.max_value:
                return value, False
        return value, True

    def to_string(self, value: Any) -> str:
        """
        Return a human-readable version of the parameter with the given value. Adds units and translates numbers
        to strings.

        @param value: Any
            The value of this parameter.
        @return: str
            A string representing the value.
        """
        return str(value) + self.units

    def describe_valid_values(self) -> str:
        """
        Human-readable description of which values are supported for this parameter.

        @return: str
            A description of the valid values range.
        """
        description = ""
        if issubclass(self.value_type, ParameterEnumValue):
            for name, value in self.value_type.items():
                description += f"- {name} -> {value} {self.units}\n"
        else:
            if self.min_value is not None or self.max_value is not None:
                if self.min_value is not None:
                    description += f"{self.min_value} <= "
                description += "value"
                if self.max_value is not None:
                    description += f" <= {self.max_value}"
                description += " " + self.units + "\n"
        return description

    def document(self):
        """
        Detailed description of this parameter and its usage in Markdown format.

        @return: str
            A detailed description of this parameter.
        """
        access = "READ/WRITE"
        if self.access_level == ParameterAccess.R:
            access = "READ ONLY"
        elif self.access_level == ParameterAccess.W:
            access = "WRITE ONLY"
        description = f"| **{self.name}** | {access} | {self.value_type.__name__} | "
        if self.description == "":
            description += "| "
        else:
            description += self.description + "| "
        valid_values = self.describe_valid_values()
        if valid_values == "":
            description += "| "
        else:
            one_line = ""
            for line in valid_values.splitlines():
                one_line += line + ", "
            description += one_line + "| "
        return description

    def __str__(self):
        return self.name


class DeviceSetupOption:
    """
    An option which can be set for a device.
    Optional features can be toggled and information can be added to a device.
    This is done by the device setup() method (see BaseDevice for more info).
    Setup options are typically defined in configuration files.

    Attributes:
        name            Identifier used for the option.
        description     A longer string describing and documenting the parameter.
        required        True if the option should always be specified.
        value_type      The type of the option value (e.g.: str, dict, ...).
        handler         A callable which takes the option value as argument and perform the setup operation.
    """

    name: str
    description: str
    required: bool
    value_type: type | None
    handler: Callable

    def __init__(self, name: str, handler: Callable):
        """
        Create a setup option object.

        @param name: str
            The name used to identify the option.
        """
        self.name = name
        self.description = ""
        self.required = False
        self.value_type = None
        self.handler = handler

    def document(self):
        """
        Detailed description of this option and its usage in Markdown format.

        @return: str
            A detailed description of this setup option.
        """
        type_name = ""
        if isinstance(self.value_type, tuple):
            for t in self.value_type:
                type_name += t.__name__ + " |"
            type_name = type_name[:-2]
        else:
            type_name = self.value_type.__name__
        description = f"| **{self.name}** | {self.required} | {type_name} | "
        description += self.description + " |"
        return description


class BaseDevice:
    """
    A generic parent class which defines functions and variables that the platform code expects to be
    defined for every device. To ensure that a class controlling a device can be integrated correctly,
    inherit from this and overwrite all non-implemented members and functions.

    Subclassing:
        When creating a child class of this, proper methods to read and write parameters must be implemented.
        A parameter should hold information required to access said parameter via the implemented communication
        protocol, while several functions are used to perform the read and write operation (note: devices must implement
        their own communication protocol):

        Read call stack:
            1. __getitem__ is called by the user with a parameter name. You most likely do not need to overwrite this
                function. If the parameter is cached, the function returns the value immediately.
            2. _read is called with a valid parameter. This needs to be implemented by the user.
            3. _extract is called on the value returned by _read. By default this converts the _read result to the type
                expected by the parameter, but it can be overridden. This value is returned by __getitem__
        Write call stack:
            1. __setitem__ is called by the user with a parameter name and a value. This is also unlikely to need
                overriding.
            2. _write is called with a valid parameter and value, of the type expected by the parameter. This needs to
                be implemented by the user.

    Adding device parameters:
        A parameter is value that can be transmitted between the control script and the device.
        Every subclass of this should add Parameters to self._parameters in __init__(self).
        The parameters hold all information that _read and _write need to query the device.

    Adding device setup options:
        Setup options are issued after the device is open and can define default behaviour or specify which components
        of the device should be enabled.
        Setup options should be added in __init__(self) by calling self.add_setup_option(), providing a Callable which
        handles setting up said option. For example, a class method can be used to set up an option.

    Attributes:
        generic_name    Human readable name for the device type e.g.: Temperature controller.
        specific_name   Identifier for a specific device, useful if more of the same type are connected.
        complete_name   A long name with both device generic and specific names.
        tags            A list of user-defined str tags.
        log_to_console  If False, device will send messages to logfiles only, not to the console.

        _parameters     Dict of name -> Parameter for supported parameters.
        _options        Dict of name -> Callable for supported setup options.
        _initialized    Keeps track of whether the device was initialized.

        _parameter_type The type of the parameters.

    Usage Guidelines:
        It is expected that all devices follow the same principles regarding specific usage and preparation. For each
        device, the following functions will be called in the given order:

        1. Constructor
            Prepare parameters and setup options. Do not attempt to connect.
        2. Open
            Establish a connection to the physical device.
            After a successful connection, it is possible to read and write some parameters within the open() function,
            if these are deemed fundamental for correct operation, but this should be avoided if possible.
            Actuating any part of the device here is discouraged.
        3. Setup
            Optional, but recommended step. Here it is possible to write some parameter values which are specific to
            the device and not the device-type. This is used for configuration file options.
            Actuating any part of the device is discouraged here as well.
            Setup options can be 'required', giving a warning if left unset.
        4. Initialize
            This is the final step before the device can be used. Device movement, actuation and any time-intensive
            preparation procedure should be performed at this stage.

        Attempting to access a device before open() and initialize() are called will raise an error.
        Note: the steps above are different for proxy devices. These are created during the setup() phase of the parent,
        hence can expect the connection to be open immediately upon construction.
        Setup should not be called on Proxy devices.
    """

    generic_name: str
    specific_name: str
    tags: list
    log_to_console: bool

    _parameters: dict
    _initialized: bool

    _parameter_type = DeviceParameter

    def __init__(self) -> None:
        self.generic_name = ""
        self.specific_name = ""
        self.tags = []
        self.log_to_console = True

        self._parameters = {}
        self._options = {}
        self._initialized = False

    @property
    def keep_open(self) -> bool:
        """
        Expresses a preference for opening the device for each operation, rather that opening at platform build-time
        and keeping it open (default).

        @return: bool
            True if the device should be kept in the open state when not in use.
        """
        return True

    def prepare_for_close(self) -> None:
        """
        Ensure the device is in a safe state before closing the connection (and losing control).
        """
        pass

    def __del__(self) -> None:
        if self.is_open():
            self.close()

    def add_parameter(
        self, parameter: DeviceParameter, ignore_shared_id: bool = False
    ) -> None:
        """
        Add parameters and checks them.
        Do not add parameters directly to self._parameters, use this instead.

        @param parameter: DeviceParameter
            The parameter which needs to be added.
        @param ignore_shared_id: bool
            If True, the warning for duplicate internal id is not shown when adding the parameter.
            Only set this if you know what you are doing.
        """
        if not isinstance(parameter, self._parameter_type):
            error = TypeError(
                f"Invalid type '{type(parameter)}' for parameter of '{type(self)}'."
            )
            error.add_note(self.exception_note())
            self.log_exception(error)
            raise error
        if parameter.name in self._parameters.keys():
            self.log(
                f"Parameter '{parameter.name}' overrides an existing parameter.",
                level="warning",
            )
        if not ignore_shared_id:
            for other_parameter in self._parameters.values():
                if other_parameter.internal_id == parameter.internal_id:
                    self.log(
                        f"Parameter '{parameter.name}' shares internal id with '{other_parameter.name}'.",
                        level="warning",
                    )
        self._parameters[parameter.name] = parameter
        self._generate_setup_options(parameter)

    def open(self, **kwargs) -> None:
        """
        Connects to the device. The connection must open before data can be sent or received.
        Note: needs to overridden in subclass!

        @param comport: str
            Identifier of the serial port to which the device is connected.
        """
        raise NotImplementedError()

    def _open(self, **kwargs) -> None:
        """
        Connects to the device. The connection must open before data can be sent or received.
        This the low-level implementation.
        Note: needs to overridden in subclass!

        @param comport: str
            Identifier of the serial port to which the device is connected.
        """
        raise NotImplementedError()

    def reopen(self):
        """
        Connects to the device after the connection was made a first time.
        Does not require to re-input the connection data.
        """
        raise NotImplementedError()

    def is_open(self) -> bool:
        """
        Check whether the connection is open.
        Note: needs to overridden in subclass!

        @return: bool
            True if the connection is open.
        """
        raise NotImplementedError()

    def close(self) -> None:
        """
        Disconnects the device.
        Note: needs to overridden in subclass!
        """
        raise NotImplementedError()

    def emergency_close(self) -> None:
        """
        Disconnect the device in an emergency situation.
        This is expected to stop any operation asynchronously.
        """
        self.close()

    def add_setup_option(self, option: DeviceSetupOption) -> None:
        """
        Add a setup option.

        @param option: DeviceSetupOption
            The setup option to add.
        """
        if option.name in self._options.keys():
            self.log(
                f"Setup option '{option.name}' overrides an existing option.",
                level="warning",
            )
        self._options[option.name] = option

    def _generate_setup_options(self, parameter: DeviceParameter) -> None:
        """
        Generate and add standard setup options for a parameter.
        This is automatically called by add_parameter().

        @param parameter: DeviceParameter
            The parameter for which the setup options should be generated.
        """
        pass

    def setup(self, **kwargs) -> None:
        """
        Setup device parameters. This should be called after open() to set-up some initial conditions of the
        device.

        @param kwargs:
           See self.setup_options for available arguments.
        """
        # Allow calling device parameters as if device was already initialized (suppress warnings).
        initialized = self._initialized
        self._initialized = True
        try:
            for name, value in kwargs.items():
                if name in self._options.keys():
                    allowed_types = self._options[name].value_type
                    if not isinstance(value, allowed_types):
                        error = TypeError(
                            f"Setup option '{name}' must be of type '{allowed_types}', but '{type(value)}' found."
                        )
                        error.add_note(self.exception_note())
                        self.log_exception(error)
                        raise error
                    self._options[name].handler(value)
                else:
                    self.log(
                        f"Setup option '{name}' is not supported.", level="warning"
                    )
            for name, option in self._options.items():
                if option.required and name not in kwargs.keys():
                    self.log(
                        f"Required setup option '{name}' not specified.",
                        level="warning",
                    )
        finally:
            # Restore the initial initialized status
            self._initialized = initialized

    def initialize(self) -> None:
        """
        Prepare the device for operation.
        Time intensive operations (movement, zeroing, syncing) are performed here.
        Every subclass should implement this method differently.
        """
        if self.is_open():
            self._initialized = True
        else:
            raise DeviceNotOpenError(
                "Cannot initialize a device if the connection is not open."
            )

    def is_initialized(self) -> bool:
        """
        The device initialization status.
        A device is ready when it is both connected and initialized, meaning that it can perform
        standard operations.

        @return: bool
            True if the device is ready for operation.
        """
        return self._initialized

    def stop_parameter(self, parameter_name: str, stop: bool = True) -> None:
        """
        Prevents a parameter from being written. Can support asynchronous operation.
        Note: if a parameter is stopped, it needs to be reactivated explicitly.

        @param parameter_name: str
            The name of the parameter as used to initiate it.
        @param stop: bool
            Sets whether the parameter should be stopped or allowed.
        @return: None
        """
        event = self.parameter_by_name(parameter_name).stop
        if stop:
            event.set()
        else:
            event.clear()

    def is_parameter_stopped(self, parameter_name: str) -> bool:
        """
        Returns the stop status of a parameter.

        @param parameter_name: str
            The name of the parameter as used to initiate it.
        @return: bool
            True if the parameter is (or was) stopped.
        """
        return self.parameter_by_name(parameter_name).stop.is_set()

    def _read(self, parameter: DeviceParameter) -> Any:
        """
        Low-level function to read data from the device.

        @param parameter: DeviceParameter
            The parameter which will be accessed.
        @return:
            The value of the parameter read from the device.

        Note: needs to be overridden in subclass!
        """
        raise NotImplementedError()

    def _write(self, parameter: DeviceParameter, value: Any) -> None:
        """
        Low-level function to write data to the device.

        @param parameter: DeviceParameter
            The parameter which will be accessed.
        @param value:
            The value of the parameter to be written to the device.

        Note: needs to be overridden in subclass!
        """
        raise NotImplementedError()

    def _extract(self, parameter: DeviceParameter, response: str) -> Any:
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
        return parameter.value_type(response)

    def _check_device_parameter(self, name: str, write: bool) -> DeviceParameter:
        """
        Checks performed before reading or writing parameters.

        @param name: str
            The name of the parameter.
        @param write: bool
            If true checks are performed for a write operation, else for read.
        @return: DeviceParameter
            If found, the required device parameter is returned.
        @raise: KeyError
            If the name does not correspond to a parameter.
        @raise: ParameterAccessError
            If the parameter access policies do not allow the requested operation.

        """
        if write:
            verb = "write"
            non_verb = "read"
            wrong_access_level = ParameterAccess.R
        else:
            verb = "read"
            non_verb = "write"
            wrong_access_level = ParameterAccess.W
        if self.keep_open and not self.is_open():
            error = DeviceNotOpenError(
                f"Failed to {verb} '{name}': device is not open.", device=self
            )
            self.log_exception(error)
            raise error
        if write and not self.is_initialized():
            error = DeviceNotInitializedError(
                f"Failed to {verb} '{name}': device is not initialized.", device=self
            )
            self.log_exception(error)
            raise error
        try:
            parameter = self.parameter_by_name(name)
        except UnrecognizedParameterName:
            error = UnrecognizedParameterName(
                f"Failed to {verb}: unknown parameter '{name}'.", device=self
            )
            self.log_exception(error)
            raise error
        if not parameter.enabled:
            error = ParameterDisabledError(
                f"Failed to {verb}: parameter '{name}' is currently disabled.",
                device=self,
                parameter=parameter,
            )
            self.log_exception(error)
            raise error
        if parameter.access_level == wrong_access_level:
            error = ParameterAccessError(
                f"Failed to {verb}: parameter '{name}' is {non_verb}-only.",
                device=self,
                parameter=parameter,
            )
            self.log_exception(error)
            raise error
        return parameter

    def __getitem__(self, name: str) -> Any:
        """
        Read a parameter from the device.

        @param name: str
            The name of the parameter.
            For a list of names, call device.keys()
            For a list of parameters, call device.parameters()

        @return:
            The value of the parameter.

        @raise: KeyError
            If the parameter name is not known.
        @raise: ValueError
            If the parameter is write-only.
        @raise: DeviceNotOpenError
            If the device is not open.
        @raise: DeviceNotReadyError
            If the device is not initialized.

        """
        self._log_parameter_start(f"Reading '{name}'.")
        parameter = self._check_device_parameter(name=name, write=False)
        if not parameter.caching_policy == ParameterCachingPolicy.NEVER:
            if parameter.last_known_value is not None:
                self._log_parameter_success(
                    f"Read '{name}' <- {parameter.to_string(parameter.last_known_value)} (cached)",
                    level="ok",
                    indent_symbol="last",
                )
                return parameter.last_known_value
            elif parameter.caching_policy == ParameterCachingPolicy.ALWAYS:
                error = ParameterAccessError(
                    f"Failed to read: '{name}' has no cached value, but caching policy is 'always'.",
                    device=self,
                    parameter=parameter,
                )
                self.log_exception(error)
                raise error
        value = self._extract(parameter, self._read(parameter))
        parameter.last_known_value = value
        self._log_parameter_success(f"Read '{name}' <- {parameter.to_string(value)}")
        return value

    def __setitem__(self, name: str, value: Any) -> None:
        """
        Write a parameter to the device.

        :name: str
            The name of the parameter.
            For a list of names, call device.keys()
            For a list of parameters, call device.parameters()
        @param value:
            The value for the parameter.

        @raise: KeyError
            If the parameter name is not known.
        @raise: ValueError
            If the parameter is read-only or the value is not the correct type.
        @raise: DeviceNotOpenError
            If the device is not open.
        @raise: DeviceNotReadyError
            If the device is not initialized.

        """
        self._log_parameter_start(f"Writing '{name}'.")
        parameter = self._check_device_parameter(name=name, write=True)
        value, valid = parameter.validate(value)
        if parameter.stop.is_set():
            self._log_parameter_success(
                f"Stopped '{name}' -> {parameter.to_string(value)}"
            )
        if not valid:
            error = ParameterValueError(
                f"Failed to write: invalid value '{value}'.",
                device=self,
                parameter=parameter,
            )
            self.log_exception(error)
            raise error
        self.log(f"Validated value {parameter.to_string(value)}")
        self._write(parameter, value)
        parameter.last_known_value = value
        self._log_parameter_success(f"Written '{name}' -> {parameter.to_string(value)}")

    def keys(self) -> list:
        """
        All available parameter names.

        @return: list of str
            A list of parameter names.
        """
        return [parameter.name for parameter in self._parameters]

    def parameters(self) -> list:
        """
        List all available parameters for this device.

        @return: list of DeviceParameter
            All parameters and relevant information for this device.
        """
        return list(self._parameters.values())

    def parameter_by_name(self, name: str) -> DeviceParameter:
        """
        Find parameter within device based on name.

        @return: DeviceParameter
            The parameter stored in this device with the corresponding name.
        @raise: UnrecognizedParameterName
            If the name does not correspond to a parameter.
        """
        try:
            return self._parameters[name]
        except KeyError:
            raise UnrecognizedParameterName(
                f"Parameter '{name}' is not supported by this device.", device=self
            )

    def _parameter_by_id(self, custom_id: Any) -> DeviceParameter | None:
        """
        Find parameter within device based on its custom id.
        Note: there is no guarantee that custom_id are unique. In case of duplicates, only one element is
        returned.

        @return: DeviceParameter | None
            The parameter stored in this device with the corresponding custom_id.
        """
        for name, parameter in self._parameters.items():
            if parameter.internal_id == custom_id:
                return parameter
        return None

    def _clear_parameters(self) -> None:
        """Remove all parameters."""
        self._parameters = {}

    @property
    def complete_name(self) -> str:
        """
        Combination of generic and specific name.

        @return: str
            A long name with both device generic and specific names.
        """
        name = self.generic_name
        if not self.specific_name == "":
            name += f" ({self.specific_name})"
        return name

    def __str__(self) -> str:
        """
        Combination of generic and specific name.

        @return: str
            A long name with both device generic and specific names.
        """
        return self.complete_name

    def exception_note(self) -> str:
        """
        Generate a short message to attach to a generic Exception to provide info on where the exception occurred.
        Note: DeviceError exceptions do not need this.

        @return: str
            Short note for an exception.
        """
        return f"Originated from device '{self.complete_name}'."

    def usage(self) -> str:
        """
        Usage information for all parameters of the device in Markdown format.

        @return: str
            A long description of all parameters and setup options of the device.
        """
        description = f"# {self.generic_name} usage\n\n"
        if self.__doc__ is not None:
            description += self.__doc__
        description += "\n## Parameters:\n\n"
        description += "| Name | Access | Type | Description | Values |\n"
        description += "|------|--------|------|-------------|--------|\n"
        for name, parameter in self._parameters.items():
            description += parameter.document() + "\n"
        description += "\n## Setup options:\n"
        description += "| Name | Required | Type | Description |\n"
        description += "|------|----------|------|-------------|\n"
        for name, option in self._options.items():
            description += option.document() + "\n"
        return description

    def log(self, message, **kwargs) -> None:
        """
        Log a message pertaining to this device.
        Internally, a call to Logger.log_message() is made, so the same kwargs are supported.
        This defines kwargs to reflect the origin of the message.

        @param message: str
            The message to be given to the user.
        @param kwargs:
            See below.
        @keyword level: str
          Warning level of the message: influences how it is highlighted and where it is displayed.
          Accepted values:
            - 'error'
            - 'warning'
            - 'ok'
            - 'none'
          Default: 'none'
        @keyword indent: str
          Messages can be indented to appear to be related to a previous message,
          for example the start of a routine.
          These options can be specified:
            - 'enter' (Increase indentation level by one).
            - 'continue' (Use current indentation level).
            - 'exit' (Decrease indentation level by one).
            - 'reset' (Exit all indentation levels back to 0).
          Default: 'continue'
        @keyword decorate: bool
          If False the message will be printed without additional formatting/info.
          Default: True
        @keyword hide: bool
          If true, the message is logged to files only and not shown on console.
          Default: False
        @keyword slack: bool
          If true, the message is always sent to slack, if false, never.
          Default: Only error messages are sent to slack.
          Note: if Slack was not set up, this will be silently ignored.
        """
        origin = self.specific_name
        if origin == "":
            origin = self.generic_name
        kwargs["subfolder"] = "devices"
        if not self.log_to_console:
            kwargs["hide"] = True
        kwargs["priority"] = 0  # corresponds to 'show all' (highest verbosity)
        Logger.log_message(message, origin=origin, **kwargs)

    def _log_parameter_start(self, message, **kwargs) -> None:
        """
        Log a message of successful parameter operation completion.
        Internally, a call to Logger.log_message() is made, so the same kwargs are supported.

        @param message: str
            The message to be given to the user.
        @param kwargs:
            See self.log(). 'level' and 'indent_symbol' are overridden.
        """
        kwargs["indent"] = "enter"
        self.log(message, **kwargs)

    def _log_parameter_success(self, message, **kwargs) -> None:
        """
        Log a message of successful parameter operation completion.
        Internally, a call to Logger.log_message() is made, so the same kwargs are supported.

        @param message: str
            The message to be given to the user.
        @param kwargs:
            See self.log(). 'level' and 'indent_symbol' are overridden.
        """
        kwargs["level"] = "ok"
        kwargs["indent"] = "exit"
        self.log(message, **kwargs)

    def _log_parameter_fail(self, message: str | Exception, **kwargs) -> None:
        """
        Log a message of successful parameter operation completion.
        Internally, a call to Logger.log_message() is made, so the same kwargs are supported.

        @param message: str
            The message to be given to the user.
        @param kwargs:
            See self.log(). 'level' and 'indent_symbol' are overridden.
        """
        kwargs["level"] = "error"
        kwargs["indent"] = "reset"
        self.log(message, **kwargs)

    def log_exception(self, error: Exception) -> None:
        """
        Log an exception.
        Calls self.log() with consistent settings to show an exception. When possible, use this method to ensure
        exception calls are logged before the exception is raised.

        @param error: Exception
            The exception which will be logged.
        """
        if isinstance(error, DeviceError):
            if error.device is not self:
                self.log(
                    f"An exception originated from '{DeviceError.device}', but logged from '{self}' (follows).",
                    level="warning",
                )
        self._log_parameter_fail(error)
