"""
File: phase_sensors.py
Author: Simone Pilon - Noël Research Group - 2024
GitHub: https://github.com/simone16

Description: Tasks related to phase sensors and pumping using phase sensors.
"""

from abc import ABC
from typing import Callable
from enum import Enum
import time
from threading import Thread, Event
from queue import Queue, Empty
import numpy as np
import pandas as pd

from ..errors import DeviceTimeoutError
from ..general import format_float
from ..syringe_pump import SyringePump
from ..gpio import SolenoidValve
from .phase_sensor import PhaseSensor
from ..base_unit_task import BaseUnitTaskTemplate
from ..task_errors import BadSlugQualityError
from ..tasks_pumps import (
    BackgroundPumpTask,
)


class ActionOnImmediatelyDetected(Enum):
    """
    Defines the possible ways Tasks which wait on phase sensors can act in case the phase being awaited is
    detected before any driver is activated.

    IGNORE: Do not check for current phase and await the desired phase change.
    RAISE: Raise an exception if the phase we are supposed to await is already present.
    RETURN: Return immediately if the phase we are supposed to await is already present.
    """

    IGNORE = 0
    RAISE = 1
    RETURN = 2


class WaitForPhaseChange(BaseUnitTaskTemplate, ABC):
    """
    Continuously check the phase detected by a phase sensor until a change is detected.
    Does nothing to cause a phase change by default, but providing start_move and stop_move methods allows
    this task to actively cause the change.
    """

    @classmethod
    def _validate_phase_sensor(cls, phase_sensor: PhaseSensor):
        cls._validate_input_log(
            phase_sensor,
            PhaseSensor,
            f"{cls.__name__} task does not support sensor type '{type(phase_sensor)}'.",
        )

    @classmethod
    def _validate_delay(cls, name, value):
        cls._validate_input_log(
            value,
            lambda x: x > 0.0,
            f"'{name}' must be positive.",
        )

    @classmethod
    def _execute_wait(
        cls,
        phase_sensor: PhaseSensor,
        timeout: float,
        wait_for_gas: bool = True,
        measure_delay: float = 0.1,
        initial_delay: float = 0.2,
        if_already_present: ActionOnImmediatelyDetected = ActionOnImmediatelyDetected.RETURN,
        stop_request: Event | None = None,
        start_move: Callable[[], None] | None = None,
        stop_move: Callable[[], None] | None = None,
    ) -> tuple[bool, bool]:
        """
        Continuously check the phase detected by a phase sensor until a change is detected.
        Does nothing to cause a phase change by default, but providing start_move and stop_move methods allows
        this task to actively cause the change.

        @param phase_sensor: PhaseSensor
            The phase sensor to use for detection.
        @param timeout: float
            Timeout in seconds. If the desired phase is not detected within this time limit, fail.
        @param wait_for_gas: bool
            If true, the valve is kept open until gas is detected, if not, waits for liquid.
        @param measure_delay: float = 0.1
            Delay in seconds between updates.
        @param initial_delay: float = 0.2
            Delay in seconds between start of monitoring and start of moving command.
        @param if_already_present: ActionOnImmediatelyDetected = ActionOnImmediatelyDetected.RETURN
            Action to take in case the phase we should wait for is already present at the start.
        @param stop_request: Event | None = None
            If set, this event stops the task regardless of whether a phase change was detected.
            If a phase change is detected, the event is set.
        @param start_move: Callable[[], None] | None = None
            If given, this function is called to start moving the slug when checking for phase change.
        @param stop_move: Callable[[], None] | None = None
            If given, this function is called to stop moving the slug when checking for phase change.
            If a start method is given, this must be given too.
        @return: tuple[bool, bool]
            Returns
            1. True if the task completed by detecting a phase change, false if it ended by receiving the
            stop_request event.
            2. True if start move was actually called.
        @raise: DeviceTimeoutError
            In case the timeout is reached.
        """
        if stop_request is None:
            stop_request = Event()
        detected_phase_change = False
        started_move = False
        time_left = timeout

        if not if_already_present == ActionOnImmediatelyDetected.IGNORE:
            phase = phase_sensor["phase"]
            if phase.is_liquid() == (not wait_for_gas):
                if if_already_present == ActionOnImmediatelyDetected.RETURN:
                    return True, started_move
                else:
                    name = "gas" if wait_for_gas else "liquid"
                    raise RuntimeError(
                        f"{cls.__name__} detected {name} before drive operation started."
                    )

        try:
            phase_sensor["monitor"] = "once"
            if start_move is not None:
                if stop_request.wait(initial_delay):
                    phase_sensor["monitor"] = "never"
                    return False, started_move
                start_move()
                started_move = True
            while time_left > 0.0:
                if stop_request.wait(measure_delay):
                    break
                time_left -= measure_delay
                phase = phase_sensor.read_event()
                if phase is not None and phase.is_liquid() == (not wait_for_gas):
                    detected_phase_change = True
                    break
        finally:
            if stop_move is not None:
                stop_move()
            phase_sensor["monitor"] = "never"
        if time_left <= 0.0:
            error = DeviceTimeoutError(
                "Waiting for phase change event timed-out.", device=phase_sensor
            )
            phase_sensor.log_exception(error)
            raise error
        return detected_phase_change, started_move


class OpenValveUntilPhaseChange(WaitForPhaseChange):
    """
    Open a gas valve until gas or liquid is detected at the phase sensor, then immediately close the valve.

    Usage:
    call self.run() to execute this task.
    """

    @classmethod
    def _validate_input(
        cls,
        valve: SolenoidValve,
        phase_sensor: PhaseSensor,
        timeout: float,
        wait_for_gas: bool = True,
        measure_delay: float = 0.1,
        initial_delay: float = 0.2,
        if_already_present: ActionOnImmediatelyDetected = ActionOnImmediatelyDetected.RETURN,
    ):
        cls._validate_input_log(
            valve,
            SolenoidValve,
            f"{cls.__name__} task does not support valve type {type(valve)}.",
        )
        cls._validate_phase_sensor(phase_sensor)
        cls._validate_delay("timeout", timeout)
        cls._validate_delay("measure_delay", measure_delay)
        cls._validate_delay("initial_delay", initial_delay)

    @classmethod
    def _open_valve(cls, valve: SolenoidValve) -> None:
        """
        Start pushing material by opening the valve.

        @param valve: SolenoidValve
            The valve to be opened.
        @return: None
        """
        valve["valve"] = "open"

    @classmethod
    def _close_valve(cls, valve: SolenoidValve) -> None:
        """
        Stop pushing material by closing the valve.

        @param valve: SolenoidValve
            The valve to be closed.
        @return: None
        """
        valve["valve"] = "close"

    @classmethod
    def _execute(
        cls,
        valve: SolenoidValve,
        phase_sensor: PhaseSensor,
        timeout: float,
        wait_for_gas: bool = True,
        measure_delay: float = 0.1,
        initial_delay: float = 0.2,
        if_already_present: ActionOnImmediatelyDetected = ActionOnImmediatelyDetected.RETURN,
    ) -> None:
        """
        Open a gas valve until gas or liquid is detected at the phase sensor, then immediately close the valve.

        @param valve: SolenoidValve
            The gas valve which will be opened.
        @param phase_sensor: PhaseSensor
            The phase sensor to use for detection.
        @param timeout: float
            Timeout in seconds. If the desired phase is not detected within this time limit, fail.
        @param wait_for_gas: bool
            If true, the valve is kept open until gas is detected, if not, waits for liquid.
        @param measure_delay: float = 0.1
            Delay in seconds between updates.
        @param initial_delay: float = 0.2
            Delay in seconds between start of monitoring and start of moving command.
        @param if_already_present: ActionOnImmediatelyDetected = ActionOnImmediatelyDetected.RETURN
            Action to take in case the phase we should wait for is already present at the start.
        @raise: DeviceTimeoutError
            In case the timeout is reached.
        """
        start = lambda: cls._open_valve(valve)
        stop = lambda: cls._close_valve(valve)
        stop_event = Event()

        phase_detected, valve_opened = cls._execute_wait(
            phase_sensor=phase_sensor,
            timeout=timeout,
            wait_for_gas=wait_for_gas,
            measure_delay=measure_delay,
            initial_delay=initial_delay,
            if_already_present=if_already_present,
            stop_request=stop_event,
            start_move=start,
            stop_move=stop,
        )

        if not phase_detected:
            raise RuntimeError("Did not detect phase change.")

    @classmethod
    def run(
        cls,
        valve: SolenoidValve,
        phase_sensor: PhaseSensor,
        timeout: float,
        wait_for_gas: bool = True,
        measure_delay: float = 0.1,
        initial_delay: float = 0.2,
        if_already_present: ActionOnImmediatelyDetected = ActionOnImmediatelyDetected.RETURN,
    ) -> None:
        """
        Open a gas valve until gas or liquid is detected at the phase sensor, then immediately close the valve.

        @param valve: SolenoidValve
            The gas valve which will be opened.
        @param phase_sensor: PhaseSensor
            The phase sensor to use for detection.
        @param timeout: float
            Timeout in seconds. If the desired phase is not detected within this time limit, fail.
        @param wait_for_gas: bool
            If true, the valve is kept open until gas is detected, if not, waits for liquid.
        @param measure_delay: float = 0.1
            Delay in seconds between updates.
        @param initial_delay: float = 0.2
            Delay in seconds between start of monitoring and start of moving command.
        @param if_already_present: ActionOnImmediatelyDetected = ActionOnImmediatelyDetected.RETURN
            Action to take in case the phase we should wait for is already present at the start.
        @raise: DeviceTimeoutError
            In case the timeout is reached.
        """
        return super().run(
            valve=valve,
            phase_sensor=phase_sensor,
            timeout=timeout,
            wait_for_gas=wait_for_gas,
            measure_delay=measure_delay,
            initial_delay=initial_delay,
            if_already_present=if_already_present,
        )


class PumpUntilPhaseChange(WaitForPhaseChange, BackgroundPumpTask):
    """
    Pump with a syringe until gas or liquid is detected at the phase sensor, then immediately stop.

    Usage:
    call self.run() to execute this task.
    """

    @classmethod
    def _validate_input(
        cls,
        pump: SyringePump,
        phase_sensor: PhaseSensor,
        flowrate: float,
        max_volume: float,
        timeout: float | None = None,
        wait_for_gas: bool = True,
        positive_pump_direction: bool = True,
        drive_valve_position: str = "ON",
        measure_delay: float = 0.1,
        initial_delay: float = 0.2,
        if_already_present: ActionOnImmediatelyDetected = ActionOnImmediatelyDetected.RETURN,
    ):
        cls._validate_input_log(
            pump,
            SyringePump,
            f"{cls.__name__} task does not support pump type '{type(pump)}'.",
        )
        cls._validate_phase_sensor(phase_sensor)
        if timeout is not None:
            cls._validate_delay("timeout", timeout)
        cls._validate_delay("max_volume", max_volume)
        cls._validate_delay("measure_delay", measure_delay)
        cls._validate_delay("initial_delay", initial_delay)

    @classmethod
    def _execute(
        cls,
        pump: SyringePump,
        phase_sensor: PhaseSensor,
        flowrate: float,
        max_volume: float,
        timeout: float | None = None,
        wait_for_gas: bool = True,
        positive_pump_direction: bool = True,
        drive_valve_position: str = "ON",
        measure_delay: float = 0.1,
        initial_delay: float = 0.2,
        if_already_present: ActionOnImmediatelyDetected = ActionOnImmediatelyDetected.RETURN,
    ) -> float | None:
        pump_is_done = Event()
        pump_is_pushing = Event()
        volume = max_volume if positive_pump_direction else -max_volume
        if timeout is None:
            # here we do not consider refill times and acceleration of the pump, so a generous margin must be given.
            timeout = max_volume / flowrate * 60.0 / 1000.0 * 2.5

        actual_pumped_volume = Queue()
        pumping_target_kwargs = {
            "pump": pump,
            "volume": volume,
            "flowrate": flowrate,
            "done_event": pump_is_done,
            "pumping_event": pump_is_pushing,
            "actual_volume_queue": actual_pumped_volume,
        }
        pumping_thread = Thread(
            target=cls._pumping_thread_target,
            name=f"{cls.__name__}-pumping_thread",
            kwargs=pumping_target_kwargs,
            daemon=True,
        )

        start = lambda: cls._start_pumping(pumping_thread)
        stop = lambda: cls._stop_pumping(pump=pump, pumping_event=pump_is_pushing)

        try:
            phase_detected, pump_started = cls._execute_wait(
                phase_sensor=phase_sensor,
                timeout=timeout,
                wait_for_gas=wait_for_gas,
                measure_delay=measure_delay,
                initial_delay=initial_delay,
                if_already_present=if_already_present,
                stop_request=pump_is_done,
                start_move=start,
                stop_move=stop,
            )
        finally:
            # ensure pumping thread is finished
            if pumping_thread.is_alive():
                pumping_thread.join(timeout=5.0)
                # if after 5 seconds it is still going, send multiple stop()
                while pumping_thread.is_alive():
                    stop()
                    pumping_thread.join(timeout=10.0)
            # clear the pump operation stop condition
            pump.stop_parameter("pump", stop=False)

        if not phase_detected:
            raise DeviceTimeoutError(
                "Reached the maximum pump volume before phase change event."
            )

        if not pump_started:
            return 0.0
        try:
            return actual_pumped_volume.get(block=False)
        except Empty:
            return None

    @classmethod
    def run(
        cls,
        pump: SyringePump,
        phase_sensor: PhaseSensor,
        flowrate: float,
        max_volume: float,
        timeout: float | None = None,
        wait_for_gas: bool = True,
        positive_pump_direction: bool = True,
        drive_valve_position: str = "ON",
        measure_delay: float = 0.1,
        initial_delay: float = 0.2,
        if_already_present: ActionOnImmediatelyDetected = ActionOnImmediatelyDetected.RETURN,
    ) -> float | None:
        """
        Pump with a syringe pump until gas or liquid is detected at the phase sensor, then immediately stop.

        @param pump: SyringePump
            The pump which will be activated.
        @param phase_sensor: PhaseSensor
            The phase sensor to use for detection.
        @param flowrate: float
            Flowrate for the pump in mL/min.
        @param max_volume: float
            Volume in uL. If the desired phase is not detected after pumping this volume, fail.
            If none is specified, pump as much as the syringe can in one go.
        @param timeout: float | None = None
            Timeout in seconds. If the desired phase is not detected within this time limit, fail.
            By default this is calculated based on flowrate and volume.
        @param wait_for_gas: bool = True
            If true, the valve is kept open until gas is detected, if not, waits for liquid.
        @param positive_pump_direction: bool = True
            If true, the pump will push solvent out. If not, it will reverse direction.
        @param drive_valve_position: str = "ON"
            The position for the main valve of the pump.
        @param measure_delay: float = 0.1
            Delay in seconds between updates.
        @param initial_delay: float = 0.2
            Delay in seconds between start of monitoring and start of moving command.
        @param if_already_present: ActionOnImmediatelyDetected = ActionOnImmediatelyDetected.RETURN
            Action to take in case the phase we should wait for is already present at the start.
        @return: float | None
            Return the pumped volume in uL, or None if there was an issue with that.
        @raise: DeviceTimeoutError
            In case the timeout or the maximum volume is reached.
        """
        return super().run(
            pump=pump,
            phase_sensor=phase_sensor,
            flowrate=flowrate,
            max_volume=max_volume,
            timeout=timeout,
            wait_for_gas=wait_for_gas,
            positive_pump_direction=positive_pump_direction,
            drive_valve_position=drive_valve_position,
            measure_delay=measure_delay,
            initial_delay=initial_delay,
            if_already_present=if_already_present,
        )


class MonitorPhase(BaseUnitTaskTemplate):
    @classmethod
    def run(
        cls,
        phase_sensor: PhaseSensor,
        delta_t: float,
        stop_event: Event,
    ) -> pd.DataFrame:
        """
        Continuously monitor a phase sensor and produce a table with timing and phases.

        @param phase_sensor: PhaseSensor
            The phase sensor that will be used to read data over the slug.
        @param delta_t: float
            Delay in S between datapoints.
        @return: pandas.DataFrame
                Columns:
                    Phase: int
                        0 -> gas
                        1 -> liquid
                    Time: float
                        time in S since start of task.

        """
        return super().run(
            phase_sensor=phase_sensor, delta_t=delta_t, stop_event=stop_event
        )

    @classmethod
    def _validate_input(
        cls,
        phase_sensor: PhaseSensor,
        delta_t: float,
        stop_event: Event,
    ) -> None:
        cls._validate_input_log(
            phase_sensor,
            PhaseSensor,
            f"{cls.__name__} task does not support sensor type '{type(phase_sensor)}'.",
        )
        cls._validate_input_log(
            delta_t,
            lambda x: x > 0,
            f"Time delay must be positive ({delta_t} <= 0).",
        )
        cls._validate_input_log(
            stop_event, Event, f"Stop event must be an event, not '{type(stop_event)}'"
        )

    @classmethod
    def _execute(
        cls,
        phase_sensor: PhaseSensor,
        delta_t: float,
        stop_event: Event,
    ) -> pd.DataFrame:
        # store the information here, nan = not detected yet.
        # phase_data = [[phase, timing],...] 1->liquid
        phase_data = []
        start_time = time.time()
        phase_data.append([int(phase_sensor["phase"].is_liquid()), 0.0])
        while not stop_event.wait(delta_t):
            phase_data.append(
                [int(phase_sensor["phase"].is_liquid()), time.time() - start_time]
            )

        # We have acquired all data, now we reduce the datapoints:
        reduced_data = pd.DataFrame({"Phase": [], "Time": []})
        active_phase = -1
        for phase, timing in phase_data:
            if not phase == active_phase:
                reduced_data.loc[len(reduced_data.index)] = [
                    phase,
                    timing,
                ]
                active_phase = phase

        return reduced_data


class SlugQuality:
    slug_volume: float | None
    slug_volume_deviation: float | None
    liquid_volume: float
    gas_volume: float
    volumes: pd.DataFrame
    error_description: str | None

    def __init__(
        self,
        liquid_volume: float,
        gas_volume: float,
        volumes: pd.DataFrame,
        slug_volume: float | None = None,
        slug_volume_deviation: float | None = None,
        error_description: str | None = None,
    ):
        self.slug_volume = slug_volume
        self.slug_volume_deviation = slug_volume_deviation
        self.gas_volume = gas_volume
        self.liquid_volume = liquid_volume
        self.volumes = volumes
        self.error_description = error_description

    @property
    def ok(self) -> bool:
        return self.error_description is None

    def raise_exception(self):
        if self.error_description is not None:
            raise BadSlugQualityError(self.error_description)

    def __str__(self):
        msg = ""
        if self.slug_volume is not None:
            msg += "Slug volume: " + format_float(self.slug_volume) + "\n"
            msg += (
                "Slug volume deviation: "
                + format_float(self.slug_volume_deviation)
                + "\n"
            )
        msg += "Liquid volume: " + format_float(self.liquid_volume) + "\n"
        msg += "Gas volume: " + format_float(self.gas_volume) + "\n"
        msg += "Volumes:\n" + self.volumes.to_string() + "\n"
        if self.error_description is not None:
            msg += "Error: " + self.error_description + "\n"
        return msg


class SlugQualityCheck(BackgroundPumpTask):
    @classmethod
    def run(
        cls,
        pump: SyringePump,
        phase_sensor: PhaseSensor,
        flowrate: float,
        pump_volume: float,
        expected_slug_volume: float | None = None,
        max_volume_deviation: float = 0.05,
        volume_step: float = 5.0,
    ) -> SlugQuality:
        """
        Pass a volume through a phase sensor to analyze the geometry of the slug.
        The data is collected and analyzed, returning a slug descriptor object.

        @param pump: SyringePump
            The pump which will be used to push the slug through the phase sensor.
        @param phase_sensor: PhaseSensor
            The phase sensor that will be used to read data over the slug.
        @param flowrate: float
            The flowrate for pushing the slug.
        @param pump_volume: float
            The total volume to push, this should be larger than the slug volume [uL].
        @param expected_slug_volume: float | None = None
            The detected slug is matched against this desired volume [uL].
        @param max_volume_deviation: float = 0.05
            Maximum allowed deviation from required slug volume as a fraction of the slug volume itself.
        @param volume_step: float = 5.0
            Phase data will be acquired in intervals of this volume [uL].
        @return: SlugQuality
            Slug descriptors.
        """
        return super().run(
            pump=pump,
            phase_sensor=phase_sensor,
            flowrate=flowrate,
            pump_volume=pump_volume,
            expected_slug_volume=expected_slug_volume,
            max_volume_deviation=max_volume_deviation,
            volume_step=volume_step,
        )

    @classmethod
    def _validate_input(
        cls,
        pump: SyringePump,
        phase_sensor: PhaseSensor,
        flowrate: float,
        pump_volume: float,
        expected_slug_volume: float | None = None,
        max_volume_deviation: float = 0.05,
        volume_step: float = 5.0,
    ) -> None:
        cls._validate_input_log(
            phase_sensor,
            PhaseSensor,
            f"{cls.__name__} task does not support sensor type '{type(phase_sensor)}'.",
        )

    @classmethod
    def _extract_droplets(cls, raw_phase_data: np.array) -> pd.DataFrame:
        """
        Convert the raw phase data array into droplets data.

        @param raw_phase_data: numpy.array
            Array of [phase, displacement] pairs.
        @return: pandas.DataFrame
            Dataframe of 'Phase' and 'Volume' columns specifying the sequence of droplets and gas bubbles and their
            volume.
        """
        droplets = pd.DataFrame({"Phase": [], "Volume": []})
        active_phase, volume_begin = raw_phase_data[0]
        volume_end = volume_begin
        for phase, displacement in raw_phase_data:
            if not np.isnan(displacement):
                volume_end = displacement
            if not phase == active_phase:
                droplets.loc[len(droplets.index)] = [
                    active_phase,
                    volume_end - volume_begin,
                ]
                volume_begin = displacement
                active_phase = phase
            if any(np.isnan((phase, displacement))):
                break
        return droplets

    @classmethod
    def _get_descriptors(
        cls,
        droplets: pd.DataFrame,
        expected_slug_volume: float | None = None,
        max_deviation: float = 0.05,
    ) -> SlugQuality:
        """
        Analyse the droplets sequence and generate descriptors for it.

        @param droplets: pandas.DataFrame
            Dataframe of 'Phase' and 'Volume' columns specifying the sequence of droplets and gas bubbles and their
            volume.
        @return: SlugQuality
            Slug descriptors.
        """
        # Init result
        descriptors = SlugQuality(
            liquid_volume=droplets.loc[droplets["Phase"] == 1, "Volume"].sum(),
            gas_volume=droplets.loc[droplets["Phase"] == 0, "Volume"].sum(),
            volumes=droplets,
        )

        if expected_slug_volume is not None:
            liquid_droplets = droplets.copy()
            liquid_droplets.reset_index(inplace=True, drop=True)
            if len(liquid_droplets) <= 1:
                descriptors.error_description = "Not enough phase changes detected."
                descriptors.slug_volume = 0.0
                descriptors.slug_volume_deviation = 1.0
                return descriptors
            # Drop start and end liquid droplets (we do not know the real volume of these droplets, as it could be larger)
            if liquid_droplets.loc[droplets.index.min(), "Phase"] == 1:
                liquid_droplets.drop(liquid_droplets.index.min(), inplace=True)
            if liquid_droplets.loc[droplets.index.max(), "Phase"] == 1:
                liquid_droplets.drop(liquid_droplets.index.max(), inplace=True)
            # remove gas
            liquid_droplets.drop(
                liquid_droplets[liquid_droplets["Phase"] == 0].index, inplace=True
            )
            # calculate deviation from target volume
            liquid_droplets["delta"] = (
                liquid_droplets["Volume"] - expected_slug_volume
            ).abs()
            # chose best based on min deviation
            best_droplet = liquid_droplets.loc[
                (liquid_droplets["delta"] == liquid_droplets["delta"].min())
            ]
            volume = best_droplet["Volume"].item()
            deviation = best_droplet["delta"].item() / volume
            descriptors.slug_volume = volume
            descriptors.slug_volume_deviation = deviation

            if deviation > max_deviation:
                descriptors.error_description = (
                    f"Slug volume does not match requirements ({format_float(volume)} uL,"
                    f" should be {format_float(expected_slug_volume)} uL)."
                )

        return descriptors

    @classmethod
    def _execute(
        cls,
        pump: SyringePump,
        phase_sensor: PhaseSensor,
        flowrate: float,
        pump_volume: float,
        expected_slug_volume: float | None = None,
        max_volume_deviation: float = 0.05,
        volume_step: float = 5.0,
    ) -> SlugQuality:
        pump_is_done = Event()
        pump_is_pushing = Event()
        # here we do not consider refill times and acceleration of the pump, so a generous margin must be given.
        timeout = pump_volume / flowrate * 60.0 / 1000.0 * 2.5
        pumping_target_kwargs = {
            "pump": pump,
            "volume": pump_volume,
            "flowrate": flowrate,
            "done_event": pump_is_done,
            "pumping_event": pump_is_pushing,
        }
        pumping_thread = Thread(
            target=cls._pumping_thread_target,
            name=f"{cls.__name__}-pumping_thread",
            kwargs=pumping_target_kwargs,
            daemon=True,
        )
        stop = lambda: cls._stop_pumping(pump=pump, pumping_event=pump_is_pushing)

        delay = (volume_step * 60) / (1000.0 * flowrate)
        # store the information here, nan = not detected yet.
        # slug_phase = [[phase, volumetric displacement],...] 1->liquid
        slug_phase = np.zeros([int(2 * pump_volume / volume_step), 2])
        slug_phase.fill(np.nan)
        try:
            slug_index = 0
            start_time = time.time()
            cls._start_pumping(pumping_thread)
            while timeout > 0.0:
                if pump_is_done.wait(delay):
                    break
                try:
                    slug_phase[slug_index][0] = int(
                        phase_sensor["phase"].is_liquid()
                    )  # phase (1=liquid)
                    slug_phase[slug_index][1] = (
                        (time.time() - start_time) * 1000.0 * flowrate / 60.0
                    )  # displacement [uL]
                except IndexError as error:
                    error.add_note("Exceeded array size while analyzing the slug.")
                    raise
                slug_index += 1
                timeout -= delay
        finally:
            # ensure pumping thread is finished
            if pumping_thread.is_alive():
                pumping_thread.join(timeout=5.0)
                # if after 5 seconds it is still going, send multiple stop()
                while pumping_thread.is_alive():
                    stop()
                    pumping_thread.join(timeout=10.0)
            # clear the pump operation stop condition
            pump.stop_parameter("pump", stop=False)

        if timeout <= 0.0:
            error = DeviceTimeoutError(
                "Timed-out while analysing the slug.", device=pump
            )
            cls.log(error)
            raise error

        # We have acquired all data, now we process:
        droplets = cls._extract_droplets(slug_phase)
        descriptors = cls._get_descriptors(
            droplets, expected_slug_volume, max_volume_deviation
        )

        return descriptors
