"""
File: array.py
Author: Simone Pilon - Noël Research Group - 2024
GitHub: https://github.com/simone16

Description: Define interfaces for devices with multiple identical subunits. Each subunit can be treated as a separate
    device as far as parameter access is concerned (ProxyDevice).
    Note: the classes defined here are simple 'interfaces', they do not implement any device communication behaviour.
    This must be inherited from a child class of BaseDevice. Inheriting from these alone is discouraged.
"""


class ProxyDevice:
    """A sub-device relying on a parent device for communication with the physical world."""

    _parent: "ArrayDevice"

    def __init__(self, parent: "ArrayDevice"):
        """
        Constructor.

        @param parent: ArrayDevice
            The parent of this proxydevice.
        """
        self._parent = parent

    @property
    def parent(self) -> "ArrayDevice":
        """
        The parent ArrayDevice of this proxy.
        """
        return self._parent


class ArrayDevice:
    """
    A physical device with multiple identical subunits which can be controlled via proxies.

    Attributes:
        _max_proxies        Maximum number of proxies.
        _proxies            Dictionary of name: str -> ProxyDevice.
    """

    _max_proxies: int
    _proxies: dict[str, ProxyDevice]

    def __init__(self):
        self._max_proxies = 0
        self._proxies = {}

    @property
    def max_proxies(self) -> int:
        """
        Maximum number of proxies allowed by the device type.

        @return: int
            Maximum allowed number of proxies.
        """
        return self._max_proxies

    def proxy(self, proxy_name: str) -> ProxyDevice:
        """
        Return one of the proxy devices within this ArrayDevice.

        @param proxy_name: str
            The identifying name for the ProxyDevice, as given in the device setup stage.
        @return: ProxyDevice
            The proxy device identified by the given name.
        @raise: KeyError
            If the name does not correspond to any ProxyDevice within this ArrayDevice.

        """
        return self._proxies[proxy_name]

    def proxies(self) -> list:
        """
        Return a list of names of the proxies within this array.

        @return: list
            A list of strings each corresponding to a proxy within the array.
        """
        return list(self._proxies)
