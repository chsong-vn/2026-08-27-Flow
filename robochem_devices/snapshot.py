"""
File: snapshot.py
Author: Simone Pilon - Noël Research Group - 2024
GitHub: https://github.com/simone16

Description: Utility to store device status.
"""

from .device import BaseDevice


class Snapshot:
    """
    Take a snapshot of a device parameters to be restored later. Should be used for device settings only: operating the
    device via snapshot could cause problems.
    Note: The snapshot can only be restored to the same device object.
    """

    def __init__(self, device: BaseDevice, parameters: list[str]) -> None:
        """
        Create a snapshot of the device by storing the values of the specified parameters.

        @param device: BaseDevice
            The device whose parameters need to be stored.
        @param parameters: list[str]
            List of parameter names.
        """
        self._device = device
        self.values = {}
        for name in parameters:
            self.values[name] = device[name]

    def restore(self) -> None:
        """
        Restores the linked device to the state of the snapshot.
        Only the parameters explicitly stored are restored.
        """
        for name, value in self.values.items():
            self._device[name] = value
