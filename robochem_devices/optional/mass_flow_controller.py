"""
File: mass_flow_controller.py
Author: Simone Pilon - Noël Research Group - 2023
GitHub: https://github.com/simone16

Description: Interface to Bronkhorst mass flow controller device (via serial-through-USB).
"""

import propar
from threading import Lock
from ..device import (
    BaseDevice,
    DeviceParameter,
    ParameterAccess,
    ParameterCachingPolicy,
)


class MassFlowController(BaseDevice):
    """
    Handles communication with Bronkhorst mass flow controller devices.
    Uses the Bronkhorst-propar library, wrapping the instrument class to implement the BaseDevice interface.

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
    """

    def __init__(self):
        BaseDevice.__init__(self)

        self.generic_name = "Mass Flow Controller"

        self._instrument = None
        # Threading lock required to access the serial interface.
        self._thread_lock = Lock()

        parameter = DeviceParameter(
            name="flow",
            access_level=ParameterAccess.R,
            value_type=float,
            internal_id=205,
        )
        parameter.description = "Flow-rate measured"
        parameter.units = " mLn/min"
        parameter.max_value = 100
        parameter.min_value = 0
        self.add_parameter(parameter)
        self._parameter_flow = parameter

        parameter = DeviceParameter(
            name="flow_setpoint",
            access_level=ParameterAccess.RW,
            value_type=float,
            internal_id=206,
        )
        parameter.description = "Flow-rate setpoint"
        parameter.units = " mLn/min"
        parameter.max_value = 100
        parameter.min_value = 0
        self.add_parameter(parameter)
        self._parameter_setpoint = parameter

        parameter = DeviceParameter(
            name="flow_units",
            access_level=ParameterAccess.R,
            value_type=str,
            internal_id=129,
        )
        parameter.description = "Flow-rate units"
        parameter.caching_policy = ParameterCachingPolicy.TRY
        self.add_parameter(parameter)

        parameter = DeviceParameter(
            name="flow_raw",
            access_level=ParameterAccess.R,
            value_type=int,
            internal_id=8,
        )
        parameter.description = "Flow-rate raw measured value [0-32000]"
        parameter.units = " mLn/min"
        parameter.max_value = 32000
        parameter.min_value = 0
        self.add_parameter(parameter)

        parameter = DeviceParameter(
            name="fluid_name",
            access_level=ParameterAccess.R,
            value_type=str,
            internal_id=25,
        )
        parameter.description = "Name of configured fluid"
        parameter.caching_policy = ParameterCachingPolicy.TRY
        self.add_parameter(parameter)

        parameter = DeviceParameter(
            name="temperature",
            access_level=ParameterAccess.R,
            value_type=float,
            internal_id=181,
        )
        parameter.description = "Fluid temperature measured"
        parameter.units = "°C"
        self.add_parameter(parameter)

        parameter = DeviceParameter(
            name="tag", access_level=ParameterAccess.RW, value_type=str, internal_id=115
        )
        parameter.description = "User defined instrument tag"
        parameter.caching_policy = ParameterCachingPolicy.TRY
        self.add_parameter(parameter)

        parameter = DeviceParameter(
            name="flow_min",
            access_level=ParameterAccess.R,
            value_type=float,
            internal_id=183,
        )
        parameter.description = "Minimum flow-rate"
        parameter.units = " mLn/min"
        parameter.caching_policy = ParameterCachingPolicy.TRY
        self.add_parameter(parameter)

        parameter = DeviceParameter(
            name="flow_max",
            access_level=ParameterAccess.R,
            value_type=float,
            internal_id=21,
        )
        parameter.description = "Maximum flow-rate"
        parameter.units = " mLn/min"
        parameter.caching_policy = ParameterCachingPolicy.TRY
        self.add_parameter(parameter)

    def open(self, comport: str):
        """
        Connects to the device. The connection must open before data can be sent or received.

        @param comport: str
            Identifier of the serial port to which the device is connected.
        """
        try:
            with self._thread_lock:
                self._instrument = propar.instrument(comport)
            fluid = self.__getitem__("fluid_name").strip()
            if self.specific_name == "":
                self.specific_name = fluid
            else:
                self.specific_name += f" - {fluid}"
            self._parameter_flow.max_value = self.__getitem__("flow_max")
            self._parameter_flow.min_value = self.__getitem__("flow_min")
            self._parameter_setpoint.max_value = self._parameter_flow.max_value
            self._parameter_setpoint.min_value = self._parameter_flow.min_value
            self.log(f"Connected on port '{comport}'.", level="ok")
        except AttributeError as e:
            self._instrument = None
            self.log(str(e), decorate=False)
            self.log(f"Failed to connect on port '{comport}'.", level="error")

    def is_open(self) -> bool:
        """
        Check whether the connection is open.

        @return: bool
            True if the connection is open.
        """
        return bool(self._instrument)

    def close(self):
        """Disconnects the device."""
        with self._thread_lock:
            self._instrument = None
        self.log("Disconnected.", level="ok")

    def _read(self, parameter: DeviceParameter):
        """
        Low-level function to read data from the device.

        @param parameter: DeviceParameter
            The parameter which will be accessed.
        @return:
            The value of the parameter read from the device.
        """
        with self._thread_lock:
            return self._instrument.readParameter(parameter.internal_id)

    def _write(self, parameter: DeviceParameter, value):
        """
        Low-level function to write data to the device.

        @param parameter: DeviceParameter
            The parameter which will be accessed.
        @param value:
            The value of the parameter to be written to the device.
        """
        with self._thread_lock:
            self._instrument.writeParameter(parameter.internal_id, value)
