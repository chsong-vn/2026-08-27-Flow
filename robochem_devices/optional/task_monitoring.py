"""
File: monitoring.py
Author: Simone Pilon - Noël Research Group - 2024
GitHub: https://github.com/simone16

Description: Unit tasks to monitor the platform status.
"""

import copy
from typing import Any, Callable
from collections import deque
from threading import Event, Lock

from ..device import BaseDevice
from ..array import ProxyDevice
from ..base_unit_task import BaseUnitTaskTemplate


class MonitorEventTrigger:
    """
    A condition which is continuously checked by the Monitoring task.
    When the condition is true, the linked event is set.
    """

    _access_lock: Lock
    _condition: Callable[[deque], bool]
    _on_true: Event

    def __init__(
        self,
        condition: Callable[[deque], bool],
        on_true: Event,
    ):
        """
        Constructor.

        @param condition: Callable[[deque], bool]
            A function which is given the values and returns either True or False.
            Function arguments are:
                - a deque object holding the last n values.
            If the function returns True, the event is set. The event is not cleared (must be cleared by user).
        @param on_true: Event
            This event will be set as the condition evaluates as True.
        """
        self._access_lock = Lock()
        # make a copy of the condition to prevent it being modified by the user without locking.
        self._condition = copy.deepcopy(condition)
        self._on_true = on_true

    @classmethod
    def has_been_stable(
        cls, stored_values: deque, target: Any, max_deviation: Any, n_values: int
    ) -> bool:
        """
        Condition for stability check.

        @param stored_values: deque
            List of the values stored.
        @param target: Any
            Target value.
        @param max_deviation: Any
            Max deviation allowed from the target value.
        @param n_values: int
            Number of datapoints to consider, assuming the history goes back long enough.
        @return: bool
            An object of this class which performs the check described.
        """
        stored_values = list(stored_values)
        if len(stored_values) > n_values:
            stored_values = stored_values[-n_values:]
        return all(
            [abs(value - target) <= max_deviation * 2 for value in stored_values]
        )

    @classmethod
    def stable_within_range(
        cls, target: Any, max_deviation: Any, n_values: int, on_true: Event
    ) -> "MonitorEventTrigger":
        """
        Factory constructor for MonitorEventTrigger which checks if a value has been stable and within range for the
        last n datapoints.

        @param target: Any
            Target value.
        @param max_deviation: Any
            Max deviation allowed from the target value.
        @param n_values: int
            Number of datapoints to consider, assuming the history goes back long enough.
        @param on_true: Event
            The event which will be triggered by the condition.
        @return: MonitorEventTrigger
            An object of this class which performs the check described.
        """
        return MonitorEventTrigger(
            condition=lambda values: cls.has_been_stable(
                stored_values=values,
                target=target,
                max_deviation=max_deviation,
                n_values=n_values,
            ),
            on_true=on_true,
        )

    def update_condition(self, new_condition: Any) -> None:
        """
        Change the reference value for the condition being checked.

        @param new_condition: Any
            The new value for the reference.
        """
        with self._access_lock:
            self._condition = copy.deepcopy(new_condition)

    def check_condition(self, recent_values: deque):
        """
        Perform the condition check on the values provided.

        @param recent_values: deque
            List of the most recent values.
        """
        with self._access_lock:
            if self._condition(recent_values):
                self._on_true.set()


class MonitoredValue:
    """
    A value to monitor continuously on a device.
    """

    device: BaseDevice
    parameter_name: str
    checks: list[MonitorEventTrigger]
    stored_values: deque

    def __init__(
        self,
        device: BaseDevice | ProxyDevice,
        parameter_name: str,
        checks: list[MonitorEventTrigger] | None = None,
        store_values: int = 10,
    ):
        """
        Constructor.

        @param device: BaseDevice | ProxyDevice
            The device which makes the parameter available.
        @param parameter_name: str
            The name of the parameter on the device.
        @param checks: list[MonitorEventTrigger] | None = None
            List of checks to continuously perform on the value.
        @param store_values: int
            Number of the most recent values which will be stored, before being overridden.
            These are the values passed to the checks.
        """
        self.device = device
        self.parameter_name = parameter_name
        self.checks = checks if checks is not None else list()
        self.stored_values = deque(maxlen=store_values)

    def update(self):
        """
        Read monitored value from device.
        Update storage history for the value.
        Perform all checks and set events as needed.
        """
        value = self.device[self.parameter_name]
        self.stored_values.append(value)
        for check in self.checks:
            check.check_condition(self.stored_values)


class ContinuousMonitoring(BaseUnitTaskTemplate):
    """
    Task to continuously monitor the platform and reactor status.
    The task is supposed to be run on a dedicated thread.

    Call cls.get_thread and start it.
    """

    @classmethod
    def run(
        cls,
        values_to_monitor: list[MonitoredValue],
        stop_event: Event,
        update_delay: float = 30.0,
    ) -> None:
        """
        Keep monitoring values and checking certain conditions on them.

        @param values_to_monitor: list[MonitoredValue]
            A list of values to monitor, specifying the device to be monitored and
            any checks to continuously carry out, as well as events to set based on the results.
        @param stop_event: Event
            Set this to stop the task.
        @param update_delay: float = 30.0
            Delay in S between updates of each value.
        """
        return super().run(
            values_to_monitor=values_to_monitor,
            stop_event=stop_event,
            update_delay=update_delay,
        )

    @classmethod
    def _validate_input(
        cls,
        values_to_monitor: list[MonitoredValue],
        stop_event: Event,
        update_delay: float = 30.0,
    ) -> None:
        cls._validate_input_log(
            update_delay,
            lambda x: x > 0.0,
            "'update_delay' must be positive.",
        )

    @classmethod
    def _execute(
        cls,
        values_to_monitor: list[MonitoredValue],
        stop_event: Event,
        update_delay: float = 30.0,
    ) -> None:
        # allow to start on an empty list, but do not keep the cpu busy in this case.
        if len(values_to_monitor) == 0:
            return
        # endless cycle is stopped by stop_event.
        while not stop_event.wait(update_delay):
            for monitored in values_to_monitor:
                try:
                    monitored.update()
                except Exception as error:
                    cls.log(error, level="warning")
