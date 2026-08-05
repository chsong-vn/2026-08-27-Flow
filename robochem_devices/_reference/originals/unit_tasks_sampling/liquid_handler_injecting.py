"""
File: liquid_handler_injecting.py
Author: Simone Pilon, Elia Savino - Noël Research Group - 2024
GitHub: https://github.com/simone16

Description: Tasks for sample injection using the NRG Sampler and Pumps.
"""

from abc import ABC
import copy
from time import sleep

from omniplatypus.devices.platform import Platform
from omniplatypus.devices.nrg.syringe_pump import SyringePump, SwitchValveSetpointValue
from omniplatypus.devices.nrg.sampler import Sampler, InjectionPort
from omniplatypus.devices.nrg.gpio import SolenoidValve
from omniplatypus.devices.nrg.phase_sensor import PhaseSensor
from omniplatypus.devices.snapshot import Snapshot

from omniplatypus.procedures.unit_tasks.sampling.liquid_handler_moving import (
    SamplerTask,
)
from omniplatypus.procedures.unit_tasks.driving.driving_pumps import (
    FillPump,
    PumpVolume,
)


class InjectionTask(SamplerTask, ABC):
    """
    Base prototype for tasks involving the sampler injection port.
    """

    @classmethod
    def _validate_platform(cls, platform: Platform):
        """Validate the input platform."""
        cls._validate_input_log(
            platform, Platform, "platform argument must be a Platform object."
        )

    @classmethod
    def _validate_pump(cls, pump: SyringePump) -> None:
        cls._validate_input_log(
            pump,
            SyringePump,
            "Pump must be a SyringePump.",
        )

    @classmethod
    def _validate_sampler(cls, sampler: Sampler):
        cls._validate_input_log(
            sampler, Sampler, "sampler argument must be a Sampler object."
        )

    @classmethod
    def _validate_solenoid_valve(cls, valve: SolenoidValve) -> None:
        cls._validate_input_log(
            valve,
            SolenoidValve,
            "Gas valve must be a SolenoidValve object.",
        )

    @classmethod
    def _validate_phase_sensor(cls, sensor: PhaseSensor) -> None:
        cls._validate_input_log(
            sensor,
            PhaseSensor,
            "Sampler phase sensor must be a PhaseSensor object.",
        )

    @classmethod
    def _get_sampler_pump(
        cls, platform: Platform, sampler: Sampler
    ) -> SyringePump | None:
        """
        Get the syringe pump connected to this sampler.

        @param platform: Platform
            The platform running the experiment.
        @param sampler: Sampler
            The sampler running the task.
        @return: SyringePump | None
            The pump connected to the sampler and to the injection port switchvalve.
            If no pump was configured returns None.
        @raise: KeyError
            If the name of the pump provided by the configuration file is not found in the platform.
        """
        if sampler.sampling_pump_name is not None:
            try:
                # noinspection PyTypeChecker
                return platform[sampler.sampling_pump_name]
            except KeyError as e:
                e.add_note(
                    f"The pump '{sampler.sampling_pump_name}' configured for sampler '{sampler}'"
                    f" was not found in platform '{platform}'."
                )
                cls.log(e)
                raise
        return None

    @classmethod
    def _injection_port_switchvalve_pump(
        cls, platform: Platform, sampler: Sampler
    ) -> SyringePump:
        """
        Connect the flow system to the injection port or to the main pump.

        @param platform: Platform
            The platform running the experiment.
        @param sampler: Sampler
            The sampler running the task.
        @return: SyringePump
            The pump controlling the switchvalve of the injection port.
        @raise: KeyError
            If the pump configured for the sampler is not found in platform.
        @raise: ValueError
            If the sampler is not configured with a pump.
        """
        pump = cls._get_sampler_pump(platform, sampler)
        if pump is None:
            error = ValueError(
                f"Sampler '{sampler}' is not configured with a pump, cannot access its switchvalve."
            )
            cls.log(error)
            raise error
        return pump

    @classmethod
    def _injection_port_switchvalve_position(
        cls, sampler: Sampler, port_name: str
    ) -> SwitchValveSetpointValue | None:
        """
        Connect the flow system to the injection port or to the main pump.

        @param sampler: Sampler
            The sampler running the task.
        @param port_name: str
            The name of the injection port we wish to (dis)connect to the flow system.
            Must belong to the sampler.
        @return: SwitchValveSetpointValue | None
            The switchvalve position corresponding to the injection, or None if none was configured.
        @raise: KeyError
            If the pump configured for the sampler is not found in platform or if the port_name does not correspond
            to an injection port on the sampler.
        @raise: ValueError
            If the sampler is not configured with a pump.
        """
        port = sampler.locations.get(port_name, None)
        if port is None:
            error = KeyError(
                f"Injection port '{port_name}' is not a location recognized by sampler '{sampler}'."
            )
            cls.log(error)
            raise error
        if not isinstance(port, InjectionPort):
            error = ValueError(
                f"Injection port '{port_name}' in sampler '{sampler}' is of type '{type(port)}'."
            )
            cls.log(error)
            raise error
        if port.injection_valve_position is not None:
            return copy.deepcopy(port.injection_valve_position)
        return None


class ConnectInjectionPort(InjectionTask):
    """
    Activate the switchvalve connected to an injection port to connect it to the flow system or to isolate it.
    """

    @classmethod
    def run(
        cls, platform: Platform, sampler: Sampler, port_name: str, inject: bool
    ) -> None:
        """
        Activate the switchvalve connected to an injection port to connect it to the flow system or to isolate it.

        @param platform: Platform
            The platform running the experiment.
        @param sampler: Sampler
            The sampler running the task.
        @param port_name: str
            The name of the injection port on the sampler, as found in sampler.locations.keys()
        @param inject: bool
            If true, the port is connected to the flow system, otherwise it is isolated.
        @return: None
        """
        return super().run(
            platform=platform,
            sampler=sampler,
            port_name=port_name,
            inject=inject,
        )

    @classmethod
    def _validate_input(
        cls, platform: Platform, sampler: Sampler, port_name: str, inject: bool
    ) -> None:
        cls._validate_platform(platform)
        cls._validate_sampler(sampler)

    @classmethod
    def _execute(
        cls, platform: Platform, sampler: Sampler, port_name: str, inject: bool
    ) -> None:
        """
        Activate the switchvalve connected to an injection port to connect it to the flow system or to isolate it.

        @param platform: Platform
            The platform running the experiment.
        @param sampler: Sampler
            The sampler running the task.
        @param port_name: str
            The name of the injection port on the sampler, as found in sampler.locations.keys()
        @param inject: bool
            If true, the port is connected to the flow system, otherwise it is isolated.
        @return: None
        """
        pump = cls._injection_port_switchvalve_pump(platform=platform, sampler=sampler)
        valve_position = cls._injection_port_switchvalve_position(
            sampler=sampler, port_name=port_name
        )
        if not inject:
            valve_position.invert()
        pump["aux_valve_setpoint"] = valve_position


class Inject(InjectionTask):
    """
    Inject into an injection port of a liquid handler.

    Usage:
    call cls.run() to execute this task.
    """

    @classmethod
    def run(
        cls,
        platform: Platform,
        sampler: Sampler,
        injection_port_name: str,
        volume: float,
        flowrate: float,
        sampling_pump: SyringePump | None = None,
        retract: bool = True,
        plunge_speed: float = 200.0,
        retract_speed: float | None = None,
        move_speed: float | None = None,
        delay_after: float = 0.0,
    ):
        """
        Inject a slug from a liquid handler into the flow system.
        Note: the slug is not delimited with gas bubble, therefore this task is only useful for cleaning or in
        combination with other routines ensuring the slug is delimited.

        @param platform: Platform
            The platform running the experiment.
        @param sampler: LiquidHandlerSampler
            The sampler device.
        @param sampling_pump: SyringePumpNrg
            The pump used to pump volumes to and from the vials. Defaults to the pump specified in the configuration
            file. If no volume is pumped, this can be left out.
        @param injection_port_name: str
            The name of the injection port location stored on sampler.locations.
        @param volume: float
            The volume to inject in uL.
        @param flowrate: float
            The flowrate to use for injection in mL/min.
        @param retract: bool = True
            If True, the needle is removed from the injection port at the end of the operation, otherwise
            it is left in (useful for directly cleaning the system).
        @param delay_after: float = 1.0
            Delay in seconds to wait after injection before closing the injection valve.
            Allows the tail bubble to equilibrate.
        @param plunge_speed: float = 200.0
            The needle is moved vertically at this speed [mm/min].
        @param retract_speed: float | None = None
            The needle is moved up from the vial at this speed [mm/min]. None uses default value from sampler.
        @param move_speed: float | None = None
            The sampler moves at this speed [mm/min]. None uses default value from sampler.
        """
        return super().run(
            platform=platform,
            sampler=sampler,
            injection_port_name=injection_port_name,
            volume=volume,
            flowrate=flowrate,
            sampling_pump=sampling_pump,
            retract=retract,
            plunge_speed=plunge_speed,
            retract_speed=retract_speed,
            move_speed=move_speed,
            delay_after=delay_after,
        )

    @classmethod
    def _validate_input(
        cls,
        platform: Platform,
        sampler: Sampler,
        injection_port_name: str,
        volume: float,
        flowrate: float,
        sampling_pump: SyringePump | None = None,
        retract: bool = True,
        plunge_speed: float = 200.0,
        retract_speed: float | None = None,
        move_speed: float | None = None,
        delay_after: float = 0.0,
    ):
        """Validate input arguments."""
        if sampling_pump is not None:
            cls._validate_pump(sampling_pump)
        cls._validate_sampler(sampler)
        cls._validate_input_log(
            injection_port_name,
            lambda x: isinstance(sampler.locations.get(x, None), InjectionPort),
            f"Injection port '{injection_port_name}' not found on sampler '{sampler}'.",
        )
        cls._validate_input_log(
            delay_after, lambda x: x >= 0.0, "Delay must be positive"
        )

    @classmethod
    def _execute(
        cls,
        platform: Platform,
        sampler: Sampler,
        injection_port_name: str,
        volume: float,
        flowrate: float,
        sampling_pump: SyringePump | None = None,
        retract: bool = True,
        plunge_speed: float = 200.0,
        retract_speed: float | None = None,
        move_speed: float | None = None,
        delay_after: float = 0.0,
    ):
        sampler_initial_state = Snapshot(device=sampler, parameters=["feed"])

        # Fill the pumps as needed
        if sampling_pump is None and not volume == 0.0:
            sampling_pump = cls._get_sampler_pump(platform, sampler)
            if sampling_pump is None:
                error = ValueError(
                    f"No default sampling pump configured for '{sampler}', "
                    f"cannot pump {volume} uL."
                )
                cls.log(error)
                raise error
            FillPump.run(pump=sampling_pump)

        # find the switchvalve on the injection port (if needed)
        valve_controller = None
        valve_position_inject = cls._injection_port_switchvalve_position(
            sampler, injection_port_name
        )
        valve_position_isolate = None
        if valve_position_inject is not None:
            valve_controller = cls._injection_port_switchvalve_pump(
                platform=platform,
                sampler=sampler,
            )
            valve_position_isolate = copy.deepcopy(valve_position_inject)
            valve_position_isolate.invert()
            valve_controller["aux_valve_setpoint"] = valve_position_isolate

        # Move the sampler to the injection port
        try:
            injection_port_move = sampler.locations[injection_port_name].move
            injection_port_plunge = sampler.locations[injection_port_name].plunge
            cls._move_xy(
                sampler=sampler,
                destination=injection_port_move,
                move_speed=move_speed,
                retract_speed=retract_speed,
            )
            if valve_position_inject is not None:
                valve_controller["aux_valve_setpoint"] = valve_position_inject
            cls._move_z(
                sampler=sampler,
                destination=injection_port_plunge,
                plunge_speed=plunge_speed,
            )
            if not volume == 0.0:
                PumpVolume.run(
                    pump=sampling_pump,
                    volume=volume,
                    flowrate=flowrate,
                    allow_refill=True,
                )
            if delay_after > 0.0:
                sleep(delay_after)
        finally:
            if valve_position_isolate is not None:
                valve_controller["aux_valve_setpoint"] = valve_position_isolate

        if retract:
            cls._set_travel_position(sampler=sampler, retract_speed=retract_speed)
        sampler_initial_state.restore()
