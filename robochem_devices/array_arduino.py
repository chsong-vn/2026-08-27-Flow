"""
File: array_arduino.py
Author: Simone Pilon - Noël Research Group - 2024
GitHub: https://github.com/simone16

Description: Define some classes and behaviours for Array-type devices. Array devices use a single connection (e.g.: a
    single control unit) to access a series of identical physical devices or sensors.
"""

from .device import DeviceSetupOption
from .device_arduino import ArduinoDevice
from .array import ProxyDevice, ArrayDevice


class ProxyArduinoDevice(ArduinoDevice, ProxyDevice):
    """
    A ProxyDevice acts as a separate device, granting access to a subset of parameters of a parent device. This is
    used for ArrayDevices which can have multiple identical units.
    """

    _parent: "ArrayArduinoDevice"

    def __init__(
        self,
        parent: "ArrayArduinoDevice",
        serial_interface,
        serial_lock,
        array_index: int,
    ):
        ArduinoDevice.__init__(
            self, serial_interface=serial_interface, serial_lock=serial_lock
        )
        ProxyDevice.__init__(self, parent)
        self._array_index = array_index
        self._array_parameter_begin = 10
        self._array_parameter_period = 10

        self.generic_name = "proxy"

    def __del__(self):
        pass

    def base_variable_number(self) -> int:
        """
        Index of the first variable of this proxy device within the array.
        The index of the proxy variable can be calculated as base+variable_index.

        @return: int
             Index of the first sub-device variable.
        """
        return (
            self._array_parameter_begin
            + self._array_parameter_period * self._array_index
        )

    @property
    def array_index(self) -> int:
        """
        Index of this proxy within the array device.

        @return: int
            The index of the subdevice.
        """
        return self._array_index

    def open(self, comport: str) -> None:
        raise NotImplementedError("Cannot open connection from a ProxyDevice.")

    def close(self) -> None:
        raise NotImplementedError("Cannot close connection from a ProxyDevice.")


class ArrayArduinoDevice(ArduinoDevice, ArrayDevice):
    """
    Parent class adding Array-device functionalities to other Device-type classes. An ArrayDevice will return
    subdevices which allow direct access to the parameters of one specific sensor or actuator within the array.
    Note: ArrayDevice does not implement BaseDevice. For a device to use ArrayDevice, it has to inherit from it and from
    a child of BaseDevice (or BaseDevice itself). Inheriting from ArrayDevice only should not be done.
    """

    _proxies: dict[str, ProxyArduinoDevice]

    _proxy_types = {}
    _default_proxy = None

    def __init__(self) -> None:
        ArduinoDevice.__init__(self)
        ArrayDevice.__init__(self)

        array = DeviceSetupOption(name="array", handler=self.setup_array)
        array.value_type = dict
        array.required = True
        array.description = (
            "Array devices must specify a list of subdevices via this setup option."
        )
        self.add_setup_option(array)

    def setup_array(self, subdevices: dict) -> None:
        """
        Setup handler for 'array'.
        The 'array' setup option populates ArrayDevices with their subdevices.

        @param subdevices: dict
            Dict of name: str -> data: dict pairs defining the identifier for each subdevice and its attributes within
            the array. 'index' must be an attribute, optionally, 'class' can be given to specify the proxy class to use
            (must be in _proxy_types).
        """
        for name, data in subdevices.items():
            if not isinstance(name, str):
                error = TypeError(
                    f"Array subdevices identifiers must be of type 'str', not '{type(name)}'."
                )
                error.add_note(self.exception_note())
                raise error
            if name in self._proxies.keys():
                error = ValueError(
                    f"Cannot define two sub-devices with the same name '{name}'."
                )
                error.add_note(self.exception_note())
                raise error
            if "index" not in data.keys():
                error = ValueError("Array sub-device does not name an index.")
                error.add_note(self.exception_note())
                raise error
            index = data["index"]
            if not isinstance(index, int):
                error = TypeError(
                    f"Array subdevices index must be of type 'int', not '{type(name)}'."
                )
                error.add_note(self.exception_note())
                raise error
            if index > self._max_proxies or index < 0:
                error = ValueError(f"Invalid index for '{name}': {index}.")
                error.add_note(self.exception_note())
                raise error
            proxy_type = self._default_proxy
            if "class" in data.keys():
                class_name = data["class"]
                if class_name not in self._proxy_types.keys():
                    error = ValueError(
                        f"Invalid class for sub-device '{name}': {class_name}."
                    )
                    error.add_note(self.exception_note())
                    raise error
                proxy_type = self._proxy_types[class_name]
            if proxy_type is None:
                error = ValueError(f"No class defined for sub-device '{name}'.")
                error.add_note(self.exception_note())
                raise error
            self._add_proxy(name, index, proxy_type)

    def _add_proxy(self, name: str, index: int, proxy_type: type) -> None:
        """
        Creates a new proxy device with the specified identifier and index and adds it to this Array device.

        @param name: str
            Identifier used for the subdevice. Needs to be unique (does not check).
        @param index: int
            An integer index identifying the subdevice within the Arduino device.
        @param proxy_type: type
            The class type to use for the proxy device.
        """
        proxy = proxy_type(
            parent=self,
            serial_interface=self._serial_iface,
            serial_lock=self._serial_thread_lock,
            array_index=index,
        )
        proxy.specific_name = f"{self.specific_name}-{name}"
        self._proxies[name] = proxy

    def initialize(self) -> None:
        """
        By default, Array devices will initialize their proxies.
        """
        ArduinoDevice.initialize(self)
        for proxy in self._proxies.values():
            proxy.initialize()

    def prepare_for_close(self) -> None:
        """
        Ensure the device is in a safe state before closing the connection (and losing control).
        """
        for proxy_name, proxy in self._proxies.items():
            proxy.prepare_for_close()
