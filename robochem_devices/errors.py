"""
File: errors.py
Author: Simone Pilon - Noël Research Group - 2023
GitHub: https://github.com/simone16

Description: Custom Exceptions for the devices module.
"""


class DeviceNotImplemented(Exception):
    """Attempt to use a device which is not implemented."""


class DeviceError(Exception):
    """
    Base class for device-related errors.
    It stores a reference to the device object that raised the exception.
    """

    device: "BaseDevice"

    def __init__(self, *args, device: "BaseDevice" = None):
        """
        Constructor.
        A reference to the device class should be provided as keyword argument.
        """
        super(Exception, self).__init__(*args)
        self.device = device

    def __str__(self) -> str:
        """A representation of the error, including the originating device."""
        return (
            super(Exception, self).__str__()
            + f"\nOriginated from device '{self.device}'."
        )


class DeviceNotOpenError(DeviceError):
    """Attempt to access device before a connection was open."""


class DeviceNotInitializedError(DeviceError):
    """Attempt to access device before it was initialized."""


class DeviceCommunicationError(DeviceError):
    """Error occurred during communication with the device."""


class InvalidResponseError(DeviceCommunicationError):
    """An invalid response was received from the device."""


class NoResponseError(DeviceCommunicationError):
    """No response was received from the device."""


class FailedWriteError(DeviceCommunicationError):
    """Failed to write the command to the device."""


class UnrecognizedParameterName(DeviceError):
    """Attempt to access a parameter that is not implemented by the device."""


class OperationNotSupported(DeviceError):
    """Request to perform an operation that the device does not support."""


class DeviceTimeoutError(DeviceError):
    """
    A device operation did not complete within the expected time.
    Prefer ParameterTimeoutError for specific parameters timeout, use this only if no relevant parameter is available.
    """


class HPLCError(DeviceError):
    """HPLC operation did not proceed as planned."""


class ValveError(DeviceError):
    """Valve operation did not proceed as planned."""


class MotorError(DeviceError):
    """Motor operation did not proceed as planned."""


class SensorError(DeviceError):
    """A sensor is not responding as expected."""


class GpioError(DeviceError):
    """GPIO related error."""


class ParameterError(DeviceError):
    """Base class for errors related to parameter access on a device."""

    def __init__(
        self,
        *args,
        device: "BaseDevice" = None,
        parameter: "DeviceParameter" = None,
    ):
        """
        Constructor.
        A reference to the class should be provided as first argument.
        """
        DeviceError.__init__(self, *args, device=device)
        self.parameter = parameter

    def __str__(self) -> str:
        """A representation of the error, including the originating device."""
        return (
            DeviceError.__str__(self)
            + f"\nWhile accessing parameter: '{self.parameter.name}'."
        )


class ParameterAccessError(ParameterError):
    """Attempt to access a parameter with incorrect access policy."""


class ParameterDisabledError(ParameterError):
    """Attempt to access a parameter that is currently disabled."""


class ParameterValueError(ParameterError):
    """Attempt to set an invalid value for a device parameter."""

    def __str__(self) -> str:
        """A representation of the error, including the originating device."""
        valid_values = self.parameter.describe_valid_values()
        if "\n" in valid_values:
            valid_values = "\n" + valid_values
        return ParameterError.__str__(self) + f"\nValid values: {valid_values}"


class ParameterParsingError(ParameterError):
    """Received invalid data for a specific parameter value."""


class ParameterCommError(ParameterError):
    """Received unexpected data during a parameter transaction."""


class ParameterTimeoutError(ParameterError):
    """Parameter did not receive acknowledge within the expected time."""


class ParameterAcknowledgeError(ParameterError):
    """Parameter received invalid acknowledge."""


class ParameterCannotBeStoppedError(ParameterError):
    """Attempt to asynchronously stop a parameter which does not offer this function."""


class ParameterNotRunningError(ParameterError):
    """Attempt to asynchronously stop a parameter while the parameter is not running."""


class SoftLimitError(ParameterError):
    """
    Grblr controller issued a soft limit alarm during the execution of this parameter.
    Send a reset command to be able to resume operations.
    """


class HardLimitError(ParameterError):
    """
    Grblr controller issued a hard limit alarm during the execution of this parameter.
    Send a reset command to be able to resume operations.
    This error indicates that the machine was not where it expected to be.
    """


class AlarmLockError(ParameterError):
    """
    Grblr controller is locked due to an alarm and cannot perform the requested operation.
    """


class ForbiddenMoveError(ParameterError):
    """Parameter requested to move in a manner that is not allowed."""
