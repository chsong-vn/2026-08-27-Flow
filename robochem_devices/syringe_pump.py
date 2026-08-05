"""
File: syringe_pump.py
Author: Simone Pilon - Noël Research Group - 2023
GitHub: https://github.com/simone16

Description: Interface to the syringe pumps developed by the Noël Research Group. In addition to the pump, this device
    can control a switch valve to direct the flow from and into the pump, as well as a second, auxiliary, switch valve.
    Within the liquid handler, the auxiliary switch valve is connected to the injection port.
"""

import copy
from typing import Any
from bidict import bidict
from .device_arduino import (
    ArduinoDevice,
    ArduinoParameter,
    ArduinoError,
    ArduinoValueOnOff,
    ArduinoValueEnum,
    ArduinoValueRun,
)
from .device import (
    ParameterAccess,
    ParameterCachingPolicy,
    DeviceSetupOption,
)
from .general import parse_with_units
from .errors import (
    ValveError,
    MotorError,
    ParameterError,
)


class SwitchValveSetpointValue(ArduinoValueEnum):
    """
    Describes possible values for the setpoint of the switchvalves ('ON' and 'OFF').
    The positions are marked on the Switchvalves: OFF connects the C (common) and the OFF channels.
    ON connects C to ON.
    """

    _raw_values = bidict({"OFF": 1, "ON": 0})

    def invert(self) -> None:
        """Switch to the opposite valve setpoint value."""
        if self.value == 0:
            self.value = 1
        elif self.value == 1:
            self.value = 0


class SwitchValvePositionValue(ArduinoValueEnum):
    """Describes possible values for the setpoint of the switchvalves ('ON' and 'OFF' and 'ERROR')."""

    _raw_values = bidict({"OFF": 1, "ON": 0, "ERROR": 2})


class PumpZeroValue(ArduinoValueEnum):
    """
    Describes possible values for zeroing syringe pumps (moving as far as possible):
    EMPTY: the syringe is emptied.
    FILL: the syringe is filled.
    IN_PLACE: the syringe is not moved, the volume counter is zeroed.
    """

    _raw_values = bidict({"EMPTY": 0, "FILL": 1, "IN_PLACE": 2})


class SyringePumpError(ArduinoError):
    """Syringe pump specific error parameter value."""

    _error_types = {
        0: "No error",
        1: "Communication error",
        2: "Valve error",
        3: "Motor error",
    }

    _error_values = {
        3: {
            0: "No errors occurred.",
            1: "Unexpected activation of positive direction endstop.",
            2: "Unexpected activation of negative direction endstop.",
            3: "Tried to set a step delay which is too low or too high.",
            4: "Attempt to move motor while driver is disabled.",
            5: "A blockage of the motor was detected.",
        }
    }

    _error_exceptions = {
        2: ValveError,
        3: MotorError,
    }

    _parameter_error_id = 1


class SyringePump(ArduinoDevice):
    """
    Handles communication with the syringe pumps developed within the Noël Research Group, including up to two
    switch-valves. The main switch valve should be connected to the syringe, connecting it to a reservoir or flow
    system. The auxiliary switch valve is used within the liquid handler to control injection.
    The device is documented in detail at https://github.com/Noel-Research-Group/liquid_handler_cnc .

    Note: If present, the main switch valve should connect to the syringe on the 'C' (common) position,
        to the flow system on the 'ON' position, and to a solvent reservoir on the 'OFF' position.
        Connecting the switchvalve in a different way can result in malfunction during the initialization procedure.


    Usage:
        First, connect to the device with
        `self.open('COM4')`

        # todo add setup and initialize to all devices instructions.

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

    @property
    def syringe_volume(self) -> float:
        """
        Volume of the syringe currently mounted on the pump.

        @return: float
            The volume of the syringe in uL.
        """
        return self._max_syringe_volume

    @syringe_volume.setter
    def syringe_volume(self, volume: float):
        """
        Set the volume of the syringe mounted on the pump.

        @param volume: float
            The volume of the syringe in uL.
        """
        if volume < 0.0 or volume > 200000.0:
            error = ValueError(f"Invalid syringe volume: {volume} uL")
            error.add_note(self.exception_note())
            self.log_exception(error)
            raise error
        self._max_syringe_volume = volume
        for parameter in self._parameters_volumes:
            parameter.max_value = volume
            parameter.min_value = -volume

    def __init__(self):
        ArduinoDevice.__init__(self)

        self.generic_name = "Syringe Pump"

        self._timeout_multiplier = (
            1.2  # Wait this many times the expected travel time before timing-out
        )

        self._switchvalve_reservoir = None
        self._switchvalve_system = None

        self._parameters_pump_movement = (
            []
        )  # Pumping parameters which can generate acks.
        self._parameters_volumes = []  # parameters which take a volume as value.
        self._parameters_valve = []  # parameters related to main valve
        self._parameters_aux_valve = []  # parameters related to aux valve
        self._parameters_acks = []  # ack enable parameters

        # This is on by default.
        parameter = ArduinoParameter(
            name="ack_pump",
            access_level=ParameterAccess.RW,
            value_type=ArduinoValueOnOff,
            internal_id=2,
        )
        parameter.description = (
            "Send acknowledge message after pump or zero move is complete"
        )
        parameter.caching_policy = ParameterCachingPolicy.TRY
        self._parameters_acks.append(parameter)
        self.add_parameter(parameter)
        self._parameter_ack_pump = parameter

        # This is on by default.
        parameter = ArduinoParameter(
            name="encoder",
            access_level=ParameterAccess.RW,
            value_type=ArduinoValueOnOff,
            internal_id=18,
        )
        parameter.description = "Enable encoder wheel to detect blockages"
        parameter.caching_policy = ParameterCachingPolicy.TRY
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="valve_setpoint",
            access_level=ParameterAccess.RW,
            value_type=SwitchValveSetpointValue,
            internal_id=3,
        )
        parameter.description = "Set switch-valve position"
        parameter.acknowledge_value = "v"
        parameter.acknowledge_timeout = 4.0
        parameter.write_acknowledge = True
        parameter.enabled = False
        self._parameters_valve.append(parameter)
        self.add_parameter(parameter)
        self._parameter_valve_move = parameter

        parameter = ArduinoParameter(
            name="valve_actual",
            access_level=ParameterAccess.R,
            value_type=SwitchValvePositionValue,
            internal_id=4,
        )
        parameter.description = "Switch-valve position read from sensor"
        parameter.enabled = False
        self._parameters_valve.append(parameter)
        self.add_parameter(parameter)

        # This is on by default.
        parameter = ArduinoParameter(
            name="ack_valve",
            access_level=ParameterAccess.RW,
            value_type=ArduinoValueOnOff,
            internal_id=19,
        )
        parameter.description = "Send acknowledge message after valve move is complete"
        parameter.caching_policy = ParameterCachingPolicy.TRY
        parameter.enabled = False
        self._parameters_acks.append(parameter)
        self._parameters_valve.append(parameter)
        self.add_parameter(parameter)
        self._parameter_ack_valve = parameter

        parameter = ArduinoParameter(
            name="aux_valve_setpoint",
            access_level=ParameterAccess.RW,
            value_type=SwitchValveSetpointValue,
            internal_id=16,
        )
        parameter.description = "Set auxiliary switch-valve position"
        parameter.values = {"OFF": 1, "ON": 0}
        parameter.acknowledge_value = "a"
        parameter.acknowledge_timeout = 4.0
        parameter.write_acknowledge = True
        parameter.enabled = False
        self._parameters_aux_valve.append(parameter)
        self.add_parameter(parameter)
        self._parameter_aux_valve_move = parameter

        parameter = ArduinoParameter(
            name="aux_valve_actual",
            access_level=ParameterAccess.R,
            value_type=SwitchValvePositionValue,
            internal_id=17,
        )
        parameter.description = "Auxiliary switch-valve position read from sensor"
        parameter.enabled = False
        self._parameters_aux_valve.append(parameter)
        self.add_parameter(parameter)

        # This is on by default.
        parameter = ArduinoParameter(
            name="ack_aux_valve",
            access_level=ParameterAccess.RW,
            value_type=ArduinoValueOnOff,
            internal_id=20,
        )
        parameter.description = (
            "Send acknowledge message after auxiliary valve move is complete"
        )
        parameter.caching_policy = ParameterCachingPolicy.TRY
        parameter.enabled = False
        self._parameters_acks.append(parameter)
        self._parameters_aux_valve.append(parameter)
        self.add_parameter(parameter)
        self._parameter_ack_aux_valve = parameter

        parameter = ArduinoParameter(
            name="diameter",
            access_level=ParameterAccess.RW,
            value_type=float,
            internal_id=5,
        )
        parameter.description = "Syringe diameter"
        parameter.units = " mm"
        parameter.max_value = 100.0
        parameter.min_value = 0.1
        parameter.float_rounding = 6
        parameter.caching_policy = ParameterCachingPolicy.TRY
        self._parameter_diameter = parameter
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="steps_per_ml",
            access_level=ParameterAccess.RW,
            value_type=float,
            internal_id=6,
        )
        parameter.description = "Motor steps per unit volume pumped"
        parameter.units = " 1/mL"
        parameter.max_value = 10000000.0
        parameter.min_value = 10.0
        parameter.caching_policy = ParameterCachingPolicy.TRY
        self._parameter_steps_per_ml = parameter
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="flowrate",
            access_level=ParameterAccess.RW,
            value_type=float,
            internal_id=7,
        )
        parameter.description = "Syringe pump flowrate"
        parameter.units = " mL/min"
        parameter.max_value = 5.0  # This is overridden by the max flowrate setup option
        parameter.min_value = 0.0001
        parameter.float_rounding = 4
        parameter.caching_policy = ParameterCachingPolicy.TRY
        self._parameter_flowrate = parameter
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="enable",
            access_level=ParameterAccess.RW,
            value_type=ArduinoValueOnOff,
            internal_id=8,
        )
        parameter.description = "Enable syringe pump motor"
        parameter.caching_policy = ParameterCachingPolicy.TRY
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="stop",
            access_level=ParameterAccess.W,
            value_type=ArduinoValueRun,
            internal_id=21,
        )
        parameter.description = "Stop syringe pumping or zeroing movements"
        parameter.acknowledge_value = "k"
        parameter.acknowledge_timeout = 0.5
        parameter.write_acknowledge = True
        self.add_parameter(parameter)
        self._parameter_stop = parameter
        self._parameters_pump_movement.append(self._parameter_stop)

        parameter = ArduinoParameter(
            name="pump", access_level=ParameterAccess.W, value_type=float, internal_id=9
        )
        parameter.description = "Pump volume now"
        parameter.units = " uL"
        parameter.acknowledge_value = "k"
        parameter.acknowledge_timeout = 2.0
        parameter.write_acknowledge = True
        parameter.float_rounding = 2
        parameter.stop_command = self._command_write(self._parameter_stop, "RUN")
        self.add_parameter(parameter)
        self._parameter_pump = parameter
        self._parameters_pump_movement.append(self._parameter_pump)
        self._parameters_volumes.append(self._parameter_pump)

        parameter = ArduinoParameter(
            name="volume_left",
            access_level=ParameterAccess.R,
            value_type=float,
            internal_id=10,
        )
        parameter.description = "Volume left since last pump command"
        parameter.units = " uL"
        parameter.float_rounding = 2
        self.add_parameter(parameter)
        self._parameters_volumes.append(parameter)

        parameter = ArduinoParameter(
            name="volume",
            access_level=ParameterAccess.RW,
            value_type=float,
            internal_id=11,
        )
        parameter.description = "Volume of fluid within syringe"
        parameter.units = " uL"
        parameter.float_rounding = 2
        self.add_parameter(parameter)
        self._parameters_volumes.append(parameter)

        parameter = ArduinoParameter(
            name="zero",
            access_level=ParameterAccess.W,
            value_type=PumpZeroValue,
            internal_id=12,
        )
        parameter.description = "Zero syringe pump in the specified direction"
        parameter.acknowledge_value = "k"
        parameter.acknowledge_timeout = 2.0
        parameter.write_acknowledge = True
        parameter.stop_command = self._command_write(self._parameter_stop, "RUN")
        self.add_parameter(parameter)
        self._parameter_zero = parameter
        self._parameters_pump_movement.append(self._parameter_zero)

        parameter = ArduinoParameter(
            name="flowrate_no_acc",
            access_level=ParameterAccess.R,
            value_type=float,
            internal_id=22,
        )
        parameter.description = "Maximum pump flowrate achieved without acceleration"
        parameter.units = " mL/min"
        parameter.max_value = 100.0
        parameter.min_value = 0.0001
        parameter.float_rounding = 4
        parameter.caching_policy = ParameterCachingPolicy.TRY
        self._parameter_flowrate_no_acc = parameter
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="flowrate_acceleration",
            access_level=ParameterAccess.R,
            value_type=float,
            internal_id=23,
        )
        parameter.description = "Acceleration for gradual increase of flowrate from 'flowrate_no_acc' to the target value"
        parameter.units = " mL/min^2"
        parameter.max_value = 100.0
        parameter.min_value = 0.0001
        parameter.float_rounding = 4
        parameter.caching_policy = ParameterCachingPolicy.TRY
        self._parameter_flowrate_acceleration = parameter
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="error",
            access_level=ParameterAccess.R,
            value_type=SyringePumpError,
            internal_id=13,
        )
        parameter.description = "Error register"
        self._error_parameter = parameter
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="save",
            access_level=ParameterAccess.W,
            value_type=ArduinoValueRun,
            internal_id=14,
        )
        parameter.description = "Save parameters as default"
        self.add_parameter(parameter)

        parameter = ArduinoParameter(
            name="reset",
            access_level=ParameterAccess.W,
            value_type=ArduinoValueRun,
            internal_id=15,
        )
        parameter.description = "Restore parameters to factory"
        self.add_parameter(parameter)

        # do this after adding the parameters.
        self.syringe_volume = 1100.0

        syringe_volume_option = DeviceSetupOption(
            name="syringe_volume", handler=self.setup_syringe_volume
        )
        syringe_volume_option.value_type = str
        syringe_volume_option.required = True
        syringe_volume_option.description = (
            "Set the maximum volume of the syringe."
            " Use a string including units (metric)."
        )
        self.add_setup_option(syringe_volume_option)

        syringe_stepperml_option = DeviceSetupOption(
            name="syringe_stepperml", handler=self.setup_syringe_stepperml
        )
        syringe_stepperml_option.value_type = (str, float)
        syringe_stepperml_option.description = (
            "Set the device motor-steps to volume conversion factor directly."
        )
        self.add_setup_option(syringe_stepperml_option)

        syringe_diameter_option = DeviceSetupOption(
            name="syringe_diameter", handler=self.setup_syringe_diameter
        )
        syringe_diameter_option.value_type = str
        syringe_diameter_option.description = (
            "Set the syringe diameter to allow the "
            "device to know how much volume was pumped. Use a string including units (metric)."
        )
        self.add_setup_option(syringe_diameter_option)

        max_flow_option = DeviceSetupOption(
            name="max_flowrate", handler=self.setup_max_flowrate
        )
        max_flow_option.value_type = float
        max_flow_option.required = True
        max_flow_option.description = "Set the maximum flowrate allowed for the pump."
        self.add_setup_option(max_flow_option)

        valve_option = DeviceSetupOption(name="valve", handler=self.setup_valve)
        valve_option.value_type = bool
        valve_option.description = (
            "Enable access to the parameters controlling the main switchvalve."
        )
        self.add_setup_option(valve_option)

        aux_valve_option = DeviceSetupOption(
            name="aux-valve", handler=self.setup_aux_valve
        )
        aux_valve_option.value_type = bool
        aux_valve_option.description = (
            "Enable access to the parameters controlling the auxiliary switchvalve."
        )
        self.add_setup_option(aux_valve_option)

        encoder_option = DeviceSetupOption(name="encoder", handler=self.setup_encoder)
        encoder_option.value_type = bool
        encoder_option.description = "Disable encoder wheel feedback on the motor."
        self.add_setup_option(encoder_option)

        valve_position_option = DeviceSetupOption(
            name="reservoir_valve_position", handler=self.setup_reservoir_valve_position
        )
        valve_position_option.value_type = str
        valve_position_option.description = (
            "Sets the valve position for pumping to the reservoir"
            " (the opposite will be used to pump to the flow system)."
        )
        self.add_setup_option(valve_position_option)

    def use_pump_acknowledge(self, enable: bool) -> None:
        """
        Sets relevant parameters to expect acknowledge strings after a pump operation
        is concluded. This should be enabled if acknowledge is enabled on the device.

        @param enable: bool
            True if acknowledge strings are expected.
        """
        for parameter in self._parameters_pump_movement:
            parameter.write_acknowledge = enable

    def use_valve_acknowledge(self, enable: bool) -> None:
        """
        Sets relevant parameters to expect acknowledge strings after a valve operation
        is concluded. This should be enabled if acknowledge is enabled on the device.

        @param enable: bool
            True if acknowledge strings are expected.
        """
        self._parameter_valve_move.write_acknowledge = enable

    def use_aux_valve_acknowledge(self, enable: bool) -> None:
        """
        Sets relevant parameters to expect acknowledge strings after an auxiliary valve
        operation is concluded. This should be enabled if acknowledge is enabled on the device.

        @param enable: bool
            True if acknowledge strings are expected.
        """
        self._parameter_aux_valve_move.write_acknowledge = enable

    def initialize(self) -> None:
        """
        Prepare the device for operation.
        Time intensive operations (movement, zeroing, syncing) are performed here.
        """
        ArduinoDevice.initialize(self)

        self.use_pump_acknowledge(self.__getitem__("ack_pump") == "ON")
        if self._parameter_ack_aux_valve.enabled:
            self.use_aux_valve_acknowledge(
                self.__getitem__(self._parameter_ack_aux_valve.name) == "ON"
            )
        if self._parameter_ack_valve.enabled:
            self.use_valve_acknowledge(
                self.__getitem__(self._parameter_ack_valve.name) == "ON"
            )
            self.__setitem__("valve_setpoint", "OFF")
        self.__setitem__("flowrate", self.max_flowrate)
        self.__setitem__("enable", "ON")
        self.__setitem__("zero", "FILL")
        self.__setitem__("enable", "OFF")

    def emergency_close(self) -> None:
        """
        Disconnect the device in an emergency situation.
        Stops any operation asynchronously.
        """
        self.close()

    def prepare_for_close(self) -> None:
        """
        Ensure the device is in a safe state before closing the connection (and losing control).
        """
        # self.__setitem__("stop", "RUN")
        self.__setitem__("enable", "OFF")

    def setup_syringe_volume(self, volume: str) -> None:
        """
        Setup handler for 'syringe_volume'.
        Uses the option to set the soft limits for the pump.

        @param volume: str
            Volume of the syringe with SI units (mL, uL, ecc...).
        """
        try:
            syringe_volume, units = parse_with_units(volume, "uL")
            self.syringe_volume = syringe_volume
        except ValueError as e:
            e.add_note(self.exception_note())
            self.log_exception(e)
            raise

    def setup_syringe_stepperml(self, stepperml: str | float) -> None:
        """
        Setup handler for 'syringe_stepperml'.
        Uses the option to set the default mL to steps conversion. This is a preferred alternative to setting the
        syringe diameter.

        @param stepperml: str | float
            Number of stepper motor steps corresponding to 1mL of pumped fluid. Must be expressed without units.
        """
        if isinstance(stepperml, str):
            try:
                stepperml = float(stepperml)
            except ValueError as e:
                e.add_note(self.exception_note())
                self.log_exception(e)
                raise
        self.__setitem__("steps_per_ml", stepperml)

    def setup_syringe_diameter(self, diameter: str) -> None:
        """
        Setup handler for 'syringe_diameter'.
        Uses the option to set the default mL to steps conversion.

        @param diameter: str
            Inner diameter of the syringe with SI units (mm, um, ecc...).
        """
        try:
            diameter, units = parse_with_units(diameter, "mm")
            self.__setitem__("diameter", diameter)
        except ValueError as e:
            e.add_note(self.exception_note())
            self.log_exception(e)
            raise

    @property
    def max_flowrate(self) -> float:
        """
        Maximum allowed flowrate for the pump in mL/min.

        @return: float
            Maximum flowrate in mL/min.
        """
        return self._parameter_flowrate.max_value

    def setup_max_flowrate(self, flowrate: float) -> None:
        """
        Setup handler for 'max_flowrate'.
        Uses the option to set a maximum flowrate for the pump.

        @param flowrate: float
            Maximum allowed flowrate in mL/min.
        """
        self._parameter_flowrate.max_value = flowrate

    def has_valve(self) -> bool:
        """
        Checks whether the device was configured with a main valve.

        @return: bool
            True if the main valve is available.
        """
        return self._parameter_valve_move.enabled

    def has_aux_valve(self) -> bool:
        """
        Checks whether the device was configured with a main valve.

        @return: bool
            True if the main valve is available.
        """
        return self._parameter_aux_valve_move.enabled

    def setup_valve(self, valve: bool) -> None:
        """
        Setup handler for 'valve'.
        Enables usage of the main switchvalve of the pump.

        @param valve: bool
            Must be True to enable the valve.
        """
        if not isinstance(valve, bool):
            error = TypeError(
                f"Invalid type '{valve}' for setup argument 'valve', expected bool."
            )
            error.add_note(self.exception_note())
            self.log_exception(error)
            raise error
        for parameter in self._parameters_valve:
            parameter.enabled = valve

    def setup_aux_valve(self, valve: bool) -> None:
        """
        Setup handler for 'aux-valve'.
        Enables usage of the auxiliary switchvalve of the pump.

        @param valve: bool
            Must be True to enable the valve.
        """
        if not isinstance(valve, bool):
            error = TypeError(
                f"Invalid type '{valve}' for setup argument 'valve', expected bool."
            )
            error.add_note(self.exception_note())
            self.log_exception(error)
            raise error
        for parameter in self._parameters_aux_valve:
            parameter.enabled = valve

    def setup_encoder(self, use_encoder: bool) -> None:
        """
        Setup handler for 'encoder'.
        Allows to disable the encoder wheel feedback. This is highly discouraged, as blocakges of the motor will not
        be detected, leading to damage to the pump or other parts of the system.

        @param use_encoder: bool
            If False, the encoder is ignored.
        """
        value = "ON" if use_encoder else "OFF"
        self.__setitem__("encoder", value)

    @property
    def main_valve_to_system(self) -> SwitchValveSetpointValue | None:
        """
        Position of the switchvalve corresponding to the solvent reservoir.

        @return: SwitchValveSetpointValue | None
        """
        if self.has_valve():
            return copy.deepcopy(self._switchvalve_system)
        else:
            return None

    @property
    def main_valve_to_reservoir(self) -> SwitchValveSetpointValue | None:
        """
        Position of the switchvalve corresponding to the solvent reservoir.

        @return: SwitchValveSetpointValue | None
        """
        return copy.deepcopy(self._switchvalve_reservoir)

    def setup_reservoir_valve_position(self, position: str) -> None:
        """
        Setup handler for 'reservoir_valve_position'.
        Sets the valve position for pumping to the reservoir (the opposite will be used to pump to the flow system).

        @param position: str
            Switchvalve position as a string ('ON' or 'OFF').
        """
        try:
            self._switchvalve_reservoir = SwitchValveSetpointValue(position)
            self._switchvalve_system = copy.deepcopy(self._switchvalve_reservoir)
            self._switchvalve_system.invert()
        except ValueError as e:
            e.add_note(
                "Occurred while processing 'reservoir_valve_position' setup option."
            )
            e.add_note(self.exception_note())
            raise

    def setup(self, **kwargs) -> None:
        """
        Setup device parameters. This should be called after open() to set-up some initial conditions of the
        device.

        @keyword syringe_volume: str
            Volume of the syringe mounted on the device in uL (with units).
        @keyword syringe_diameter: str
            Diameter of the syringe mounted on the device in mm (with units).
        @keyword syringe_stepperml: str | float
            Steps of the motor for each mL of solution moved by the pump. Overrides syringe_diameter.
        """
        if (
            "syringe_stepperml" not in kwargs.keys()
            and "syringe_diameter" not in kwargs.keys()
        ):
            self.log(
                "Neither syringe diameter nor steps_per_ml specified.", level="warning"
            )
        elif (
            "syringe_stepperml" in kwargs.keys() and "syringe_diameter" in kwargs.keys()
        ):
            self.log(
                "Both syringe diameter and steps_per_ml specified.", level="warning"
            )
        ArduinoDevice.setup(self, **kwargs)

    def pumping_time(self, volume: float) -> float:
        """
        Calculates the expected time required to pump a volume of liquid with the current syringe pump
        configuration.

        @param volume: float
            The volume to be pumped in uL.
        @return: float
            The time in S required to pump the volume.
        """
        comm_latency = 0.2  # [S]
        volume = abs(volume) / 1000  # [mL]
        target_flowrate = self.__getitem__("flowrate")  # [mL/min]
        slow_flowrate = self.__getitem__("flowrate_no_acc")  # [mL/min]

        # At low flowrates, the calculation is very simple.
        if target_flowrate <= slow_flowrate:
            return volume / target_flowrate * 60.0 + comm_latency

        # Calculate the volume pumped if accelerating from min to target flowrate.
        # If this is more than the requested volume, target flowrate is never reached.
        acceleration = self.__getitem__("flowrate_acceleration")  # [mL/min^2]
        acc_volume = (target_flowrate**2 - slow_flowrate**2) / (
            2 * acceleration
        )  # [mL]

        # <3
        if volume > acc_volume:
            return (
                (volume + (target_flowrate - slow_flowrate) ** 2 / (2 * acceleration))
                / target_flowrate
            ) * 60.0 + comm_latency
        elif volume < acc_volume:
            return (
                ((slow_flowrate / acceleration) ** 2 + 2 * volume / acceleration) ** 0.5
                - slow_flowrate / acceleration
            ) * 60.0 + comm_latency
        else:
            return (
                target_flowrate - slow_flowrate
            ) / acceleration * 60.0 + comm_latency

    def _write(self, parameter: ArduinoParameter, value: Any) -> None:
        """
        Low-level function to write data to the device.

        @param parameter: DeviceParameter
            The parameter which will be accessed.
        @param value:
            The value of the parameter to be written to the device.
        @raise: RuntimeError
            In case of issues with communications.
        """
        if parameter is self._parameter_pump:
            current_volume = self.__getitem__("volume")
            if current_volume - value < 0.0:
                message = (
                    f"Cannot pump {parameter.to_string(value)}: "
                    f"insufficient volume available ({current_volume}{parameter.units})."
                )
                error = ParameterError(message, device=self, parameter=parameter)
                self.log_exception(error)
                raise error
            elif current_volume - value > self._max_syringe_volume:
                message = (
                    f"Cannot pump {parameter.to_string(value)}: "
                    f"insufficient syringe capacity ( required: {current_volume - value}{parameter.units}"
                    f", available: {self._max_syringe_volume}{parameter.units})."
                )
                error = ParameterError(message, device=self, parameter=parameter)
                self.log_exception(error)
                raise error
        if parameter.write_acknowledge:
            if parameter is self._parameter_pump:
                parameter.acknowledge_timeout = (
                    self.pumping_time(value) * self._timeout_multiplier
                )
            elif parameter is self._parameter_zero:
                parameter.acknowledge_timeout = (
                    self.pumping_time(self._max_syringe_volume)
                    * self._timeout_multiplier
                )
        try:
            ArduinoDevice._write(self, parameter, value)
        except MotorError as error:
            if parameter in self._parameters_pump_movement:
                raise
            else:
                self.log(error, level="warning")
        if parameter in self._parameters_acks:
            if parameter is self._parameter_ack_pump:
                self.use_pump_acknowledge(value == 1)
            elif parameter is self._parameter_ack_valve:
                self.use_valve_acknowledge(value == 1)
            elif parameter is self._parameter_ack_aux_valve:
                self.use_aux_valve_acknowledge(value == 1)
        elif parameter is self._parameter_zero:
            if value == "FILL":
                self.__setitem__("volume", self._max_syringe_volume)
        elif parameter in (self._parameter_diameter, self._parameter_steps_per_ml):
            self._parameter_flowrate_no_acc.last_known_value = None
            self._parameter_flowrate_acceleration.last_known_value = None
