"""
File: base_unit_task.py
Author: Simone Pilon, Elia Savino
github: https://github.com/simone16, github.com/EliaSavino

Description: Base structure for all unit tasks.
"""

from typing import Any, Callable
from abc import ABC, abstractmethod
from threading import Thread

from .general import format_float, dict_to_str
from .logger import Logger


class BaseUnitTaskTemplate(ABC):
    """
    BaseUnitTaskTemplate class. All unit tasks should inherit from this class. It sets up the logger and provides a
    callable method that is used to run each function. User should implement the validate_input, execute and cleanup
    methods, that are called in sequence by the .run method. Only method that returns a value is executed, the other two
    are void. Also provides a logger interface to log messages, the logger takes the class to find its own namespace.

    If user wants to add private utility methods, they should be added as private methods, i.e. _utility_method. Please
    do so only if those methods are not be used standalone or by any other task. Otherwise, they should be their own
    unit tasks.

    usage: result = BaseUnitTaskTemplate.run(*args, **kwargs) (also possible to return void)
    """

    @classmethod
    def _validate_input_log(
        cls, obj: Any, condition: type | Callable[[Any], bool], error_message: str
    ) -> None:
        """
        Validates an input object against a given condition. The condition can either be a type (for type checking)
        or a callable that takes the object as an argument and returns a boolean (for custom conditions).

        @param obj: Any
            The object to validate.
        @param condition: type | Callable[[Any], bool]
            The condition to validate against. Can be a type or a callable returning a boolean.
        @param error_message: str
            The error message to log and raise if validation fails.
        @return: None

        @raise: TypeError
            If the condition is a type and the object does not match the type.
        @raise: ValueError
            If the condition is a callable and the object does not meet the condition.
        """
        # If the condition is a type, check isinstance
        if isinstance(condition, type):
            if not isinstance(obj, condition):
                error = TypeError(error_message)
                cls.log(error_message)
                raise error

        # If the condition is a callable, it should return True for valid objects, False otherwise
        elif callable(condition):
            if not condition(obj):
                error = ValueError(error_message)
                cls.log(error_message)
                raise error
        else:
            error_message = "Invalid condition provided for validation."
            error = ValueError(error_message)
            cls.log(error_message)
            raise error

    @classmethod
    @abstractmethod
    def _validate_input(cls, *args, **kwargs) -> None:
        """
        Validate input arguments.
        Must be implemented by subclasses.
        """
        pass

    @classmethod
    @abstractmethod
    def _execute(cls, *args, **kwargs) -> None:
        """
        Execute the main functionality.
        Must be implemented by subclasses.
        """
        pass

    @classmethod
    def _cleanup(cls) -> None:
        """
        Perform cleanup after execution.
        Should be implemented by subclasses.
        """
        pass

    @classmethod
    def run(cls, *args, **kwargs):
        try:
            cls.log(f"Started...", indent="enter")

            cls._validate_input(*args, **kwargs)

            arg_ctr = 0
            for a in args:
                cls._log_arg(a, argument_name=f"[{arg_ctr}]")
                arg_ctr += 1
            for name, a in kwargs.items():
                cls._log_arg(a, argument_name=name)

            result = cls._execute(*args, **kwargs)
            cls._cleanup()

            if result is not None:
                cls._log_arg(result, argument_name="Result", is_result=True)
            cls.log(f"Completed.", indent="exit", level="ok")
            return result
        except Exception as e:
            cls.log(e)
            raise

    @classmethod
    def thread_name(cls, *args, **kwargs) -> str:
        """
        A meaningful name for a thread running this task with the given arguments.
        Note: returned names are not unique and should not be used to identify or access a thread.

        @param args:
            run arguments.
        @param kwargs:
            run keyword arguments.
        @return: str
            A name for a thread running this task.
        """
        return cls.__name__

    @classmethod
    def get_thread(cls, *args, **kwargs) -> Thread:
        """
        A thread calling the run method of this task with the arguments provided.

        @param args:
            run arguments.
        @param kwargs:
            run keyword arguments.
        @return: Thread
            A thread whose target is the run method of this task with the arguments provided.
        """
        return Thread(
            target=cls.run,
            name=cls.thread_name(args, kwargs),
            args=args,
            kwargs=kwargs,
        )

    @classmethod
    def log(cls, message: str | Exception, **kwargs) -> None:
        """
        Log a message.

        @param message: str | Exception
            The message to log, or an exception.
        """
        kwargs["subfolder"] = "tasks"
        if "origin" in kwargs.keys():
            kwargs.pop("origin")
        kwargs["priority"] = 4  # corresponds to medium verbosity
        Logger.log_message(message, origin=cls.__name__, **kwargs)

    @classmethod
    def _log_arg(
        cls,
        argument: Any,
        argument_name: str | None = None,
        is_result: bool = False,
    ) -> None:
        """
        Log an argument of this task (or a result).

        @param argument: Any
            Any argument given to or returned by the task.
        @param argument_name: str | None = None
            Optional name to show for the argument.
        @param is_result: bool = False
            Set to true if the argument is a result of the unit task.
        """
        if isinstance(argument, dict):
            text = dict_to_str(argument)
        elif isinstance(argument, float):
            text = format_float(argument)
        else:
            text = str(argument)
        if argument_name is not None:
            if len(text) > 50 or "\n" in text:
                spacer = ":\n"
            else:
                spacer = ": "
            text = argument_name + spacer + text
        cls.log(text)
