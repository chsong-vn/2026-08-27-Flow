"""
File: liquid_handler_moving.py
Author: Simone Pilon
github: https://github.com/simone16

Description: Unit tasks for simple sampler motion using the NRG Sampler and Pumps.
"""

from abc import ABC

from omniplatypus.devices.nrg.sampler import GrblPosition, Sampler
from omniplatypus.procedures.unit_tasks.base_unit_task import BaseUnitTaskTemplate
from omniplatypus.devices.snapshot import Snapshot


class SamplerTask(BaseUnitTaskTemplate, ABC):
    """
    Base prototype for tasks involving liquid handlers.
    """

    @classmethod
    def _validate_sampler(cls, sampler: Sampler):
        """Validate the input sampler."""
        cls._validate_input_log(
            sampler, Sampler, "sampler argument must be a Sampler object."
        )

    @classmethod
    def _set_travel_position(
        cls, sampler: Sampler, retract_speed: float | None = None
    ) -> None:
        """
        Ensure the sampler is in the proper position to initiate a horizontal travel move.

        @param sampler: Sampler
            The sampler which will be moved.
        @param retract_speed: float | None = None
            Vertical travel motion speed in mm/min. If None uses default of sampler.
        """
        if retract_speed is None:
            position = sampler["position"]
            if sampler.travel_position.z < position.z:
                retract_speed = 500.0
            else:
                retract_speed = sampler.default_feedrate_z

        sampler["feed"] = retract_speed
        sampler["move"] = sampler.travel_position
        sampler["aux_needle"] = "OFF"

    @classmethod
    def _same_xy(
        cls, start: GrblPosition, end: GrblPosition, tolerance: float = 0.5
    ) -> bool:
        """
        Check if the destination differs from the current position horizontally (only x and y coordinates).
        Note: start must specify both x and y, end does not need to: no coordinate for end means the same value as start
        is used, but no coordinate for start does not mean anything.

        @param start: GrblPosition
            Starting position. Must specify both x and y. Typically set to sampler["position"].
        @param end: GrblPosition
            Destination. Any unspecified coordinate will be treated as a 0 relative move.
        @param tolerance: float = 0.5
            Maximum difference for each coordinate to result in a different location, in mm.
        @return: bool
            Returns true if the indicated positions are approximately the same.
        """
        if start.x is None or start.y is None:
            error = ValueError(
                f"Invalid starting point '{start}'. Both X and Y must be given for start."
            )
            cls.log(error)
            raise error
        delta_x = 0.0 if end.x is None else end.x - start.x
        delta_y = 0.0 if end.y is None else end.y - start.y
        return (abs(delta_x) < tolerance) and (abs(delta_y) < tolerance)

    @classmethod
    def _same_z(
        cls, start: GrblPosition, end: GrblPosition, tolerance: float = 0.5
    ) -> bool:
        """
        Check if the destination differs from the current position vertically (only z coordinate).
        Note: start must specify z, end does not need to: no coordinate for end means the same value as start
        is used, but no coordinate for start does not mean anything.

        @param start: GrblPosition
            Starting position. Must specify both z. Typically set to sampler["position"].
        @param end: GrblPosition
            Destination. Any unspecified coordinate will be treated as a 0 relative move.
        @param tolerance: float = 0.5
            Maximum difference for each coordinate to result in a different location, in mm.
        @return: bool
            Returns true if the indicated positions are approximately the same.
        """
        if start.z is None:
            error = ValueError(
                f"Invalid starting point '{start}'. Z value must be given for start."
            )
            cls.log(error)
            raise error
        delta_z = 0.0 if end.z is None else end.z - start.z
        return abs(delta_z) < tolerance

    @classmethod
    def _move_xy(
        cls,
        sampler: Sampler,
        destination: GrblPosition,
        move_speed: float | None = None,
        retract_speed: float | None = None,
        tolerance: float = 0.5,
    ) -> None:
        """
        Move the sampler horizontally.
        Ensures travel position is achieved before commencing motion.
        Checks whether the move is required and does nothing if sampler is already in position within given tolerance.

        @param sampler: Sampler
            The sampler operating the motion.
        @param destination: GrblPosition
            The position the sample should move to. Z coordinate is ignored.
        @param move_speed: float | None = None
            Horizontal travel velocity in mm/min. If None uses default of sampler.
        @param retract_speed: float | None = None
            Vertical travel velocity in mm/min. If None uses default of sampler.
        @param tolerance: float = 0.5
            Maximum difference for each coordinate to result in a different location, in mm.
        """
        if move_speed is None:
            move_speed = sampler.default_feedrate_xy

        position = sampler["position"]
        if not cls._same_xy(position, destination, tolerance=tolerance):
            destination_xy = GrblPosition(x=destination.x, y=destination.y)
            cls._set_travel_position(sampler=sampler, retract_speed=retract_speed)
            sampler["feed"] = move_speed
            sampler["move"] = destination_xy

    @classmethod
    def _move_z(
        cls,
        sampler: Sampler,
        destination: GrblPosition,
        plunge_speed: float = 500.0,
        tolerance: float = 0.5,
    ) -> None:
        """
        Move the sampler vertically.
        Checks whether the move is required and does nothing if sampler is already in position within given tolerance.

        @param sampler: Sampler
            The sampler operating the motion.
        @param destination: GrblPosition
            The position the sample should move to. X and Y coordinates are ignored.
        @param plunge_speed: float = 500.0
            Vertical travel velocity in mm/min.
        @param tolerance: float = 0.5
            Maximum difference for each coordinate to result in a different location, in mm.
        """
        position = sampler["position"]
        if not cls._same_z(position, destination, tolerance=tolerance):
            destination_z = GrblPosition(z=destination.z)
            sampler["feed"] = plunge_speed
            sampler["move"] = destination_z

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
        sampler_name = str(kwargs.get("sampler", ""))
        if not sampler_name:
            sampler_name = kwargs.get("sampler_name", "")
        if sampler_name:
            pump_name = f" ({sampler_name})"
        return cls.__name__ + sampler_name


class HomeSampler(SamplerTask):
    """
    Returns the sampler head to the home position. Allows easier access to the sample tray and ensures accurate sampler
    positioning in case steps were lost.

    Usage:
    call cls.run() to execute this task.
    """

    @classmethod
    def run(
        cls,
        sampler: Sampler,
        move_speed: float | None = None,
        retract_speed: float | None = None,
        margin: float = 5.0,
    ) -> None:
        """
        Returns the sampler head to the home position. Allows easier access to the sample tray and ensures accurate sampler
        positioning in case steps were lost.

        @param sampler: Sampler
            The sampler which should move.
        @param retract_speed: float | None = None
            The needle is moved vertically at this speed [mm/min]. If None uses default of sampler.
        @param move_speed: float | None = None
            The sampler moves at this speed [mm/min]. If None uses default of sampler.
        @param margin: float = 5.0
            The sampler will quickly move this far from the zero positions before performing a careful zeroing [mm].
        """
        return super().run(
            sampler=sampler,
            move_speed=move_speed,
            retract_speed=retract_speed,
            margin=margin,
        )

    @classmethod
    def _validate_input(
        cls,
        sampler: Sampler,
        move_speed: float | None = None,
        retract_speed: float | None = None,
        margin: float = 5.0,
    ) -> None:
        cls._validate_sampler(sampler)

    @classmethod
    def _execute(
        cls,
        sampler: Sampler,
        move_speed: float | None = None,
        retract_speed: float | None = None,
        margin: float = 5.0,
    ) -> None:
        # This prevents double zeroing at the start of the experiment if this is called in the cleanup (as it should).
        if not sampler.moved_since_zero:
            return

        if retract_speed is None:
            retract_speed = sampler.default_feedrate_z
        if move_speed is None:
            move_speed = sampler.default_feedrate_xy

        sampler_initial_state = Snapshot(device=sampler, parameters=["feed"])
        cls._set_travel_position(sampler=sampler, retract_speed=retract_speed)
        near_zero_z = GrblPosition(z=-abs(margin))
        near_zero_xy = GrblPosition(x=abs(margin), y=abs(margin))
        sampler["feed"] = retract_speed
        sampler["move"] = near_zero_z
        sampler["feed"] = move_speed
        sampler["move"] = near_zero_xy
        sampler["home"] = "RUN"
        sampler_initial_state.restore()
