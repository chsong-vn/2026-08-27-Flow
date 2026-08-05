"""
File: serial_id.py
Author: Simone Pilon - Noël Research Group - 2023
GitH0ub: https://github.com/simone16

Description: Utilities to memorize and identify serial devices.
"""

import serial
import serial.tools.list_ports
import os.path
import json

from .logger import Logger
from .general import path_to_configuration_folder
from .device_arduino import ArduinoDevice


class KnownDevices:
    """
    Holds information to identify and distinguish different devices connected via serial-through-USB protocol.
    Information about known devices is stored in a json formatted file accessed via the load() and save() methods.
    To configure new devices, run interactive_serial_scan() while the devices are connected (or run for each device),
    to assign them a name and store identifiers.

    Once the devices are configured, simply:
        known_devices = KnownDevices()
        comport = known_devices[device_name]

    Devices are currently identified by serial_number, vid (vendor ID), pid (product ID), manufacturer and custom ID.
    If a device does not specify some of these parameters, the script will only match devices which also do not specify
    the same parameter.
    Custom IDs can be programmed into the devices themselves to distinguish between otherwise identical devices, but the
    matching process is slower as a serial connection must be opened to read the custom ID. Custom IDs should be set
    only when necessary.
    """

    def __init__(self):
        self._default_config_filename = "known_devices.json"
        self._path_to_config = os.path.join(
            path_to_configuration_folder(), self._default_config_filename
        )
        self._known_devices = None  # Loads from known_devices.json
        self._custom_id_cache = {}  # Stores custom_ids between searches to save time
        self.logger = Logger.log_message

    def load(self):
        """Retrieves known devices list from file and stores it in this object."""
        if os.path.isfile(self._path_to_config):
            with open(self._path_to_config, "r") as known_devices_file:
                try:
                    self._known_devices = json.load(known_devices_file)
                except json.JSONDecodeError as e:
                    self.logger(str(e), level="error")
                    self.logger(
                        f"Unable to open known device file '{self._path_to_config}'.",
                        level="error",
                    )
                    self._known_devices = None
        else:
            self.logger(
                f"Could not find configuration file '{self._path_to_config}'.",
                level="warning",
            )
            self._known_devices = None

    def save(self):
        """Overwrites the known devices list with data from this object."""
        if self._known_devices:
            with open(self._path_to_config, "w") as known_devices_file:
                json.dump(self._known_devices, known_devices_file, indent=4)

    def add(self, device_name, device_data):
        """
        Add a new device to the list of known devices in this object.
        Does not directly affect the known devices file.

        @param device_name: str
            This name will be used to identify the device. If name already exists it will be overridden.
        @param device_data: obj
            Object holding device data as returned by serial.tools.list_ports.comports()[n]
        """
        if isinstance(device_name, str):
            if not self._known_devices:
                self._known_devices = {}
            self._known_devices[device_name] = {}
            if device_data.serial_number:
                self._known_devices[device_name][
                    "serial_number"
                ] = device_data.serial_number
            if device_data.vid:
                self._known_devices[device_name]["vid"] = device_data.vid
            if device_data.pid:
                self._known_devices[device_name]["pid"] = device_data.pid
            if device_data.manufacturer:
                self._known_devices[device_name][
                    "manufacturer"
                ] = device_data.manufacturer

    def _add_custom_id(self, device_name, custom_id):
        """
        Add a custom ID to a known device.
        A custom ID can be programmed into devices which lack a serial number, if the firmware is accessible.
        This is used for some arduino boards which do not give a serial number for the serial device, and as a result
        cannot be distinguished based on that.
        Custom IDs are only compared if explicitly set.

        @param device_name: str
            The name used to identify the device. The device must be known.
        @param custom_id: str
            The ID returned by the serial device when prompted with '?'.
        """
        if (
            isinstance(device_name, str)
            and isinstance(custom_id, str)
            and device_name in self._known_devices
        ):
            self._known_devices[device_name]["custom_id"] = custom_id

    def _read_custom_id(self, device_comport):
        """
        Attempts to read the custom ID from the serial device. Serial communication is opened and '?' is sent,
        the response is returned as the ID.
        Note: Attempts to use cached values for custom ids, if you want to force reading custom ids from devices,
        prepend 'self._custom_id_cache = {}'

        @param device_comport: str
            String representing the comport of the device.
        @return: str
            The ID provided by the device or None upon failure.
        """
        if device_comport in self._custom_id_cache.keys():
            return self._custom_id_cache[device_comport]
        arduino_device = None
        try:
            arduino_device = ArduinoDevice()
            arduino_device.open(device_comport)
            arduino_device.flush_buffer_in()
            return arduino_device["ID"]
        except (ValueError, serial.SerialException) as e:
            self.logger(str(e), level="error")
        finally:
            if arduino_device and arduino_device.is_open():
                arduino_device.close()
        return None

    @staticmethod
    def _compare_device_parameter(
        device_data_parameter, known_device, known_device_parameter_key
    ):
        """
        Compares a single parameter of a device with that of a known device.

        @param device_data_parameter: str or int
            The parameter read from serial.tools.list_ports.comports()[n]
        @param known_device: obj
            The object storing the parameters of the known device to compare.
        @param known_device_parameter_key: str
            The key to the corresponding parameter for a device stored in memory.
        @return: True if the parameters match, or are both not available.
        """
        if known_device_parameter_key not in known_device.keys():
            return not bool(device_data_parameter)
        if device_data_parameter:
            return device_data_parameter == known_device[known_device_parameter_key]
        return False

    def _compare(self, device_data, known_device):
        """
        Compares a connected device to a known one.

        @param device_data: obj
            Object holding device data as returned by serial.tools.list_ports.comports()[n]
        @param known_device: dict
            Dictionary of device data as stored in _known_devices.values()
        @return: True is the devices match, False if not. Only available parameters are checked, so false positives
            can occur.
        """
        if not self._compare_device_parameter(
            device_data.serial_number, known_device, "serial_number"
        ):
            return False
        if not self._compare_device_parameter(device_data.vid, known_device, "vid"):
            return False
        if not self._compare_device_parameter(device_data.pid, known_device, "pid"):
            return False
        # Vendor name differs on some computers
        # if not self._compare_device_parameter(
        #     device_data.manufacturer, known_device, "manufacturer"
        # ):
        #     return False
        if (
            "serial_number" not in known_device.keys()
            and "custom_id" in known_device.keys()
        ):
            return known_device["custom_id"] == self._read_custom_id(device_data.device)
        return True

    def _name_of(self, device_data):
        """
        Searches the known devices list for a match with the device data given.

        @param device_data: obj
            Object holding device data as returned by serial.tools.list_ports.comports()[n]
        @return: str The name of the matching device or None if none is found.
        """
        if self._known_devices:
            matching_device_name = None
            for name in self._known_devices.keys():
                if self._compare(device_data, self._known_devices[name]):
                    matching_device_name = name
                    break
            return matching_device_name
        return None

    def __getitem__(self, device_name):
        """
        Searches for a specific device and returns the comport (e.g. 'COM5') if the device is found.

        @param device_name: str
            The name used to identify the known device.
        @return: str
            The comport string identifying the serial port to which the device is connected, or None if not found.
        @raise KeyError:
            Raises an exception if the name is not in the known devices list.
        """
        if not self._known_devices:
            self.load()
            if not self._known_devices:
                raise KeyError("The list of known devices is empty.")
        connected_devices = serial.tools.list_ports.comports()
        if device_name in self._known_devices.keys():
            for connected_device in connected_devices:
                if self._compare(connected_device, self._known_devices[device_name]):
                    return connected_device.device
        else:
            raise KeyError(
                f"The name '{device_name}' is not associated with any known device."
            )
        return None

    def keys(self):
        """
        Returns the names of all known devices.

        @return: dict_keys
            Known devices names
        """
        if not self._known_devices:
            self.load()
        return self._known_devices.keys()

    def interactive_serial_scan(self):
        """
        Scans serial devices and allows to memorize unknown devices interactively via the command line.
        """
        self.load()
        connected_devices = [
            {"data": data, "known": False, "name": ""}
            for data in serial.tools.list_ports.comports()
        ]
        # Search known devices
        self._custom_id_cache = {}
        for connected_device in connected_devices:
            name = self._name_of(connected_device["data"])
            if name:
                connected_device["name"] = name
                connected_device["known"] = True
            else:
                connected_device["name"] = connected_device["data"].description
                connected_device["known"] = False
        # Interactive loop
        while True:
            # List devices
            index = 0
            if connected_devices:
                print("Connected devices:")
            else:
                print("No devices found.")
            for connected_device in connected_devices:
                descriptor = "found  " if connected_device["known"] else "unknown"
                comport = connected_device["data"].device
                name = connected_device["name"]
                print(f"{index}: [{comport}] {descriptor} {name}")
                index += 1
            # Prompt user for action
            user_prompt = input(
                """Type the index of the device to store/change its name.\nType 'exit' to finish: """
            )
            if user_prompt == "exit":
                break
            try:
                device_index = int(user_prompt)
                device_name = input("Name: ")
                if connected_devices[device_index]["known"]:
                    self._known_devices[device_name] = self._known_devices.pop(
                        connected_devices[device_index]["name"]
                    )
                    connected_devices[device_index]["name"] = device_name
                else:
                    self.add(device_name, connected_devices[device_index]["data"])
                    connected_devices[device_index]["known"] = True
                    connected_devices[device_index]["name"] = device_name
                if (
                    "serial_number" not in self._known_devices[device_name].keys()
                    and "custom_id" not in self._known_devices[device_name].keys()
                ):
                    user_prompt = input(
                        "This device has no serial number, do you wish to add a custom ID?\n"
                        "Use this option only if a custom ID was programmed into the device. (y/n):"
                    )
                    if user_prompt in ("y", "yes"):
                        custom_id = self._read_custom_id(
                            connected_devices[device_index]["data"].device
                        )
                        if custom_id:
                            self._add_custom_id(device_name, custom_id)
                            print(f"Added custom ID '{custom_id}'.")
                        else:
                            print("Could not find a custom ID.")
            except (ValueError, IndexError):
                print("Use a valid index or type 'exit'.")
        self.save()
