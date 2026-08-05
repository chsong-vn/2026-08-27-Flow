"""
File: driving_pumps.py
Author: Elia Savino, Simone Pilon - Noël Research Group - 2023
GitHub: github.com/EliaSavino, https://github.com/simone16

Happy Hacking!

Description: This is the unit tasks set for all syringe pumps. At the moment, only the SimoTronic pumps are supported.
Specific tasks (e.g. sampling) can be found in the specific modules.
"""

import copy
from typing import Any
from abc import ABC, abstractmethod
from threading import Event, Thread
from queue import Queue, Full

from .syringe_pump import SyringePump, SwitchValveSetpointValue
from .snapshot import Snapshot
from .base_unit_task import BaseUnitTaskTemplate
from .task_errors import TaskTimeoutError


class PumpTask(BaseUnitTaskTemplate, ABC):
    """
    Base prototype for pump related tasks.
    """

    @classmethod
    @abstractmethod
    def _execute_pump(cls, *args, **kwargs) -> Any:
        """
        Pump task execution.
        Within this method, pump can be assumed to be enabled and with acknowledge. The pump is returned to its
        initial state once complete.

        @param args:
        @param kwargs:
        @return:
        """
        pass

    @classmethod
    def _validate_pump(cls, pump: SyringePump) -> None:
        """
        Validate the pump given to the task.

        @param pump: SyringePump
            The syringe pump device.
        @return: None
        """
        cls._validate_input_log(
            pump,
            SyringePump,
            "Pump must be a SyringePump object.",
        )

    @classmethod
    def _get_reservoir_valve_position(
        cls,
        pump: SyringePump,
        reverse: bool,
        given_position: str | SwitchValveSetpointValue | None,
    ) -> SwitchValveSetpointValue:
        """
        Decides which main valve position to use for connecting the pump to its reservoir based on the user provided
        argument (overrides) and the pump configuration.
        To connect the pump to the flow system, invert the valve position.

        @param pump: SyringePump
            A validated syringe pump.
        @param reverse: bool
            If set to true, the task will be performed in reverse, swapping the valve positions.
            For example, it will refill from the flow line position and pump into the reservoir.
        @param given_position: str | SwitchValveSetpointValue | None
            The argument provided by the used, overriding any setting.
        @return: SwitchValveSetpointValue
            Main switchvalve position corresponding to the solvent reservoir.
        @raise: ValueError
            If an invalid argument (or combination of arguments) is provided.
        """
        if given_position is None:
            valve_position = pump.main_valve_to_reservoir
            if valve_position is None:
                error = ValueError(
                    f"Pump {pump} was configured with a main valve,"
                    f" but no reservoir valve position was configured or specified for this task."
                )
                cls.log(error)
                raise error
        else:
            try:
                valve_position = SwitchValveSetpointValue(given_position)
            except ValueError as e:
                e.add_note(
                    f"Invalid valve position argument given for '{cls.__name__}' task."
                )
                cls.log(e)
                raise e
        if reverse:
            valve_position.invert()
        return valve_position

    @classmethod
    def _execute(cls, pump: SyringePump, *args, **kwargs) -> Any:
        initial_pump_state = Snapshot(
            device=pump, parameters=["ack_pump", "enable", "flowrate"]
        )
        if initial_pump_state.values["ack_pump"] == "OFF":
            pump["ack_pump"] = "ON"
        if initial_pump_state.values["enable"] == "OFF":
            pump["enable"] = "ON"

        return_value = cls._execute_pump(pump, *args, **kwargs)

        initial_pump_state.restore()

        return return_value

    @classmethod
    def thread_name(cls, *args, **kwargs) -> str:
        """
        A meaningful name for a thread running this task with the given arguments.

        @param args:
            run arguments.
        @param kwargs:
            run keyword arguments.
        @return: str
            A name for a thread running this task.
        """
        pump_name = str(kwargs.get("pump", ""))
        if pump_name:
            pump_name = f" ({pump_name})"
        return cls.__name__ + pump_name


class BackgroundPumpTask(BaseUnitTaskTemplate, ABC):
    """
    Base class for pumping tasks which require to perform other activities while pumping and stop asynchronously the
    pumping operation.

    Usage:
    Inherit from this and implement your own check.
    """

    @classmethod
    def _pumping_thread_target(
        cls,
        pump: SyringePump,
        volume: float,
        flowrate: float,
        done_event: Event,
        pumping_event: Event,
        allow_refill: bool = True,
        actual_volume_queue: Queue | None = None,
    ) -> None:
        """
        Target for the pumping thread.
        Can perform a pumping operation on a separate thread, communicating via done_event and pumping_event.

        @param pump: SyringePump
            The pump chosen for the task.
        @param volume: float
            Volume to pump in uL.
        @param flowrate: float
            Flowrate for the pumping action in mL/min.
        @param done_event: Event
            Once the pumping is done, this event will be set before the function returns.
        @param pumping_event: Event
            This event is provided to the pumping task to signal when to send the stop event.
            The task sets this event when actively pumping (not refilling), so that the stop event can be sent to
            the pump.
        @param allow_refill: bool = True
            Is the pump allowed to refill within this operation?
        @param actual_volume_queue: Queue | None = None
            If set, the actual volume pumped during this operation is stored here [uL].
        """
        actual_volume = PumpVolume.run(
            pump=pump,
            volume=volume,
            flowrate=flowrate,
            allow_refill=allow_refill,
            pumping_event=pumping_event,
        )
        if actual_volume_queue is not None:
            try:
                actual_volume_queue.put(actual_volume, block=False)
            except Full:
                error = ValueError(
                    f"{cls.__name__} was provided with a full queue to store actual volume pumped."
                )
                error.add_note(
                    "To prevent errors, provide an empty queue to this task."
                )
                cls.log(error)
                raise error
        done_event.set()

    @classmethod
    def _start_pumping(cls, pumping_thread: Thread) -> None:
        """
        Start pushing material by pumping with the syringe.

        @param pumping_thread: Thread
            The thread which will be started and should pump.
        @return: None
        """
        pumping_thread.start()

    @classmethod
    def _stop_pumping(
        cls,
        pump: SyringePump,
        pumping_event: Event,
    ) -> None:
        """
        Stop pushing material by stopping the pump.

        @param pump: SyringePump
            The pump operating in the pumping thread.
        @param pumping_event: Event
            This event is provided to the pumping task to signal when to send the stop event.
            The task sets this event when actively pumping (not refilling), so that the stop event can be sent to
            the pump.
        @return: None
        """
        PumpVolume.stop_asynchronously(pump=pump, pumping_event=pumping_event)


class PrimePump(PumpTask):
    """
    Prime a syringe pump into its reservoir by performing several empty-fill cycles.
    This is helpful to remove bubbles from the syringe and recommended after some time the pumps have not been
    running. Solvent is pumped in and out of the reservoir only. Once complete, the valve is connected to the flow
    system (opposite direction to the one given here).
    If the pump is already enabled, it is left like that. If not, the pump is temporarily enabled to perform
    the operation.

    Usage:
    call cls.run() to execute this task.
    """

    @classmethod
    def run(
        cls,
        pump: SyringePump,
        flowrate: float | None = None,
        cycles: int = 1,
        fill: bool = True,
        reverse: bool = False,
        reservoir_valve_position: str | SwitchValveSetpointValue | None = None,
    ) -> None:
        """
        Prime a syringe pump into its reservoir by performing several empty-fill cycles.
        This is helpful to remove bubbles from the syringe and recommended after some time the pumps have not been
        running. Solvent is pumped in and out of the reservoir only. Once complete, the valve is connected to the flow
        system (opposite direction to the one given here, or in the configuration file).
        If the pump was not configured with a switchvalve, it will not be manipulated, but the pumping action will
        take place.
        If the pump is already enabled, it is left like that. If not, the pump is temporarily enabled to perform
        the operation.

        @param pump: SyringePumpNrg
            The pump device.
        @param flowrate: float | None = None
            Flowrate to use in mL/min. If None, max pump flowrate is used.
        @param cycles: int = 1
            How many pumping cycles to perform.
        @param fill: bool = True
            If true the priming operation ends with a full syringe, otherwise with an empty one.
        @param reverse: bool = False
            If set to true, the task will be performed in reverse, swapping the valve positions.
            Solvent will be sucked and pushed into the flow system.
        @param reservoir_valve_position: str | SwitchValveSetpointValue | None = None
            The valve position corresponding to the solvent reservoir. This should be 'OFF'.
            The priming will be done in this position.
            If None, the value will be taken from the configuration file.
        """
        return super().run(
            pump=pump,
            flowrate=flowrate,
            cycles=cycles,
            fill=fill,
            reverse=reverse,
            reservoir_valve_position=reservoir_valve_position,
        )

    @classmethod
    def _validate_input(
        cls,
        pump: SyringePump,
        flowrate: float | None = None,
        cycles: int = 1,
        fill: bool = True,
        reverse: bool = False,
        reservoir_valve_position: str | SwitchValveSetpointValue = "OFF",
    ):
        """Validate input arguments."""
        super()._validate_pump(pump)
        cls._validate_input_log(
            cycles,
            lambda x: x > 0,
            "Cycles must be positive.",
        )

    @classmethod
    def _execute_pump(
        cls,
        pump: SyringePump,
        flowrate: float | None = None,
        cycles: int = 1,
        fill: bool = True,
        reverse: bool = False,
        reservoir_valve_position: str | SwitchValveSetpointValue | None = None,
    ) -> None:
        """
        Prime a syringe pump into its reservoir by performing several empty-fill cycles.
        This is helpful to remove bubbles from the syringe and recommended after some time the pumps have not been
        running. Solvent is pumped in and out of the reservoir only. Once complete, the valve is connected to the flow
        system (opposite direction to the one given here, or in the configuration file).
        If the pump was not configured with a switchvalve, it will not be manipulated, but the pumping action will
        take place.
        If the pump is already enabled, it is left like that. If not, the pump is temporarily enabled to perform
        the operation.

        @param pump: SyringePumpNrg
            The pump device.
        @param flowrate: float | None = None
            Flowrate to use in mL/min. If None, max pump flowrate is used.
        @param cycles: int = 1
            How many pumping cycles to perform.
        @param fill: bool = True
            If true the priming operation ends with a full syringe, otherwise with an empty one.
        @param reverse: bool = False
            If set to true, the task will be performed in reverse, swapping the valve positions.
            Solvent will be sucked and pushed into the flow system.
        @param reservoir_valve_position: str | SwitchValveSetpointValue | None = None
            The valve position corresponding to the solvent reservoir. This should be 'OFF'.
            The priming will be done in this position.
            If None, the value will be taken from the configuration file.
        """
        valve_position = None
        if pump.has_valve():
            valve_position = super()._get_reservoir_valve_position(
                pump=pump, reverse=reverse, given_position=reservoir_valve_position
            )

        if flowrate is None:
            flowrate = pump.max_flowrate

        pump["flowrate"] = flowrate
        if pump.has_valve():
            pump["valve_setpoint"] = valve_position

        for i in range(cycles):
            pump["zero"] = "FILL"
            pump["zero"] = "EMPTY"
        if fill:
            pump["zero"] = "FILL"

        if pump.has_valve():
            valve_position.invert()  # Opposite position -> flow system
            pump["valve_setpoint"] = valve_position


class FillPump(PumpTask):
    """
    Refill a pump completely from the solvent reservoir. Pumps a small amount back into the reservoir
    to minimize backlash error. Once complete, the valve is connected to the flow
    system (opposite direction to the one given here).
    If the pump is already enabled, it is left like that. If not, the pump is temporarily enabled to perform
    the operation.

    Usage:
    call cls.run() to execute this task.
    """

    @classmethod
    def run(
        cls,
        pump: SyringePump,
        flowrate: float | None = None,
        fill: bool = True,
        reverse: bool = False,
        reservoir_valve_position: str | SwitchValveSetpointValue | None = None,
    ) -> None:
        """
        Refill a pump completely from the solvent reservoir. Pumps a small amount back into the reservoir
        to minimize backlash error. Once complete, the valve is connected to the flow
        system (opposite direction to the one given here).
        If the pump is already enabled, it is left like that. If not, the pump is temporarily enabled to perform
        the operation.

        @param pump: SyringePump
            The pump device.
        @param flowrate: float | None = None
            Flowrate to use in mL/min. If None, max pump flowrate is used.
        @param fill: bool = True
            If true the syringe is filled, otherwise it is emptied.
        @param reverse: bool = False
            If set to true, the task will be performed in reverse, swapping the valve positions.
            Solvent will be taken from the flow system and pushed into the reservoir.
        @param reservoir_valve_position: str | SwitchValveSetpointValue | None = None
            The valve position corresponding to the solvent reservoir. This should be 'OFF'.
            The priming will be done in this position.
            If None, the value will be taken from the configuration file.
        """
        return super().run(
            pump=pump,
            flowrate=flowrate,
            fill=fill,
            reverse=reverse,
            reservoir_valve_position=reservoir_valve_position,
        )

    @classmethod
    def _validate_input(
        cls,
        pump: SyringePump,
        flowrate: float | None = None,
        fill: bool = True,
        reverse: bool = False,
        reservoir_valve_position: str | SwitchValveSetpointValue | None = None,
    ) -> None:
        """validate input arguments"""
        super()._validate_pump(pump)

    @classmethod
    def _execute_pump(
        cls,
        pump: SyringePump,
        flowrate: float | None = None,
        fill: bool = True,
        reverse: bool = False,
        reservoir_valve_position: str | SwitchValveSetpointValue | None = None,
    ) -> None:
        """
        Refill a pump completely from the solvent reservoir. Pumps a small amount back into the reservoir
        to minimize backlash error. Once complete, the valve is connected to the flow
        system (opposite direction to the one given here).
        If the pump is already enabled, it is left like that. If not, the pump is temporarily enabled to perform
        the operation.

        @param pump: SyringePump
            The pump device.
        @param flowrate: float | None = None
            Flowrate to use in mL/min. If None, max pump flowrate is used.
        @param fill: bool = True
            If true the syringe is filled, otherwise it is emptied.
        @param reverse: bool = False
            If set to true, the task will be performed in reverse, swapping the valve positions.
            Solvent will be taken from the flow system and pushed into the reservoir.
        @param reservoir_valve_position: str | SwitchValveSetpointValue | None = None
            The valve position corresponding to the solvent reservoir. This should be 'OFF'.
            The priming will be done in this position.
            If None, the value will be taken from the configuration file.
        """
        valve_position = None
        if pump.has_valve():
            valve_position = super()._get_reservoir_valve_position(
                pump=pump, reverse=reverse, given_position=reservoir_valve_position
            )

        backlash_direction = 1.0
        zero = "FILL"
        if not fill:
            backlash_direction = -1.0
            zero = "EMPTY"

        if flowrate is None:
            flowrate = pump.max_flowrate
        pump["flowrate"] = flowrate
        if pump.has_valve():
            pump["valve_setpoint"] = valve_position

        pump["zero"] = zero

        # To avoid backlash error, we pump a little (2% of total) back into the reservoir.
        pushback = 0.02
        if pump.syringe_volume > 2000.0:
            pushback = 0.15
        pump["pump"] = pump.syringe_volume * pushback * backlash_direction

        if pump.has_valve():
            valve_position.invert()  # Opposite position -> flow system
            pump["valve_setpoint"] = valve_position


class PumpVolume(PumpTask):
    """
    Pumps a specific amount of solvent at the desired flowrate. By default, this is the entire volume.
    If the pump is already enabled, it is left like that. If not, the pump is temporarily enabled to perform
    the operation.
    Note: When pumping accurate volumes, ensure the pump will not need to be refilled, as this can affect accuracy.

    Usage:
    call cls.run() to execute this task.
    """

    @classmethod
    def stop_asynchronously(
        cls, pump: SyringePump, pumping_event: Event, timeout: float = 120.0
    ) -> None:
        """
        Stop a PumpVolume task from running, regardless of whether it is done.
        The PumpVolume task should be running on a separate thread.
        This will wait for the pump to be actually pumping (it will not stop refilling operations or other), unless
        the timeout is reached.
        Note: This will stop the task tied to the pump given, not any specific task.

        @param pump: SyringePump
            The pump which is currently performing the task.
            This must match what was given to the task .run() method.
        @param pumping_event: Event
            The event signal which is set when the task is pushing volume.
            This must match what was given to the task .run() method.
        @param timeout: float = 120.0
            Timeout if the pump task does not allow stopping after this many seconds.
        @return: None
        @raise: TaskTimeoutError
            If the waiting times out.
        """
        # wait for the pumping event to set. This should happen as soon as the pump is pushing and not refilling.
        error = None
        if not pumping_event.wait(timeout):
            error = TaskTimeoutError(
                f"Timed out while waiting for task '{cls.__name__}' to be able to receive stop signal."
            )
        pump.stop_parameter("pump")
        if error is not None:
            cls.log(error)
            raise error

    @classmethod
    def run(
        cls,
        pump: SyringePump,
        volume: float,
        flowrate: float = 2.0,
        allow_refill: bool = False,
        refill_flowrate: float | None = None,
        reverse: bool = False,
        reservoir_valve_position: str | SwitchValveSetpointValue | None = None,
        pumping_event: Event | None = None,
    ) -> float:
        """
        Pumps a specific amount of solvent at the desired flowrate.
        If the pump is already enabled, it is left like that. If not, the pump is temporarily enabled to perform
        the operation.
        Note: When pumping accurate volumes, ensure the pump will not need to be refilled, as this can affect accuracy.

        @param pump: SyringePump
            The pump device.
        @param volume: float
            Volume to pump in uL. Negative values 'suck' into the syringe, positive push out.
        @param flowrate: float = 2.0
            Flowrate to use in mL/min.
        @param allow_refill: bool = False
            If True, allows pumping volumes larger than what is available in the syringe by refilling the syringe
            as many times as required.
        @param refill_flowrate: float | None = None
            Sets the flowrate to use for the refill operations only in mL/min.
            If None, defaults to pump max rate.
        @param reverse: bool = False
            If set to true, the task will be performed in reverse, swapping the valve positions.
            Solvent will be taken from the flow system and pushed into the reservoir.
        @param reservoir_valve_position: str | SwitchValveSetpointValue | None = None
            The valve position corresponding to the solvent reservoir. This should be 'OFF'.
            The refilling will be done in this position, while the pumping will use the opposite position.
            If None, the value will be taken from the configuration file.
        @param pumping_event: Event | None = None
            This event signals that the pumping operation is in progress (when set). If the pump can refill, this event
            is only set when the pump is in drive and not while it is refilling. When asynchronously stopping this task,
            check this event and send the stop event to the pump only when pumping_event is set. This ensures the real
            pumping operation is stopped and not a refill operation.
        @return: float
            Actual volume pumped in this operation in uL. If the task was stopped asynchronously before pumping the
            amount requested, this value will reflect the actual amount pumped.
        """
        return super().run(
            pump=pump,
            volume=volume,
            flowrate=flowrate,
            allow_refill=allow_refill,
            refill_flowrate=refill_flowrate,
            reverse=reverse,
            reservoir_valve_position=reservoir_valve_position,
            pumping_event=pumping_event,
        )

    @classmethod
    def _validate_input(
        cls,
        pump: SyringePump,
        volume: float,
        flowrate: float = 2.0,
        allow_refill: bool = False,
        refill_flowrate: float | None = None,
        reverse: bool = False,
        reservoir_valve_position: str | SwitchValveSetpointValue | None = None,
        pumping_event: Event | None = None,
    ) -> None:
        """Validate input arguments."""
        super()._validate_pump(pump)
        # volume and flowrate are validated by pump.
        # drive and allow refill are validated by trial.

    @classmethod
    def _execute_pump(
        cls,
        pump: SyringePump,
        volume: float,
        flowrate: float = 2.0,
        allow_refill: bool = False,
        refill_flowrate: float | None = None,
        reverse: bool = False,
        reservoir_valve_position: str | SwitchValveSetpointValue | None = None,
        pumping_event: Event | None = None,
    ) -> float:
        """
        Pumps a specific amount of solvent at the desired flowrate. By default, this is the entire volume.
        If the pump is already enabled, it is left like that. If not, the pump is temporarily enabled to perform
        the operation.
        Note: When pumping accurate volumes, ensure the pump will not need to be refilled, as this can affect accuracy.

        @param pump: SyringePump
            The pump device.
        @param volume: float
            Volume to pump in uL. Negative values 'suck' into the syringe, positive push out.
        @param flowrate: float = 2.0
            Flowrate to use in mL/min.
        @param allow_refill: bool = False
            If True, allows pumping volumes larger than what is available in the syringe by refilling the syringe
            as many times as required.
        @param refill_flowrate: float | None = None
            Sets the flowrate to use for the refill operations only in mL/min.
            If None, defaults to pump max rate.
        @param reverse: bool = False
            If set to true, the task will be performed in reverse, swapping the valve positions.
            Solvent will be taken from the flow system and pushed into the reservoir.
        @param reservoir_valve_position: str | SwitchValveSetpointValue | None = None
            The valve position corresponding to the solvent reservoir. This should be 'OFF'.
            The refilling will be done in this position, while the pumping will use the opposite position.
            If None, the value will be taken from the configuration file.
        @param pumping_event: Event | None = None
            This event signals that the pumping operation is in progress (when set). If the pump can refill, this event
            is only set when the pump is in drive and not while it is refilling. When asynchronously stopping this task,
            check this event and send the stop event to the pump, to ensure the real pumping operation is stopped and
            not a refill operation.
        """
        valve_position = None
        if pump.has_valve():
            valve_position = super()._get_reservoir_valve_position(
                pump=pump, reverse=reverse, given_position=reservoir_valve_position
            )
            valve_position.invert()

        if pumping_event is None:
            pumping_event = Event()
        pump_stop_event = pump.parameter_by_name("pump").stop
        available_volume = pump["volume"]
        total_volume = pump.syringe_volume
        actual_volume_pumped = 0.0

        # Does nothing if volume == 0.0
        if (0.0 < volume <= available_volume) or (
            volume < 0.0 and available_volume - volume <= total_volume
        ):
            # Pump in one go
            pump["flowrate"] = flowrate
            if pump.has_valve():
                pump["valve_setpoint"] = valve_position
            pumping_event.set()
            pump["pump"] = volume
            pumping_event.clear()
            actual_volume_pumped = available_volume - pump["volume"]
        elif allow_refill:
            # Refill and pump as needed
            if not pump.has_valve():
                error = RuntimeError(
                    f"Pump '{pump}' cannot refill (no main switch valve configured)."
                )
                cls.log(error)
                raise error
            reservoir = copy.deepcopy(valve_position)
            reservoir.invert()
            pump["flowrate"] = flowrate
            volume_to_pump = volume
            while abs(volume_to_pump) > 0.1:
                FillPump.run(
                    pump=pump,
                    flowrate=refill_flowrate,
                    fill=volume > 0.0,
                    reservoir_valve_position=reservoir,
                )

                available_volume = pump["volume"]
                if volume > 0.0:
                    pump_volume = min(available_volume, volume_to_pump)
                else:
                    pump_volume = max(available_volume - total_volume, volume_to_pump)
                pump["valve_setpoint"] = valve_position
                pumping_event.set()
                pump["pump"] = pump_volume
                pumping_event.clear()
                actual_volume_pumped += available_volume - pump["volume"]
                volume_to_pump -= pump_volume
                if pump_stop_event.is_set():
                    # If the last pump operation was stopped, do not keep going.
                    volume_to_pump = 0.0
        else:
            error = RuntimeError(
                f"Pump '{pump}' cannot pump {volume} uL without refill (current volume: {available_volume} uL)."
            )
            cls.log(error)
            raise error
        return actual_volume_pumped


class MixSlug(PumpTask):
    """
    Mix a prepared reaction slug in the flow mixing loop.
    Within this section, the slug is moved up and down repeatedly to try and create a homogeneous composition.

    Usage:
    call cls.run() to execute this task.
    """

    @classmethod
    def run(
        cls,
        pump: SyringePump,
        amplitude: float,
        flowrate: float,
        cycles: int,
        push_then_pull: bool = True,
    ) -> None:
        return super().run(
            pump=pump,
            amplitude=amplitude,
            flowrate=flowrate,
            cycles=cycles,
            push_then_pull=push_then_pull,
        )

    @classmethod
    def _validate_input(
        cls,
        pump: SyringePump,
        amplitude: float,
        flowrate: float,
        cycles: int,
        push_then_pull: bool = True,
    ):
        """Validate input arguments."""
        super()._validate_pump(pump)
        cls._validate_input_log(
            cycles,
            lambda x: x >= 0,
            "Cycles must be positive.",
        )
        cls._validate_input_log(
            amplitude,
            lambda x: x > 0,
            "Amplitude must be positive.",
        )

    @classmethod
    def _execute_pump(
        cls,
        pump: SyringePump,
        amplitude: float,
        flowrate: float,
        cycles: int,
        push_then_pull: bool = True,
    ) -> None:
        if cycles == 0:
            return

        available_volume = pump["volume"]
        total_volume = pump.syringe_volume

        if push_then_pull:
            if available_volume <= amplitude:
                FillPump.run(pump=pump)
        else:
            if total_volume - available_volume <= amplitude:
                FillPump.run(pump=pump, fill=False)
            amplitude = -amplitude

        pump["flowrate"] = flowrate
        for i in range(cycles):
            pump["pump"] = amplitude
            pump["pump"] = -amplitude
