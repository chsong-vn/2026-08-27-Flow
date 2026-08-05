"""
File: liquid_handler_sampling.py
Author: Simone Pilon, Elia Savino
github: https://github.com/simone16, github.com/EliaSavino

Description: Unit tasks for sample handling and preparation using the NRG Sampler and Pumps.
    Most of these tasks work with a pandas.DataFrame holding sample information over every vial in the system.
"""

import copy
import math
import numpy
import numpy as np
import pandas as pd
import re
from time import sleep
from abc import ABC
from typing import Any
from scipy.optimize import linprog

from omniplatypus.devices.nrg.syringe_pump import SyringePump
from omniplatypus.devices.nrg.sampler import (
    GrblPosition,
    Sampler,
    VialHolder,
    DummySampler,
)
from omniplatypus.devices.knauer.autosampler_alias import (
    AutosamplerAlias,
    AliasVialHolder,
)
from omniplatypus.devices.platform import Platform, SampleHolder
from omniplatypus.utilities.general import format_float
from omniplatypus.devices.snapshot import Snapshot
from omniplatypus.procedures.unit_tasks.user_actions import UserSetSamplesRequest
from omniplatypus.procedures.unit_tasks.errors import NoSuitableVialError, RecipeError
from omniplatypus.procedures.unit_tasks.sampling.liquid_handler_moving import (
    SamplerTask,
)
from omniplatypus.procedures.unit_tasks.driving.driving_pumps import (
    PumpVolume,
    FillPump,
    MixSlug,
)


class DataFrameTask(SamplerTask, ABC):
    """
    Base prototype for tasks involving pandas DataFrames.

    Attributes:
        index_name: str
            Name of the index column of the DataFrame.
        required_columns: set[str]
            Set of column names which must be in the dataframe.
    """

    index_name: str = "VialID"
    vial_name: str = "VialName"
    units_concentration: str = "mM"
    units_volume: str = "uL"
    required_columns: set = set()
    created_columns: set = set()

    @classmethod
    def _validate_dataframe(cls, samples: pd.DataFrame, sample_id: str | None = None):
        """Validate the input dataframe."""
        cls._validate_input_log(
            samples, pd.DataFrame, "samples must be a pandas DataFrame."
        )
        cls._validate_input_log(
            samples,
            lambda x: x.index.name == cls.index_name,
            f"samples DataFrame must use '{cls.index_name}' column as index"
            f" (use samples.set_index('{cls.index_name}', inplace=True, verify_integrity=True)).",
        )
        cls._validate_input_log(
            samples,
            lambda x: x.index.is_unique,
            f"samples DataFrame index '{cls.index_name}' must be unique"
            f" (use samples.set_index('{cls.index_name}', inplace=True, verify_integrity=True))",
        )
        cls._validate_input_log(
            samples,
            lambda x: cls.required_columns.issubset(x.columns),
            "Samples dataframe is missing the following required columns:"
            f"{cls.required_columns - set(samples.columns)}.",
        )
        if sample_id is not None:
            cls._validate_input_log(
                sample_id,
                lambda x: x in samples.index,
                f"Vial '{sample_id}' not found in samples Dataframe.",
            )

    @classmethod
    def _validate_platform(cls, platform: Platform):
        """Validate the input platform."""
        cls._validate_input_log(
            platform, Platform, "platform argument must be a Platform object."
        )

    @classmethod
    def _get_sampler_pump(
        cls, platform: Platform, sampler: Sampler
    ) -> SyringePump | None:
        """
        Get the pump connected to the sampler.

        @param platform: Platform
            The platform running the experiment.
        @param sampler: Sampler
            The sampler to which the pump is connected.
        @return: SyringePump | None
            The pump connected to the sampler, or None if not available.
        """
        if sampler.sampling_pump_name is not None:
            try:
                # noinspection PyTypeChecker
                pump: SyringePump = platform[sampler.sampling_pump_name]
            except (KeyError, AttributeError):
                return None
            return pump
        return None

    @classmethod
    def _get_sampler(
        cls, platform: Platform, sample_id: str
    ) -> Sampler | AutosamplerAlias:
        """
        Get Sampler to manipulate the given vial.

        @param platform: Platform
            The platform running the experiment.
        @param sample_id: str
            The index of the vial / sample we need to manipulate.
        @return: Sampler | AutosamplerAlias
            The sampler that the vial belongs to.
        """
        # noinspection PyTypeChecker
        sampler: Sampler = platform[platform.samples.loc[sample_id, "Sampler"]]
        pump = cls._get_sampler_pump(platform, sampler)
        return sampler

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
        if isinstance(argument, pd.DataFrame):
            columns = cls.required_columns
            if is_result:
                columns = columns.union(cls.created_columns)
            if len(columns) > 0:
                text = argument[list(columns)].to_string()
            else:
                text = "Unaltered DataFrame."
            argument = text
        super()._log_arg(
            argument=argument, argument_name=argument_name, is_result=is_result
        )


class GetVialInfo(DataFrameTask):
    """
    Get a human-readable string indicating name and location of the vial.
    This is handy to help humans identify a vial quickly and easily instead of providing the vial_id.
    """

    required_columns: set = {"VialName", "Sampler", "Holder", "Position"}

    @classmethod
    def run(cls, vial_id: str, samples: pd.DataFrame) -> str:
        """
        Get a human-readable string indicating name and location of the vial.
        This is handy to help humans identify a vial quickly and easily instead of providing the vial_id.

        @param vial_id: str
            The unique identifier of the vial, as found in the samples dataframe.
        @param samples: pandas.DataFrame
            The reference dataframe containing information about all vials.
        @return: str
            Human-readable string indicating the name and location of the vial in the form
            'name @[sampler, holder, position]'.
        """
        return super().run(vial_id=vial_id, samples=samples)

    @classmethod
    def _validate_input(cls, vial_id: str, samples: pd.DataFrame):
        cls._validate_dataframe(samples)

    @classmethod
    def _execute(cls, vial_id: str, samples: pd.DataFrame) -> str:
        try:
            name, sampler, holder, position = samples.loc[
                vial_id, ["VialName", "Sampler", "Holder", "Position"]
            ]
            return f"{name} @[{sampler}, {holder}, {position}]"
        except KeyError:
            return f"{vial_id} [not found in dataframe]"


class UpdateSampleViability(DataFrameTask):
    """
    Updates the value of the 'Viable' column, establishing which vials can or cannot be used based on their type.
    If the 'Viable' column is missing, it is added.

    for samples of type: [Solvent, Stock] the volume has to be > Volume_min
    for samples of type: [Sample] the volume has to be 0 (viability will be immediately set to False once
    the sample has been visited (see in dispense sample)
    for samples of type: [Waste, Cleaning] the volume has to be < Volume_max
    for samples of type: [Gas, Mixing] always viable

    Usage:
    call cls.run() to execute this task.
    """

    required_columns: set = {"Type", "Volume", "Volume_min", "Volume_max"}
    created_columns: set = {"Viable"}

    @classmethod
    def run(cls, samples_to_update: pd.DataFrame) -> None:
        """
        Updates the value of the 'Viable' column, establishing which vials can or cannot be used based on their type.
        If the 'Viable' column is missing, it is added.

        for samples of type: [Solvent, Stock] the volume has to be > Volume_min
        for samples of type: [Sample] the volume has to be 0 (viability will be immediately set to False once
        the sample has been visited (see in dispense sample)
        for samples of type: [Waste, Cleaning] the volume has to be < Volume_max
        for samples of type: [Gas, Mixing] always viable

        :param samples_to_update: pd.DataFrame
            The samples whose viability should be updated.
            To update only a selection of rows, use a view of the dataframe, e.g.: samples.loc[[sample_id]]
        """
        return super().run(samples_to_update=samples_to_update)

    @classmethod
    def _validate_input(cls, samples_to_update: pd.DataFrame):
        cls._validate_dataframe(samples_to_update)

    @classmethod
    def _execute(cls, samples_to_update: pd.DataFrame) -> None:
        """
        Updates the value of the 'Viable' column, establishing which vials can or cannot be used based on their type.
        If the 'Viable' column is missing, it is added.

        for samples of type: [Solvent, Stock] the volume has to be > Volume_min
        for samples of type: [Sample] the volume has to be 0 (viability will be immediately set to False once
        the sample has been visited (see in dispense sample)
        for samples of type: [Waste, Cleaning] the volume has to be < Volume_max
        for samples of type: [Gas, Mixing] always viable

        :param samples_to_update: pd.DataFrame
            The samples whose viability should be updated.
            To update only a selection of rows, use a view of the dataframe, e.g.: samples.loc[[sample_id]]
        """
        samples_to_update["Viable"] = (
            (
                (
                    samples_to_update["Type"].isin(
                        ["Solvent", "Stock", "Cleaning", "CleaningAgent"]
                    )
                )
                & (samples_to_update["Volume"] >= samples_to_update["Volume_min"])
            )
            | (
                (samples_to_update["Type"] == "Sample")
                & (np.isclose(samples_to_update["Volume"], 0.0, atol=1e-3))
            )
            | (
                (samples_to_update["Type"] == "Waste")
                & (samples_to_update["Volume"] <= samples_to_update["Volume_max"])
            )
            | (samples_to_update["Type"].isin(["Gas", "Mixing"]))
        )


class GenerateSampleDataframe(DataFrameTask):
    """
    Generates a Pandas.DataFrame holding information on all samples and vials for a campaign.
    The information is generated primarily based on an input dataframe (see below), integrating specific information
    gathered from a platform object. The platform must be already built.
    The input dataframe is expected to use 'VialID' as index.
    The output dataframe is stored in the platform.
    The output dataframes can be fed back into this task, some values will be preserved (such as Counter and Viable, in
    some cases), while the overridden_columns will not.

    Example dataframes:
    Input:
              VialName  Conc_A  Volume    Type      Sampler    Holder Position
    VialID
    001       N2           0.0     0.0     Gas  Sampler_cnc  holder_B       A1
    002       Waste_1      0.0     0.0   Waste  Sampler_cnc  holder_F       A1
    003       Waste_2      0.0     0.0   Waste  Sampler_cnc  holder_F       A2
    004       Waste_3      0.0     0.0   Waste  Sampler_cnc  holder_F       A3
    005       Sample_1     0.0     0.0  Sample  Sampler_cnc  holder_C       A1
    006       Sample_2     0.0     0.0  Sample  Sampler_cnc  holder_C       A2
    007       Sample_3     0.0     0.0  Sample  Sampler_cnc  holder_C       A3
    008       Sample_4     0.0     0.0  Sample  Sampler_cnc  holder_C       A4

    Output (stored in platform):
              VialName  Conc_A  Volume    Type      Sampler    Holder Position      X       Y     T  Z_bottom  Z_top  Volume_min  Volume_max  Viable  Counter
    VialID
    001       N2           0.0     0.0     Gas  Sampler_cnc  holder_B       A1   8.50    4.50     0     -80.5  -43.5       100.0      3800.0    True        0
    002       Waste_1      0.0     0.0   Waste  Sampler_cnc  holder_F       A1  12.25  188.50     0      81.0  -43.5       500.0      9500.0    True        0
    003       Waste_2      0.0     0.0   Waste  Sampler_cnc  holder_F       A2  12.25  218.50     0      81.0  -43.5       500.0      9500.0    True        0
    004       Waste_3      0.0     0.0   Waste  Sampler_cnc  holder_F       A3  12.25  248.50     0      81.0  -43.5       500.0      9500.0    True        0
    005       Sample_1     0.0     0.0  Sample  Sampler_cnc  holder_C       A1  98.75   94.85     0      80.5  -43.5       100.0      3800.0    True        0
    006       Sample_2     0.0     0.0  Sample  Sampler_cnc  holder_C       A2  98.75  117.35     0      80.5  -43.5       100.0      3800.0    True        0
    007       Sample_3     0.0     0.0  Sample  Sampler_cnc  holder_C       A3  98.75  139.85     0      80.5  -43.5       100.0      3800.0    True        0
    008       Sample_4     0.0     0.0  Sample  Sampler_cnc  holder_C       A4  98.75  162.35     0      80.5  -43.5       100.0      3800.0    True        0

    Vial types:
        Solvent:
            Solvent stock for diluting solutions and creating slugs.
        Stock:
            Stock solution of reagents in known concentration.
        Sample:
            Vial for storing reaction crudes and samples for later analysis.
        Waste:
            Waste vial for cleaning operations.
        Cleaning:
            Rinsing vials with solvent to clean the needle.
        Gas:
            Empty vial connected to a gas outlet.
        Mixing:
            Empty vial used for slug mixing. To avoid contamination, this vial is kept empty.
        CleaningAgent:
            Solvent to clean the reactor at the end of the run (optional).

    Viable:
        Boolean indicating whether the vial can be considered usable for the task (given by type).

    Counter:
        Incremental counter indicating how many times the vial was used. Usage corresponds to piercing of the septum.

    Conc_xyz:
        These columns indicate the concentration of species 'xyz' for each vial in mM.

    Usage:
    call cls.run() to execute this task.
    """

    required_columns: set = {DataFrameTask.vial_name, "Sampler", "Holder", "Position"}
    created_columns: set = {
        "X",
        "Y",
        "T",
        "Z_bottom",
        "Z_top",
        "Volume_min",
        "Volume_max",
        "Viable",
        "Counter",
    }
    overridden_columns: set = {
        "X",
        "Y",
        "T",
        "Z_bottom",
        "Z_top",
        "Volume_min",
        "Volume_max",
    }
    _sample_name_pattern = re.compile(r"(?P<row>\D+)(?P<col>\d+)")

    @classmethod
    def run(
        cls,
        platform: Platform,
        samples: pd.DataFrame,
    ) -> None:
        """
        Generates a Pandas.DataFrame holding information on all samples and vials for a campaign.
        The information is generated primarily based on an input dataframe (see below), integrating specific information
        gathered from a platform object. The platform must be already built.
        The input dataframe is expected to use 'VialID' as index.
        The output dataframe is stored in the platform.

        @param platform: Platform
            The platform running the campaign.
        @param samples: pandas.DataFrame
            The samples input dataframe.
        """
        return super().run(platform=platform, samples=samples)

    @classmethod
    def _validate_input(cls, platform: Platform, samples: pd.DataFrame):
        cls._validate_dataframe(samples)
        cls._validate_platform(platform)
        cls._validate_input_log(
            platform.sample_holders,
            lambda x: len(x) > 0,
            "No sample holder information found in platform. Make sure platform is built.",
        )

    @classmethod
    def vial_parameters(
        cls, platform: Platform, sampler_name: str, holder_name: str, position_name: str
    ) -> dict[str, float]:
        """
        Calculates the coordinates of a sample within a liquid handler.
        X and Y refer to the sample position, Z to the position the needle should move to reach the lowest position
        within the vial.

        @param platform: Platform
            The platform holding information about the liquid handler and vial holders.
        @param sampler_name: str
            Name of the Sampler within the platform (the same as used for Platform['name']).
        @param holder_name: str
            Name of the holder within the sampler, as specified in the platform configuration file.
        @param position_name: str
            Name of the sample position within the holder (e.g. 'A1' or 'B3').
        @return: dict[str, float]
            A dictionary containing the x and y coordinates of the vial, as well as the lowest and highest z values
            allowed for piercing the vial septum and extract or add liquid.
            The dictionary keys are:
                'X': X vial position
                'Y' Y vial position
                'Z_bottom' Z vial position to reach the bottom of the vial
                'Z_top' Z vial position to just punch the septum
                'Volume_min' in mL
                'Volume_max' in mL
        """
        cls._validate_input_log(
            sampler_name,
            lambda x: x in platform.keys(),
            f"Sampler '{sampler_name}' not found.",
        )
        # noinspection PyTypeChecker
        sampler: Sampler = platform[sampler_name]

        if isinstance(sampler, Sampler):
            return cls._sub_vial_parameters_nrg(
                platform=platform,
                sampler=sampler,
                holder_name=holder_name,
                position_name=position_name,
            )
        elif isinstance(sampler, AutosamplerAlias):
            return cls._sub_vial_parameters_alias(
                platform=platform,
                sampler=sampler,
                holder_name=holder_name,
                position_name=position_name,
            )
        else:
            error = ValueError(f"Unknown sampler type: '{type(sampler)}'.")
            cls.log(error)
            raise error

    @classmethod
    def _sub_vial_parameters_alias(
        cls,
        platform: Platform,
        sampler: AutosamplerAlias,
        holder_name: str,
        position_name: str,
    ) -> dict[str, float]:
        """
        Subroutine for vial_parameters for alias sampler

        :param platform: Platform
            The platform holding information about the liquid handler and vial holders.
        :param sampler: the alias sampler object
        :param holder_name: str
            Name of the holder within the sampler, as specified in the platform configuration file.
        :param position_name: str
            Name of the sample position within the holder (e.g. 'A1' or 'B3').

        """
        # noinspection PyTypeChecker
        sample_holder: AliasVialHolder | None = sampler.locations.get(holder_name, None)
        if sample_holder is None:
            error = ValueError(
                f"Holder '{holder_name}' not found for sampler '{sampler}'."
            )
            cls.log(error)
            raise error
        # noinspection PyTypeChecker
        sample_holder_type: SampleHolder = platform.sample_holders.get(
            sample_holder.type, None
        )
        if sample_holder_type is None:
            error = ValueError(f"Unknown holder type: '{sample_holder.type}'.")
            cls.log(error)
            raise error
        # convert the vial name to a row and column (knowing that A1 is 11)
        match holder_name:
            case "holder_left":
                T = "L"
            case "holder_right":
                T = "R"
            case _:
                raise ValueError(f"Unknown holder type: '{holder_name}'.")
        result = {
            "X": ord(position_name[0].upper()) - ord("A") + 1,
            "Y": int(position_name[1:]),
            "T": T,
            "Volume_min": sample_holder_type.min_volume,
            "Volume_max": sample_holder_type.max_volume,
            "Z_bottom": 0.0,
            "Z_top": 0.0,
        }

        return result

    @classmethod
    def _sub_vial_parameters_nrg(
        cls, platform: Platform, sampler: Sampler, holder_name: str, position_name: str
    ) -> dict[str, float]:
        """
        Subroutine for vial_parameters for NRG sampler

        @param platform: Platform
            The platform holding information about the liquid handler and vial holders.
        @param sampler: str
            The sampler object
        @param holder_name: str
            Name of the holder within the sampler, as specified in the platform configuration file.
        @param position_name: str
            Name of the sample position within the holder (e.g. 'A1' or 'B3').
        @return: dict[str, float]
            A dictionary containing the x and y coordinates of the vial, as well as the lowest and highest z values
            allowed for piercing the vial septum and extract or add liquid.
            The dictionary keys are:
                'X': X vial position
                'Y' Y vial position
                'Z_bottom' Z vial position to reach the bottom of the vial
                'Z_top' Z vial position to just punch the septum
                'Volume_min' in uL
                'Volume_max' in uL
        """

        holder: VialHolder | None = sampler.locations.get(holder_name, None)
        if holder is None:
            error = ValueError(
                f"Holder '{holder_name}' not found for sampler '{sampler}'."
            )
            error.add_note(f"Error occurred while processing vial {position_name}.")
            cls.log(error)
            raise error

        sample_match = cls._sample_name_pattern.match(position_name)
        if sample_match is None:
            error_message = f"Invalid vial position: '{position_name}'."
            cls.log(error_message)
            error = ValueError(error_message)
            cls.log(error)
            raise error
        row_label, col_label = sample_match.groups()

        sample_holder: SampleHolder | None = platform.sample_holders.get(
            holder.shape, None
        )
        if sample_holder is None:
            error = ValueError(f"Unknown holder type: '{holder.shape}'.")
            cls.log(error)
            raise error

        row_xyz = sample_holder.components.get(row_label, None)
        if row_xyz is None:
            error = ValueError(
                f"Invalid row '{row_label}' for holder type '{holder.shape}' (vial position: '{position_name}')."
            )
            cls.log(error)
            raise error
        col_xyz = sample_holder.components.get(col_label, None)
        if col_xyz is None:
            error = ValueError(
                f"Invalid column '{col_label}' for holder type"
                f" '{holder.shape}' (vial position: '{position_name}')."
            )
            cls.log(error)
            raise error

        row_xyz = numpy.array(row_xyz + [0.0])
        col_xyz = numpy.array(col_xyz + [0.0])
        holder_xyz = numpy.array(holder.position.value)
        vial_xyz = holder_xyz + row_xyz + col_xyz

        result = {
            "X": vial_xyz[0],
            "Y": vial_xyz[1],
            "Z_bottom": vial_xyz[2],
            "Z_top": vial_xyz[2],
            "Volume_min": sample_holder.min_volume,
            "Volume_max": sample_holder.max_volume,
        }
        result["Z_bottom"] -= sample_holder.max_depth
        result["Z_top"] -= sample_holder.min_depth

        # noinspection PyUnresolvedReferences
        abs_min_z = sampler.parameter_by_name("move").min_value.z
        if result["Z_top"] < abs_min_z:
            error = ValueError(
                f"Vial '{holder_name}' > '{position_name}':"
                f" sampler cannot reach septum ({result['Z_top']} but lowest is {abs_min_z})."
            )
            cls.log(error)
            raise error
        if result["Z_bottom"] < abs_min_z:
            result["Z_bottom"] = abs_min_z
            cls.log(
                f"Vial '{holder_name}' > '{position_name}': sampler cannot reach bottom.",
                level="warning",
            )
        return result

    @classmethod
    def _validate_output(cls, platform: Platform, samples: pd.DataFrame):
        """
        Minimize the chances of a failure while the experiments are being run by checking the values provided in the
        sample dataframe.

        @param platform: Platform
            The platform holding information about the liquid handler and vial holders.
        @param samples: pd.DataFrame
            The output samples dataframe to validate.
        """
        for index, row in samples.iterrows():
            sampler = platform[row["Sampler"]]
            required_fields = {
                Sampler.__name__: [
                    "X",
                    "Y",
                    "Z_bottom",
                    "Z_top",
                    "Volume_min",
                    "Volume_max",
                ],
                DummySampler.__name__: [
                    "X",
                    "Y",
                    "Z_bottom",
                    "Z_top",
                    "Volume_min",
                    "Volume_max",
                ],
                AutosamplerAlias.__name__: ["X", "Y", "T", "Volume_min", "Volume_max"],
            }
            if not all(
                [row[x] is not None for x in required_fields[type(sampler).__name__]]
            ):
                error = ValueError("Some None values in the dataframe!")
                cls.log(error)
                raise error
            if isinstance(sampler, Sampler):
                parameter = sampler.parameter_by_name("move")
                pos = GrblPosition(x=row["X"], y=row["Y"], z=row["Z_top"])
                value, good_top = parameter.validate(pos)
                pos.z = row["Z_bottom"]
                value, good_bot = parameter.validate(pos)
                if not (good_top and good_bot):
                    error = ValueError(
                        f"Sample '{GetVialInfo.run(index, samples)}' cannot be reached!"
                    )
                    cls.log(error)
                    raise error

    @classmethod
    def _execute(
        cls,
        platform: Platform,
        samples: pd.DataFrame,
    ) -> None:
        # Calculate vial position in machine terms.
        sample_named_coords = samples[["Sampler", "Holder", "Position"]]
        sample_xyz_coords = []
        for index, row in sample_named_coords.iterrows():
            vial_parameters = cls.vial_parameters(
                platform=platform,
                sampler_name=row["Sampler"],
                holder_name=row["Holder"],
                position_name=row["Position"],
            )
            vial_parameters[cls.index_name] = index
            sample_xyz_coords.append(vial_parameters)
        sample_xyz_coords = pd.DataFrame(sample_xyz_coords)
        sample_xyz_coords.set_index(cls.index_name, inplace=True, verify_integrity=True)

        # The input samples dataframe may have existing 'X', 'Y' ecc... columns. These will be overridden.
        # So they must be dropped before merging.
        parameter_cols = set(sample_xyz_coords.columns)
        overlapping_cols = set(samples).intersection(parameter_cols)
        samples.drop(list(overlapping_cols), axis=1, inplace=True)
        samples_with_parameters = pd.merge(
            left=samples,
            right=sample_xyz_coords,
            left_index=True,
            right_index=True,
        )

        # Calculate viability
        UpdateSampleViability.run(samples_to_update=samples_with_parameters)

        # pandas complains if volumes are initially set as ints.
        samples_with_parameters["Volume"] = samples_with_parameters["Volume"].astype(
            np.dtype("float")
        )

        # Add septum counter
        # todo do not overwrite if there was a value
        samples_with_parameters["Counter"] = 0
        cls._validate_output(platform=platform, samples=samples_with_parameters)

        # Update the platform samples
        platform.samples = samples_with_parameters


class FindVial(DataFrameTask):
    """
    Find a vial based on type and distance from current Sampler position.
    Handy to select one vial when multiple equivalent vials are available (e.g. for Waste).
    Optional parameter ensure_volume can be used to ensure the vial is suitable for the planned pumping operation.

    Usage:
    call cls.run() to execute this task.
    """

    required_columns: set = {"Sampler", "Viable", "Type", "X", "Y"}

    @classmethod
    def run(
        cls,
        platform: Platform,
        sampler: Sampler,
        vial_type: str,
        nominal_volume: float | None = None,
        ensure_volume: float | None = None,
        order_by_distance: bool = True,
        from_sample: str | None = None,
        allow_user_requests: bool = True,
    ) -> str:
        """
        Find the closest, suitable vial of the given type.

        @param platform: Platform
            The platform object.
        @param sampler: Sampler
            The Sampler device in question.
        @param vial_type: str
            The type of vial to filter for.
            See 'GenerateSampleDataframe' for a list of available types.
        @param nominal_volume: float | None = None
            Nominal volume required for the vial. This corresponds to the Volume_max
            attribute in the sample dataframe.
            By default, any vial is considered.
        @param ensure_volume: float | None = None
            Volume which will be added (positive) or removed (negative) from the vial.
            Only vials which match the volume requirements will be considered.
            If None (default), all Viable vials are considered.
        @param order_by_distance: bool = True
            When multiple candidate vials are available choose the nearest one.
            If False, the vial is chosen based on its index within the samples dataframe (lower index first).
        @param from_sample: str | None = None
            VialID of the sample for which the closest match should be found.
            If None, current sampler position is used to find nearest match.
        @param allow_user_requests: bool = True
            If no suitable vial is found, the function will attempt to request the user to add a suitable vial via
            the platform. Setting this to false will generate an exception instead (as if no user requester was set).
        @return: str
            The VialID of the best candidate.
        @raise: NoSuitableVialError
            If no suitable vial is found.
        """
        return super().run(
            platform=platform,
            sampler=sampler,
            vial_type=vial_type,
            nominal_volume=nominal_volume,
            ensure_volume=ensure_volume,
            order_by_distance=order_by_distance,
            from_sample=from_sample,
            allow_user_requests=allow_user_requests,
        )

    @classmethod
    def _validate_input(
        cls,
        platform: Platform,
        sampler: Sampler,
        vial_type: str,
        nominal_volume: float | None = None,
        ensure_volume: float | None = None,
        order_by_distance: bool = True,
        from_sample: str | None = None,
        allow_user_requests: bool = True,
    ):
        cls._validate_dataframe(platform.samples)
        cls._validate_platform(platform)
        cls._validate_sampler(sampler)

    @classmethod
    def _execute(
        cls,
        platform: Platform,
        sampler: Sampler,
        vial_type: str,
        nominal_volume: float | None = None,
        ensure_volume: float | None = None,
        order_by_distance: bool = True,
        from_sample: str | None = None,
        allow_user_requests: bool = True,
    ) -> str:
        sampler_name = None
        for name, item in platform.devices.items():
            if item is sampler:
                sampler_name = name
                break
        if sampler_name is None:
            error = ValueError(f"Sampler '{sampler}' not found in platform {platform}.")
            cls.log(error)
            raise error

        # the infinite loop is in case we can request the user to take action when this fails.
        # the user gets infinite tries at fixing the issue.
        while True:
            try:
                viable = platform.samples[
                    (platform.samples["Viable"])
                    & (platform.samples["Sampler"] == sampler_name)
                    & (platform.samples["Type"] == vial_type)
                ]
                if len(viable.index) == 0:
                    error = NoSuitableVialError(
                        f"Sampler '{sampler}' has no viable vials of this type.",
                        vial_type=vial_type,
                    )
                    cls.log(error)
                    raise error

                if nominal_volume is not None:
                    viable = viable[
                        (nominal_volume * 0.8 <= viable["Volume_max"])
                        & (viable["Volume_max"] <= nominal_volume * 1.2)
                    ]
                if len(viable.index) == 0:
                    error = NoSuitableVialError(
                        f"Sampler '{sampler}' has no viable vials of this type"
                        f" with the requested nominal volume ({nominal_volume} uL).",
                        vial_type=vial_type,
                    )
                    cls.log(error)
                    raise error

                viable = viable.copy()
                if ensure_volume is not None:
                    viable["Volume"] += ensure_volume
                    if vial_type == "Sample":
                        if ensure_volume > 0.0:
                            viable = viable[(viable["Volume"] <= viable["Volume_max"])]
                        else:
                            viable = viable[(viable["Volume"] >= viable["Volume_min"])]
                    else:
                        UpdateSampleViability.run(viable)
                        viable = viable[(viable["Viable"])]
                if len(viable.index) == 0:
                    error = NoSuitableVialError(
                        f"Sampler '{sampler}' has no viable vials of this type"
                        f" with sufficient volume (requested to add {ensure_volume} uL).",
                        vial_type=vial_type,
                    )
                    cls.log(error)
                    raise error

                if order_by_distance:
                    if from_sample is None:
                        position = sampler["position"]
                    else:
                        position = GrblPosition()
                        position.x = platform.samples.loc[from_sample, "X"]
                        position.y = platform.samples.loc[from_sample, "Y"]
                    viable["Distance"] = (
                        (viable["X"] - position.x) ** 2
                        + (viable["Y"] - position.y) ** 2
                    ) ** 0.5
                    vial_id: str = viable[
                        viable["Distance"].eq(viable["Distance"].min())
                    ].index[0]
                    if vial_id is not None:
                        return vial_id
                else:
                    return viable.index[0]
            except NoSuitableVialError as error:
                if allow_user_requests and platform.user_action_requester is not None:
                    # Ask user for solution.
                    message = (
                        f"The platform is missing a '{vial_type}' vial on sampler '{sampler}'"
                        + " to be able to conclude the operation!\n"
                    )
                    if ensure_volume is not None:
                        verb = "added" if ensure_volume > 0.0 else "taken"
                        message += f"The vial must allow {abs(ensure_volume)} uL to be {verb}.\n"
                    if platform.user_action_requester(
                        UserSetSamplesRequest(
                            current_samples=copy.deepcopy(platform.samples),
                            description=message,
                        )
                    ):
                        continue
                # if we get here, either there is no user_action_requester
                # or the user_action_requester told us to abort.
                # raise only, exception was already logged.
                raise error


class RecipeComponent:
    """
    A single chemical component of a slug recipe.
    Indicates the name of a single chemical and its concentration within the slug.
    This is meant to constitute the input for the GenerateComposition unit task.

    Attributes:
        name: str
            The name of the chemical, as found in the sample dataframe column 'Conc_<name>'.
        concentration: float
            Concentration of the component in the slug in mM.
        sampling_priority: int = 1000
            Priority in the order of sampling this specific component when putting together the recipy.
            Different slug sampling methods may enforce this differently, but higher numbers should correspond to
            earlier sampling. Default is 1000.
    """

    concentration_units = DataFrameTask.units_concentration

    name: str
    concentration: float
    sampling_priority: int

    def __init__(self, name: str, concentration: float, sampling_priority: int = 1000):
        """
        Constructor.

        @param name: str
            The name of the chemical, as found in the sample dataframe column 'Conc_<name>'.
        @param concentration: float
            Concentration of the component in the slug in mM.
        @param sampling_priority: int = 1000
            Priority in the order of sampling this specific component when putting together the recipy.
            Different slug sampling methods may enforce this differently, but higher numbers should correspond to
            earlier sampling. Default is 1000.
        """
        self.name = name
        self.concentration = concentration
        self.sampling_priority = sampling_priority

    def __str__(self) -> str:
        """
        Human-readable description.

        @return: str
            Description of the object.
        """
        return f"{self.name}: {format_float(self.concentration)} mM"

    def __repr__(self):
        return self.__str__()

    def __eq__(self, other):
        if isinstance(other, RecipeComponent):
            return (
                self.name == other.name
                and self.concentration == other.concentration
                and self.sampling_priority == other.sampling_priority
            )
        return False


class VialRecipeComponent:
    """
    Specifies the amount of substance to extract from a vial to compose a slug recipe.
    Indicates the name of a single vial (which can contain more than one chemical) and the volume to be extracted from
    it to form a slug.
    This is meant to be the output of the GenerateComposition unit task.

    Attributes:
        vial_id: str
            The VialID of the vial, as found in the samples dataframe.
        volume: float
            Volume to extract in uL.
        sampling_priority: int = 1000
            Priority in the order of sampling this specific vial when putting together the recipy.
            Different slug sampling methods may enforce this differently, but higher numbers should correspond to
            earlier sampling. Default is 1000.
        is_solvent: bool = False
            Should the content be treated as a solvent?
    """

    volume_units = DataFrameTask.units_volume

    vial_id: str
    human_readable_name: str
    volume: float
    sampling_priority: int
    is_solvent: bool

    def __init__(
        self,
        vial_id: str,
        volume: float,
        sampling_priority: int = 1000,
        is_solvent: bool = False,
        human_readable_name: str = "",
    ):
        """
        Constructor.

        @param vial_id: str
            The sampleID of the vial.
        @param volume: float
            Volume to extract in uL.
        @param sampling_priority: int = 1000
            Priority in the order of sampling this specific vial when putting together the recipy.
            Different slug sampling methods may enforce this differently, but higher numbers should correspond to
            earlier sampling. Default is 1000.
        @param is_solvent: bool = False
            Should the content be treated as a solvent?
        @param human_readable_name: str = ""
            Optional name which will be used when referring to the vial in human readable contexts, such as logs.
            This is not to be used as an identifier, use vial_id instead.
        """
        self.vial_id = vial_id
        self.volume = volume
        self.sampling_priority = sampling_priority
        self.is_solvent = is_solvent
        self.human_readable_name = human_readable_name

    def __str__(self) -> str:
        """
        Human-readable description.

        @return: str
            Description of the object.
        """
        name = self.human_readable_name
        if name is None or len(name) == 0:
            name = self.vial_id
        return f"{name}: {round(self.volume, 2)} uL"

    def __repr__(self):
        return self.__str__()

    def __eq__(self, other):
        if isinstance(other, VialRecipeComponent):
            return (
                self.vial_id == other.vial_id
                and self.volume == other.volume
                and self.sampling_priority == other.sampling_priority
                and self.is_solvent == other.is_solvent
            )
        return False


class GenerateComposition(DataFrameTask):
    """
    Calculate volumes to extract from each Stock type vial in the samples dataframe based on the experiment recipe.
    Vials are selected preferring the least full vial that has enough volume to make the mixture, this way new vials
    are not pierced until necessary.
    Note: all volumes are given in uL, all concentrations are taken in M.

    Usage:
    call cls.run() to execute this task.
    """

    required_columns: set = {
        DataFrameTask.vial_name,
        "Type",
        "Volume",
        "Volume_min",
        "Sampler",
    }

    @classmethod
    def run(
        cls,
        platform: Platform,
        experiment_recipe: list[RecipeComponent],
        slug_volume: float,
        sampler_name: str,
        max_relative_error: float = 0.05,
        min_volume: float = 0.5,
        min_accurate_volume: float = 20.0,
    ) -> tuple[list[VialRecipeComponent], list[RecipeComponent]]:
        """
        Calculate volumes to extract from each Stock type vial in the samples dataframe based on the experiment recipe.
        Vials are selected preferring the least full vial that has enough volume to make the mixture, this way new vials
        are not pierced until necessary.
        Vial sampling priority is calculated as that of the highest-priority compound that the vial contains, solvents
        have default vial priority.
        Note: all volumes are given in uL, all concentrations are taken in M.

            @param platform: Platform
                The platform object.
                Note: this is only used to get the samples dataframe and to send user requests.
            @param experiment_recipe: list[RecipeComponent]
                List of chemical components of the slug. Each component must provide a name and a concentration value.
                Concentration must be provided in M for every component.
                Name of chemical must correspond to a Conc_<name> column on the sample_df.
            @param slug_volume: float
                The volume of the final slug in uL.
            @param sampler_name: str
                Name of the sampler which will be preparing the slug as found in the samples dataframe.
            @param max_relative_error: float = 0.05
                For each compound, if the solution found differs from the desired concentration by more than this amount,
                the solution is rejected (RecipeError is raised).
                The error is calculated as desired_value - value / desired_value, thus this value must be > 0.0.
            @param min_volume: float = 0.5
                Ingredients which require less than this volume (in uL) to be extracted from a vial are excluded from the
                final recipe (with a warning).
            @param min_accurate_volume: float = 30.0
                If an ingredient requires less than this volume, an alternative stock solution is searched to increase
                the volume to ensure accuracy. If no alternative stock is found or no alternative recipe can be
                formulated, the original low volume recipe is used with a warning.
            @return: tuple[list[VialRecipeComponent], list[RecipeComponent]]
                Index=0: List of vials components of the slug. Each component provides a volume value in uL corresponding to the
                         volume which will need to be extracted from the vial to obtain the requested slug composition.
                Index=1: The input RecipeComponent list with the actual values used in the recipe.
            @raise: RecipeError
                In case the algorithm fails to find a recipe.
        """
        return super().run(
            platform=platform,
            experiment_recipe=experiment_recipe,
            slug_volume=slug_volume,
            sampler_name=sampler_name,
            max_relative_error=max_relative_error,
            min_volume=min_volume,
            min_accurate_volume=min_accurate_volume,
        )

    @classmethod
    def chemical_to_column(cls, name: str) -> str:
        """
        Get the name of the column holding concentration information over a compound of a given name in the samples
        dataframe.

        @param name: str
            The compound name used in the experiment recipe.
        @return: str
            The column name corresponding to that compound concentration in the vial.
        """
        return "Conc_" + name

    @classmethod
    def column_to_chemical(cls, name: str) -> str:
        """
        Get the name of the compound from the column holding concentration information over it in the samples
        dataframe.

        @param name: str
            The column name corresponding to that compound concentration in the vial.
        @return: str
            The compound name used in the experiment recipe.
        """
        return name.replace("Conc_", "", 1)

    @classmethod
    def _validate_input(
        cls,
        platform: Platform,
        experiment_recipe: list[RecipeComponent],
        slug_volume: float,
        sampler_name: str,
        max_relative_error: float = 0.05,
        min_volume: float = 0.5,
        min_accurate_volume: float = 20.0,
    ) -> None:
        cls._validate_dataframe(platform.samples)

        for ingredient in experiment_recipe:
            cls._validate_input_log(
                ingredient,
                RecipeComponent,
                f"Ingredient '{ingredient}' in recipe is of type {type(ingredient)}, "
                "but only 'RecipeComponent' is supported.",
            )
            cls._validate_input_log(
                ingredient.concentration,
                lambda x: x >= 0.0,
                f"Ingredient '{ingredient}' in recipe has negative concentration.",
            )

        cls._validate_input_log(
            sampler_name,
            lambda x: x in platform.samples["Sampler"].values,
            f"Sampler '{sampler_name}' not found in samples dataframe.",
        )

        cls._validate_input_log(
            slug_volume, lambda x: x > 0.0, f"Invalid slug volume: {slug_volume} uL."
        )
        cls._validate_input_log(
            max_relative_error,
            lambda x: x > 0.0,
            f"Invalid max error on components: {max_relative_error}.",
        )
        cls._validate_input_log(
            min_volume,
            lambda x: x > 0.0,
            f"Invalid minimum volume on components: {min_volume}.",
        )
        cls._validate_input_log(
            min_accurate_volume,
            lambda x: x > 0.0,
            f"Invalid minimum accurate volume on components: {min_accurate_volume}.",
        )

    @classmethod
    def _extract_chemical_recipe(
        cls,
        experiment_recipe: list[RecipeComponent],
    ) -> pd.DataFrame:
        """
        Convert the input experiment recipe to the format used internally.

        @param experiment_recipe: list[RecipeComponent]
            List of chemical components of the slug. Each component must provide a name and a concentration value.
            Concentration must be provided in mM for every component.
            Name of chemical must correspond to a Conc_<name> column on the sample_df.
        @return: pandas.DataFrame
            Dataframe with recipe information for all compounds.
            Columns correspond to compounds, row 0 holds concentration (mM) and row 1 holds the sampling priority.
            example:
                Conc_vinegar  Conc_salt
             0           250        250
             1       2000.00    1000.00
        """
        concentrations = []
        priority = []
        columns = []
        for ingredient in experiment_recipe:
            if ingredient.concentration > 0.0:
                columns.append(cls.chemical_to_column(ingredient.name))
                concentrations.append(ingredient.concentration)
                priority.append(ingredient.sampling_priority)

        return pd.DataFrame(data=[concentrations, priority], columns=columns)

    @staticmethod
    def _calculate_bounds(A, C, target_volume):
        """
        Calculate bounds for the linear programming problem.

        Parameters:
        A (np.ndarray): Matrix of concentrations of compounds over samples.
        C (np.ndarray): Vector of target concentrations in a sample.
        target_volume (float): Maximum volume for each sample.

        Returns:
        list of tuples: Bounds for each sample.
        """
        # Initialize bounds with the default max volume
        bnd = [(0, target_volume) for _ in range(A.shape[0])]

        # Find columns in A that correspond to compounds with zero target concentration
        zero_conc_indices = np.where(C == 0)[0]

        # For each sample, set the bound to (0, 0) if it contains any compound with zero target concentration
        for sample_index in range(A.shape[0]):
            if np.any(A[sample_index, zero_conc_indices] > 0):
                bnd[sample_index] = (0, 0)

        return bnd

    @classmethod
    def _fail_recipe(
        cls,
        exception: Exception,
        excluded_vials: list[str],
        stashed_recipies: list[tuple[list[VialRecipeComponent], pd.DataFrame]],
    ) -> tuple[list[VialRecipeComponent], pd.DataFrame]:
        """
        Fails the recipe by raising the given exception.
        If stashed recipes are available, returns the last one instead.

        @param exception: Exception
            The exception causing the recipe failure.
        @param excluded_vials: list[str]
            List of vials which have been excluded.
            This is added to the notes of the exception, if raised.
        @param stashed_recipies: list[list[VialRecipeComponent]]
            List of previously calculated recipies which succeeded, but were not ideal.
            At the moment this is due to volumes below the accuracy limit.
        @return: tuple[list[VialRecipeComponent], pandas.DataFrame]
            The best recipe so far.
        """
        if stashed_recipies:
            return stashed_recipies[-1]
        if excluded_vials:
            exception.add_note(
                f"The following vials were excluded due to insufficient volume: {excluded_vials}"
            )
        raise exception

    @classmethod
    def _get_recipe(
        cls,
        viable_samples: pd.DataFrame,
        recipe_concentrations: pd.DataFrame,
        slug_volume: float,
        sampler_name: str,
        max_relative_error: float,
        min_volume: float,
        min_accurate_volume: float = 20.0,
    ) -> tuple[list[VialRecipeComponent], pd.DataFrame]:
        # make many attempts: if a concentration profile is selected but does not have enough volume, exclude it
        # and try again (in this loop).
        attempts_left = 100
        excluded_vials = []
        stashed_recipies = []
        while attempts_left > 0:
            attempts_left -= 1
            # check that there is at least one non_zero value in for each compound in the viable samples columns:
            for col in recipe_concentrations.columns:
                if not any(viable_samples[col]):
                    error = NoSuitableVialError(
                        f"Cannot find a viable vial for compound '{cls.column_to_chemical(col)}' "
                        f"in sampler '{sampler_name}'.\n"
                        "Add a vial with the required compound to the sampler.",
                        vial_type="Stock",
                    )
                    return cls._fail_recipe(error, excluded_vials, stashed_recipies)

            # find a unique profile for each type of sample
            unique_profiles = viable_samples.drop_duplicates(
                subset=recipe_concentrations.columns.tolist()
            )

            # drop from unique profiles if a row is all 0s or if it is not a stock
            unique_profiles = unique_profiles.loc[(~(unique_profiles == 0).all(axis=1))]
            unique_profiles = unique_profiles.loc[(unique_profiles["Type"] == "Stock")]

            # Solving by linear programming:
            # Ax + s = C, where:
            # A is the matrix of concentrations in the vials (rows = compounds, cols = vials).
            # C is the vector of desired moles of compounds in the slug (desired concentration * slug_volume).
            # x is the vector of volumes of each vial. This is found by the linear programming solver.
            # s(lack) is the error which the linear programming tries to minimize in moles.

            # Create the concentration matrix A
            concentration_matrix_a = unique_profiles[
                recipe_concentrations.columns
            ].to_numpy()

            # Create the target amounts vector C
            moles_vector_c = (
                recipe_concentrations.loc[0].to_numpy().flatten() * slug_volume
            )

            # Calculate the vial priority vector based on compound sampling priority
            # For each vial, the priority will be that of the highest-priority compound that it contains (conc. != 0)
            vial_priorities = (
                (concentration_matrix_a != 0.0).astype(int)
                * recipe_concentrations.loc[1].to_numpy().flatten()
            ).max(axis=1)

            # let's do some linear programming now:
            # Objective is to minimize the total volume of samples used
            objective = np.ones(
                concentration_matrix_a.shape[0]
            )  # Minimize the sum of volumes
            lhs_ineq = (
                -concentration_matrix_a.T
            )  # Constraints to meet or exceed target concentrations
            rhs_ineq = -moles_vector_c  # Right-hand side of the inequalities

            # Bounds for each volume to be non-negative
            bnd = cls._calculate_bounds(
                concentration_matrix_a, moles_vector_c, slug_volume
            )

            # noinspection PyDeprecation
            opt = linprog(
                c=objective,
                A_ub=lhs_ineq,
                b_ub=rhs_ineq,
                bounds=bnd,
                method="highs",
            )

            # Calculate concentrations back from result, considering equipment accuracy
            result_recipe_concentrations = copy.deepcopy(recipe_concentrations)
            if opt.success:
                # Round the values based on equipment accuracy
                # Min volume is taken as reference for last significant digit
                round_to_digits = -math.floor(math.log(min_volume, 10))
                volumes = np.round(opt.x, round_to_digits)

                result_recipe_concentrations.loc[0] = (
                    np.dot(concentration_matrix_a.T, volumes) / slug_volume
                )
            relative_errors = (
                result_recipe_concentrations.loc[0] - recipe_concentrations.loc[0]
            ) / recipe_concentrations.loc[0]

            if not opt.success or any(relative_errors > max_relative_error):
                error = RecipeError(
                    "Unable to find a suitable combination of volumes to "
                    "obtain the requested recipe from the available vials."
                    "Suggested fix: review the concentration profiles of the vials."
                )
                for col_name, relative_error in relative_errors.items():
                    if relative_error > max_relative_error:
                        error.add_note(
                            f"Error on '{cls.column_to_chemical(col_name)}':"
                            f" {round(relative_error*100, 0)}%."
                        )
                return cls._fail_recipe(error, excluded_vials, stashed_recipies)

            # Construct the recipe from the solution
            volumes_recipe = []
            total_volume_used = 0
            not_feasible = False
            not_optimal = False

            for i in range(len(opt.x)):
                required_volume = opt.x[i]
                if required_volume == 0.0:
                    continue

                sample_profile = (
                    unique_profiles.iloc[i]
                    .to_frame()
                    .T[recipe_concentrations.columns.tolist()]
                )
                sample_priority = vial_priorities[i]

                same_profile_samples = pd.merge(
                    viable_samples.reset_index(),
                    sample_profile.reset_index(),
                    on=recipe_concentrations.columns.tolist(),
                    how="inner",
                )
                same_profile_samples.set_index(cls.index_name, inplace=True)
                same_profile_samples.sort_values(by=["Volume"], inplace=True)

                if required_volume < min_volume:
                    # Skip if required volume is less than the minimum which can be measured
                    cls.log(
                        f"Recipe component '{GetVialInfo.run(sample_profile.index[0], viable_samples)}' "
                        f"excluded. (volume {required_volume} uL < minimum {min_volume} uL)."
                        f"Trying again (attempts left: {attempts_left}).",
                        level="warning",
                    )
                    # Exclude from viable samples for next iteration.
                    viable_samples = viable_samples.drop(
                        index=same_profile_samples.index
                    )
                    # todo we are ignoring low volumes, but really we should scrap and try to find a better recipe
                    continue

                # check if the emptiest sample has enough volume, if not try the second emptiest and so on:
                found_suitable_vial = False
                for sample_id, sample in same_profile_samples.iterrows():
                    sample_id: str
                    if sample["Volume"] - required_volume >= sample["Volume_min"]:
                        volumes_recipe.append(
                            VialRecipeComponent(
                                vial_id=sample_id,
                                volume=required_volume,
                                sampling_priority=sample_priority,
                                human_readable_name=GetVialInfo.run(
                                    sample_id, viable_samples
                                ),
                            )
                        )
                        total_volume_used += required_volume
                        found_suitable_vial = True
                        break
                if not found_suitable_vial:
                    # scrap recipe
                    for vial_index in same_profile_samples.index:
                        excluded_vials.append(
                            GetVialInfo.run(vial_index, viable_samples)
                        )
                    viable_samples = viable_samples.drop(
                        index=same_profile_samples.index
                    )
                    cls.log(
                        f"Could not create recipe using the concentrations profile of {same_profile_samples.index[0]}. "
                        f"Excluding it and trying again (attempts left: {attempts_left})."
                    )
                    not_feasible = True
                    break

                if required_volume < min_accurate_volume:
                    # Try again, but keep this solution if no better one is found.
                    not_optimal = True
                    cls.log(
                        f"Recipe component '{GetVialInfo.run(sample_profile.index[0], viable_samples)}' "
                        f"has volume below recommended limit: "
                        f"(volume {required_volume} uL < accuracy limit {min_accurate_volume} uL)."
                        f"Trying again (attempts left: {attempts_left}).",
                        level="warning",
                    )
                    # Exclude from viable samples for next iteration.
                    viable_samples = viable_samples.drop(
                        index=same_profile_samples.index
                    )

            if not_feasible:
                continue

            # Check total volume and adjust with solvent if needed
            if total_volume_used > slug_volume:
                error = RecipeError(
                    "Unable to find a suitable recipe: none of the available vials for a certain "
                    "compound have sufficient concentration to form the slug without exceeding the volume limit.\n",
                    "Ensure that all slug components are available at sufficiently high concentration.",
                )
                return cls._fail_recipe(error, excluded_vials, stashed_recipies)
            if total_volume_used < slug_volume:
                # calculate how much solvent:
                additional_volume = slug_volume - total_volume_used

                # find all the solvent samples:
                # todo not checking which solvent we are taking
                solvent_samples = viable_samples[
                    viable_samples["Type"] == "Solvent"
                ].sort_values(by=["Volume"])

                # check if the emptiest sample has enough volume, if not try the second emptiest and so on:
                found_suitable_vial = False
                for sample_id, sample in solvent_samples.iterrows():
                    if sample["Volume"] - additional_volume >= sample["Volume_min"]:
                        volumes_recipe.append(
                            VialRecipeComponent(
                                vial_id=sample_id,
                                volume=additional_volume,
                                is_solvent=True,
                                human_readable_name=GetVialInfo.run(
                                    sample_id, solvent_samples
                                ),
                            )
                        )
                        found_suitable_vial = True
                        break
                if not found_suitable_vial:
                    error = NoSuitableVialError(
                        f"No suitable vial found for the slug solvent (requires {additional_volume} uL).\n"
                        "Refill or add new solvent vials.",
                        vial_type="Solvent",
                    )
                    return cls._fail_recipe(error, excluded_vials, stashed_recipies)

            if not_optimal:
                stashed_recipies.append((volumes_recipe, result_recipe_concentrations))
                continue
            else:
                return volumes_recipe, result_recipe_concentrations

        # All attempts failed... declare defeat :-(
        error = RecipeError(
            "Unable to find a suitable recipe: none of the available vials for a certain "
            "compound have enough remaining volume.\n"
            "Ensure that all slug components are available.",
        )
        return cls._fail_recipe(error, excluded_vials, stashed_recipies)

    @classmethod
    def _execute(
        cls,
        platform: Platform,
        experiment_recipe: list[RecipeComponent],
        slug_volume: float,
        sampler_name: str,
        max_relative_error: float = 0.05,
        min_volume: float = 0.5,
        min_accurate_volume: float = 20.0,
    ) -> tuple[list[VialRecipeComponent], list[RecipeComponent]]:
        # keep trying and requesting user actions forever.
        while True:
            # make a deep copy of samples. we modify the Viable column here, but that should not remain.
            viable_samples = platform.samples[
                (platform.samples["Viable"])
                & (platform.samples["Sampler"] == sampler_name)
            ]
            recipe_concentrations = cls._extract_chemical_recipe(experiment_recipe)

            try:
                vial_recipe, recipe_concentrations = cls._get_recipe(
                    viable_samples=viable_samples,
                    recipe_concentrations=recipe_concentrations,
                    slug_volume=slug_volume,
                    sampler_name=sampler_name,
                    max_relative_error=max_relative_error,
                    min_volume=min_volume,
                )
                result_experiment_recipe = []
                for column in recipe_concentrations.columns:
                    component = RecipeComponent(
                        name=cls.column_to_chemical(column),
                        concentration=recipe_concentrations.loc[0, column],
                        sampling_priority=recipe_concentrations.loc[1, column],
                    )
                    result_experiment_recipe.append(component)
                return vial_recipe, result_experiment_recipe
            except (NoSuitableVialError, RecipeError) as error:
                if platform.user_action_requester is not None:
                    message = error.args[0]
                    try:
                        for note in error.__notes__:
                            message += "\n" + note
                    except AttributeError:
                        pass
                    if platform.user_action_requester(
                        UserSetSamplesRequest(
                            current_samples=copy.deepcopy(platform.samples),
                            description=message,
                        )
                    ):
                        continue
                cls.log(error)
                raise error


class UndefinedSamplerPump(Exception):
    """Request to pump a volume in or out of a sample in a sampler which does not specify a pump for this operation."""


class PumpSample(DataFrameTask):
    """
    Suck or push a determined volume from a vial / sample.
    Moves to the vial specified via the sample id and returns to the travel position, unless otherwise specified.
    Note: Use negative volume values to suck from the vials, use zero to simply move to the vial (set retract=False to
    stay).

    Usage:
    call cls.run() to execute this task.
    """

    required_columns: set = {
        DataFrameTask.vial_name,
        "Sampler",
        "Type",
        "Volume",
        "Volume_max",
        "Volume_min",
        "X",
        "Y",
        "Z_bottom",
        "Z_top",
        "Viable",
    }

    @classmethod
    def run(
        cls,
        platform: Platform,
        sample_id: str,
        volume: float,
        needle_position: float | str = "auto",
        use_pump: SyringePump | None = None,
        auxiliary_needle: bool = True,
        delay_headspace_equilibration: float = 0.5,
        flowrate: float = 1.0,
        plunge_speed: float = 500.0,
        dipping_speed: float | None = None,
        retract_speed: float | None = None,
        move_speed: float | None = None,
        delay_before_dipping: float = 0.0,
        volume_before_dipping: float = 0.0,
        delay_after_pumping: float = 0.0,
        retract: bool = True,
        update_volume: bool = True,
        allow_refill: bool = False,
    ) -> None:
        """
        Suck or push a determined volume from a vial / sample.
        Moves to the vial specified via the sample id and returns to the travel position, unless otherwise specified.
        Note: Use negative volume values to suck from the vials.

        @param platform: Platform
            The platform object running the experiment.
        @param sample_id: str
            Unique identifier for the sample within the dataframe.
        @param volume: float
            The volume to pump from the vial in uL. Positive values add liquid to the vial, negative remove it.
        @param needle_position: float | str = 1.0
            Vertical positioning of the needle for the pump operation.
            If a float is provided, 0.0 correspond to the highest position within the vial, 1.0 to the lowest. Fractions
            correspond to values in between.
            If a string is provided (case-insensitive):
                'top': highest position inside the vial
                'bottom': lowest position inside the vial
                'auto': use top position for pushing into the vial, bottom for sucking (Default).
                'outside': The needle is left in the travel position
        @param use_pump: SyringePump | None = None
            Override the default syringe pump to use in combination with the sampler specified by the sample_id entry.
            By default, the pump specified in the configuration file is used.
        @param auxiliary_needle: bool = True
            Puncture the vial with the auxiliary needle before entering with the main needle.
            This allows pressure equilibration via the nitrogen line on the auxiliary needle.
        @param delay_headspace_equilibration: float = 0.5
            Allow this time before entering with the main needle, to ensure pressure equilibration.
            This is skipped if auxiliary_needle is set to false.
        @param flowrate: float = 0.5
            Pumping flowrate in mL/min.
        @param plunge_speed: float = 500.0
            The needle is moved down into the vial septum at this speed [mm/min].
            Note that moving the needle through the septum too quickly can bend the needle.
        @param dipping_speed: float | None = None
            The needle is moved down from the vial headspace into the liquid at this speed.
            None uses default value from sampler.
        @param retract_speed: float | None = None
            The needle is moved up from the vial at this speed [mm/min]. None uses default value from sampler.
        @param move_speed: float | None = None
            The sampler moves at this speed [mm/min]. None uses default value from sampler.
        @param delay_before_dipping: float = 0.0
            Pause in S to perform upon entering the vial (the needle stops in the vial headspace, after piercing
            the septum, but before touching any liquid within the vial).
            If a bubble is present in the sampling loop, the pause allows its volume to reach equilibrium before the
            sample liquid is reached.
        @param volume_before_dipping: float = 0.0
            This volume is pushed or sucked (negative) from the vial headspace. If sucked, the content volume of the
            vial is not updated.
            This is to allow inserting small bubbles between slug components, reducing diffusion and cross-contamination
            errors.
        @param delay_after_pumping: float = 0.0
            Pause in S to perform after pumping the liquid from the vial.
            If a bubble is present, the pause allows its volume to reach equilibrium.
        @param retract: bool = True
            If set to False, the needle is left within the vial after the pumping operation.
            This can be used to keep pumping outside of this unit task.
        @param update_volume: bool = True
            If set to False, the volume and viability values in the samples dataframe are not changed.
            This is useful if, for example, sucking air from a N2 vial.
        @param allow_refill: bool = False
            If True, allows pumping volumes larger than what is available in the syringe by refilling the syringe
            as many times as required.
        """
        return super().run(
            platform=platform,
            sample_id=sample_id,
            volume=volume,
            needle_position=needle_position,
            use_pump=use_pump,
            auxiliary_needle=auxiliary_needle,
            delay_headspace_equilibration=delay_headspace_equilibration,
            flowrate=flowrate,
            plunge_speed=plunge_speed,
            retract_speed=retract_speed,
            move_speed=move_speed,
            delay_before_dipping=delay_before_dipping,
            volume_before_dipping=volume_before_dipping,
            delay_after_pumping=delay_after_pumping,
            retract=retract,
            update_volume=update_volume,
            allow_refill=allow_refill,
        )

    @classmethod
    def _validate_input(
        cls,
        platform: Platform,
        sample_id: str,
        volume: float,
        needle_position: float | str = "auto",
        use_pump: SyringePump | None = None,
        auxiliary_needle: bool = True,
        delay_headspace_equilibration: float = 0.5,
        flowrate: float = 1.0,
        plunge_speed: float = 500.0,
        dipping_speed: float | None = None,
        retract_speed: float | None = None,
        move_speed: float | None = None,
        delay_before_dipping: float = 0.0,
        volume_before_dipping: float = 0.0,
        delay_after_pumping: float = 0.0,
        retract: bool = True,
        update_volume: bool = True,
        allow_refill: bool = False,
    ) -> None:
        cls._validate_platform(platform)
        cls._validate_dataframe(platform.samples, sample_id)
        if use_pump is not None:
            cls._validate_input_log(
                use_pump,
                SyringePump,
                f"Syringe pump must be of type 'SyringePump', not '{type(use_pump)}'.",
            )
        if isinstance(needle_position, str):
            needle_positions = ("top", "bottom", "auto", "outside")
            cls._validate_input_log(
                needle_position,
                lambda x: x.lower() in needle_positions,
                f"Needle position can be one of '{needle_positions}' (is {needle_position}).",
            )
            cls._validate_input_log(
                volume,
                lambda x: not ((not x == 0.0) and needle_position == "outside"),
                "Cannot pump a volume != 0 in the vial with the needle outside.",
            )
        elif isinstance(needle_position, (int | float)):
            cls._validate_input_log(
                needle_position,
                lambda x: 0.0 <= x <= 1.0,
                f"If numerical, needle position must be 0.0 <= x <= 1.0 (is {needle_position}).",
            )

    @classmethod
    def _update_volume(
        cls,
        samples: pd.DataFrame,
        sample_id: str,
        volume_pumped: float,
    ) -> None:
        """
        Track the volume of liquid within a vial on the samples dataframe.

        @param samples: pandas.DataFrame
            DataFrame holding information on all samples and vials. See 'GenerateSampleDataframe' for more details.
        @param sample_id: str
            Unique identifier for the sample within the dataframe.
        @param volume_pumped: float
            Volume change in uL. Positive values add liquid to the vial, negative remove it.
        """
        volume = samples.loc[sample_id, "Volume"]
        volume += volume_pumped

        if samples.loc[sample_id, "Type"] == "Sample":
            samples.loc[sample_id, "Viable"] = False
        else:
            UpdateSampleViability.run(samples_to_update=samples.loc[[sample_id]])

        samples.loc[sample_id, "Volume"] = volume

    @classmethod
    def _update_counter(
        cls,
        samples: pd.DataFrame,
        sample_id: str,
        counter_increment: int = 1,
    ) -> None:
        """
        Track the volume of liquid within a vial on the samples dataframe.

        @param samples: pandas.DataFrame
            DataFrame holding information on all samples and vials. See 'GenerateSampleDataframe' for more details.
        @param sample_id: str
            Unique identifier for the sample within the dataframe.
        @param counter_increment: int = 1
            Amount by which the counter should be incremented.
        """
        counter = samples.loc[sample_id, "Counter"]
        counter += counter_increment
        if samples.loc[sample_id, "Type"] == "Sample":
            samples.loc[sample_id, "Viable"] = False
        samples.loc[sample_id, "Counter"] = counter

    @classmethod
    def _execute(
        cls,
        platform: Platform,
        sample_id: str,
        volume: float,
        needle_position: float | str = "auto",
        use_pump: SyringePump | None = None,
        auxiliary_needle: bool = True,
        delay_headspace_equilibration: float = 0.5,
        flowrate: float = 1.0,
        plunge_speed: float = 500.0,
        dipping_speed: float | None = None,
        retract_speed: float | None = None,
        move_speed: float | None = None,
        delay_before_dipping: float = 0.0,
        volume_before_dipping: float = 0.0,
        delay_after_pumping: float = 0.0,
        retract: bool = True,
        update_volume: bool = True,
        allow_refill: bool = False,
    ) -> None:
        total_volume_pumped = 0.0
        sampler = cls._get_sampler(platform=platform, sample_id=sample_id)
        if use_pump is not None:
            pump = use_pump
        else:
            pump = cls._get_sampler_pump(platform=platform, sampler=sampler)
        if dipping_speed is None:
            dipping_speed = sampler.default_feedrate_z
        if retract_speed is None:
            retract_speed = sampler.default_feedrate_z
        sampler_initial_state = Snapshot(device=sampler, parameters=["feed"])
        sample_data = platform.samples.loc[sample_id]

        if not volume == 0.0 and pump is None:
            error = UndefinedSamplerPump(
                f"Sampler '{sampler}' was not configured with a pump,"
                f" cannot pump from sample '{GetVialInfo.run(sample_id, platform.samples)}'."
            )
            cls.log(error)
            raise error

        if isinstance(needle_position, str):
            needle_position = needle_position.lower()
            if needle_position == "outside":
                sample_position_z = sampler.travel_position.z
            elif needle_position == "bottom":
                sample_position_z = sample_data["Z_bottom"]
            elif needle_position == "top":
                sample_position_z = sample_data["Z_top"]
            else:
                sample_position_z = (
                    sample_data["Z_top"] if volume >= 0.0 else sample_data["Z_bottom"]
                )
        else:
            sample_position_z = (
                sample_data["Z_top"]
                + (sample_data["Z_bottom"] - sample_data["Z_top"]) * needle_position
            )

        sample_position = GrblPosition(
            x=sample_data["X"], y=sample_data["Y"], z=sample_position_z
        )
        cls._move_xy(
            sampler=sampler,
            destination=sample_position,
            move_speed=move_speed,
            retract_speed=retract_speed,
        )

        # Equilibrate pressure with the auxiliary needle
        if auxiliary_needle:
            sampler["aux_needle"] = "ON"
            sleep(delay_headspace_equilibration)

        # Enter the vial
        position = sampler["position"]
        if not cls._same_z(position, sample_position):
            sampler["feed"] = plunge_speed
            if sample_position.z < sample_data["Z_top"]:
                # first enter the headspace, pump and pause as needed
                sampler["move"] = GrblPosition(z=sample_data["Z_top"])
                if not volume_before_dipping == 0.0:
                    PumpVolume.run(
                        pump=pump,
                        volume=volume_before_dipping,
                        flowrate=flowrate,
                        allow_refill=allow_refill,
                    )
                    if volume_before_dipping > 0.0:
                        total_volume_pumped += volume_before_dipping
                if delay_before_dipping > 0.0:
                    sleep(delay_before_dipping)
                cls._update_counter(samples=platform.samples, sample_id=sample_id)
            destination_z = GrblPosition(z=sample_position.z)
            sampler["feed"] = dipping_speed
            sampler["move"] = destination_z

        # Main pumping action
        if not volume == 0.0:
            PumpVolume.run(
                pump=pump,
                volume=volume,
                flowrate=flowrate,
                allow_refill=allow_refill,
            )
            total_volume_pumped += volume
            if update_volume:
                cls._update_volume(
                    samples=platform.samples,
                    sample_id=sample_id,
                    volume_pumped=total_volume_pumped,
                )

        # Final equilibration delay
        if delay_after_pumping > 0.0:
            sleep(delay_after_pumping)

        # Retract and return sampler to original state
        if retract:
            cls._set_travel_position(sampler=sampler, retract_speed=retract_speed)

        sampler_initial_state.restore()


class CleanNeedle(DataFrameTask):
    """
    Cleans the needle by plunging and retracting the needle into a vial with cleaning solvent.

    Usage:
    call cls.run() to execute this task.
    """

    @classmethod
    def run(
        cls,
        platform: Platform,
        sampler: Sampler,
        purge_volume: float = 100.0,
        purge_flowrate: float = 2.0,
        use_pump: SyringePump | None = None,
        rinse_cycles: int = 2,
        plunge_speed: float = 500.0,
        dipping_speed: float | None = None,
        volume_before_dipping: float = 0.0,
        retract_speed: float | None = None,
        move_speed: float | None = None,
    ) -> None:
        """
        Clean the needle by purging the required volume into a waste vial and rinsing into a cleaning vial.

        @param platform: Platform
            Platform running the campaign.
        @param sampler: Sampler
            Sampler device whose needle will be cleaned.
        @param purge_volume: float = 100.0
            Optional volume to purge in the waste in uL.
        @param purge_flowrate: float = 2.0
            Flowrate to use for purging the volume in mL/min.
        @param use_pump: SyringePump | None = None
            Override the default syringe pump to use in combination with the sampler specified by the sample_id entry.
            By default, the pump specified in the configuration file is used.
        @param rinse_cycles: int = 2
            Optional rinsing cycles can be performed in a cleaning vial.
        @param plunge_speed: float = 500.0
            The needle is moved down into the vial septum at this speed [mm/min].
        @param dipping_speed: float | None = None
            The needle is moved down from the vial headspace into the liquid at this speed.
            None uses default value from sampler.
        @param volume_before_dipping: float = 0.0
            This volume is pushed or sucked (negative) from the rinsing vial headspace.
            If sucked, the content volume of the vial is not updated.
            This is to allow inserting small bubbles between slug components, reducing diffusion and cross-contamination
            errors.
        @param retract_speed: float | None = None
            Speed for the vertical needle movement out of the vials in mm/min. If None, uses default of sampler.
        @param move_speed: float | None = None
            Speed for horizontal movement to the vials in mm/min. If None, uses default of sampler.
        @return: None
        """
        return super().run(
            platform=platform,
            sampler=sampler,
            purge_volume=purge_volume,
            purge_flowrate=purge_flowrate,
            use_pump=use_pump,
            rinse_cycles=rinse_cycles,
            plunge_speed=plunge_speed,
            dipping_speed=dipping_speed,
            volume_before_dipping=volume_before_dipping,
            retract_speed=retract_speed,
            move_speed=move_speed,
        )

    @classmethod
    def _validate_input(
        cls,
        platform: Platform,
        sampler: Sampler,
        purge_volume: float = 100.0,
        purge_flowrate: float = 2.0,
        use_pump: SyringePump | None = None,
        rinse_cycles: int = 2,
        plunge_speed: float = 500.0,
        dipping_speed: float | None = None,
        volume_before_dipping: float = 0.0,
        retract_speed: float | None = None,
        move_speed: float | None = None,
    ) -> None:
        cls._validate_platform(platform)
        cls._validate_dataframe(platform.samples)
        cls._validate_sampler(sampler)
        cls._validate_input_log(
            rinse_cycles,
            lambda x: x > 0,
            "Cycles must be positive.",
        )
        if use_pump is not None:
            cls._validate_input_log(
                use_pump,
                SyringePump,
                f"Syringe pump must be of type 'SyringePump', not '{type(use_pump)}'.",
            )

    @classmethod
    def _execute(
        cls,
        platform: Platform,
        sampler: Sampler,
        purge_volume: float = 100.0,
        purge_flowrate: float = 2.0,
        use_pump: SyringePump | None = None,
        rinse_cycles: int = 2,
        plunge_speed: float = 500.0,
        dipping_speed: float | None = None,
        volume_before_dipping: float = 0.0,
        retract_speed: float | None = None,
        move_speed: float | None = None,
    ) -> None:
        # purge into waste vial if needed.
        if not purge_volume == 0.0:
            # here motion speeds can be None, as PumpSample handles that.

            waste_id = FindVial.run(
                platform=platform,
                sampler=sampler,
                vial_type="Waste",
                ensure_volume=purge_volume,
            )
            if waste_id is None:
                error = NoSuitableVialError(
                    f"No viable waste vial available on '{sampler}'", vial_type="Waste"
                )
                cls.log(error)
                raise error
            PumpSample.run(
                platform=platform,
                sample_id=waste_id,
                volume=purge_volume,
                flowrate=purge_flowrate,
                needle_position="top",
                use_pump=use_pump,
                plunge_speed=plunge_speed,
                dipping_speed=dipping_speed,
                retract_speed=retract_speed,
                move_speed=move_speed,
                allow_refill=True,
            )

        # dip into cleaning vial
        if not rinse_cycles == 0:
            # dipping_speed and retract_speed are used directly here, so they cannot be None.
            if dipping_speed is None:
                dipping_speed = sampler.default_feedrate_z
            if retract_speed is None:
                retract_speed = sampler.default_feedrate_z

            clean_id = FindVial.run(
                platform=platform,
                sampler=sampler,
                vial_type="Cleaning",
            )
            if clean_id is None:
                error = NoSuitableVialError(
                    f"No cleaning vial available on '{sampler}'", vial_type="Cleaning"
                )
                cls.log(error)
                raise error
            PumpSample.run(
                platform=platform,
                sample_id=clean_id,
                volume=volume_before_dipping,
                needle_position="top",
                plunge_speed=plunge_speed,
                retract_speed=retract_speed,
                move_speed=move_speed,
                retract=False,
                update_volume=False,
            )
            top = GrblPosition(z=platform.samples.loc[clean_id, "Z_top"])
            bottom = GrblPosition(z=platform.samples.loc[clean_id, "Z_bottom"])
            sampler_initial_state = Snapshot(device=sampler, parameters=["feed"])
            while rinse_cycles > 0:
                sampler["feed"] = dipping_speed
                sampler["move"] = bottom
                if rinse_cycles > 1:
                    sampler["feed"] = retract_speed
                    sampler["move"] = top
                rinse_cycles -= 1
            cls._set_travel_position(sampler=sampler, retract_speed=retract_speed)
            sampler_initial_state.restore()


class OrderRecipe(DataFrameTask):
    """
    Process a list of VialRecipeComponents to improve mixing and accuracy and guarantee consistency across runs.

    Usage:
    call cls.run() to execute this task.
    """

    @classmethod
    def run(
        cls, recipe: list[VialRecipeComponent], method: str = "first"
    ) -> list[VialRecipeComponent]:
        """
        Process a list of VialRecipeComponents to improve mixing and accuracy.

        @param recipe: list[VialRecipeComponent]
            List of vials components of the slug. Each component provides a volume value in uL corresponding to the
            volume which will need to be extracted from the vial to obtain the requested slug composition.
        @param method: str
            The method to use for processing the list.
            Available methods:
            - 'middle':
                Half of each solvent is taken before the reagents, the rest after.
            - 'first':
                Reagents are taken first, solvents after.
        @return: list[VialRecipeComponent]
            The recipe giving the same final composition, but with altered order.
        """
        return super().run(recipe=recipe, method=method)

    @classmethod
    def _validate_input(
        cls, recipe: list[VialRecipeComponent], method: str = "first"
    ) -> None:
        sorting_methods = cls.sorting_methods()
        cls._validate_input_log(
            method,
            lambda x: x in sorting_methods,
            f"Invalid sorting method '{method}'. Valid methods: '{sorting_methods}'",
        )

    # These are the attributes taken from the VialRecipeComponent into the df
    _vial_attributes = [
        "vial_id",
        "volume",
        "sampling_priority",
        "is_solvent",
        "human_readable_name",
    ]

    @classmethod
    def _to_df(cls, recipe: list[VialRecipeComponent]) -> pd.DataFrame:
        """
        Transform the list recipe into a dataframe.

        @param recipe: list[VialRecipeComponent]
            The input recipe.
        @return: pd.DataFrame
            The input recipe as a DataFrame.
        """
        data = [
            [
                vial.__getattribute__(attribute_name)
                for attribute_name in cls._vial_attributes
            ]
            for vial in recipe
        ]
        return pd.DataFrame(
            data=data,
            columns=cls._vial_attributes,
        )

    @classmethod
    def _to_list(cls, recipe: pd.DataFrame) -> list[VialRecipeComponent]:
        """
        Transform the dataframe recipe into a list.

        @param recipe: pandas.DataFrame
            Recipe in dataframe form.
        @return: list[VialRecipeComponent]
            Recipe as a list.
        """
        result = []
        for index, row in recipe.iterrows():
            result.append(VialRecipeComponent(**row))
        return result

    @classmethod
    def _sort_method_middle(
        cls, recipe: list[VialRecipeComponent]
    ) -> list[VialRecipeComponent]:
        """
        Sandwiches all reagents in the middle of the slug with solvents on the two sides.

        @param recipe: list[VialRecipeComponent]
            The unordered recipe.
        @return: list[VialRecipeComponent]
            The ordered recipe.
        """
        recipe_df = cls._to_df(recipe)

        # Max priority of any solvent in the recipe
        split_priority_threshold = recipe_df.loc[
            recipe_df["is_solvent"], "sampling_priority"
        ].max()
        # Total recipe volume
        total_volume = recipe_df["volume"].sum()

        # recipe components with priority lower or equal to solvent are split in two
        # Do not split low volume components (volume < 1/5 of the slug).
        split_components = recipe_df.loc[
            (
                (recipe_df["sampling_priority"] <= split_priority_threshold)
                & (recipe_df["volume"] >= total_volume * 0.2)
            )
        ].copy()
        non_split_components = recipe_df.drop(split_components.index).copy()

        # Half volume of split component, as they will appear twice.
        split_components["volume"] = split_components["volume"] / 2

        # sort split components from high to low priority and high to low volume.
        split_components.sort_values(
            ["is_solvent", "sampling_priority", "volume"],
            ascending=[False, False, False],
            inplace=True,
        )
        # sort non-split components from high to low priority and high to low volume.
        non_split_components.sort_values(
            ["sampling_priority", "volume"],
            ascending=[False, False],
            inplace=True,
        )
        # sort split components in reverse
        split_components_after = split_components.reindex(
            index=split_components.index[::-1]
        )

        # put together result
        result = pd.concat(
            [split_components, non_split_components, split_components_after]
        )
        return cls._to_list(result)

    @classmethod
    def _sort_method_first(
        cls, recipe: list[VialRecipeComponent]
    ) -> list[VialRecipeComponent]:
        """
        Takes all reagents first, then the solvents.

        @param recipe: list[VialRecipeComponent]
            The unordered recipe.
        @return: list[VialRecipeComponent]
            The ordered recipe.
        """
        recipe_df = cls._to_df(recipe)
        recipe_df.sort_values(
            ["is_solvent", "sampling_priority", "volume"],
            ascending=[True, False, True],
            inplace=True,
        )
        return cls._to_list(recipe_df)

    @classmethod
    def _sorting_methods(cls) -> dict:
        return {"middle": cls._sort_method_middle, "first": cls._sort_method_first}

    @classmethod
    def sorting_methods(cls) -> list[str]:
        return list(cls._sorting_methods().keys())

    @classmethod
    def _execute(
        cls, recipe: list[VialRecipeComponent], method: str = "first"
    ) -> list[VialRecipeComponent]:
        methods = cls._sorting_methods()
        try:
            return methods[method.lower()](recipe)
        except KeyError:
            error = ValueError(f"Invalid method name '{method}'.")
            error.add_note(f"Valid method names are: {methods.keys()}")
            cls.log(error)
            raise error


class PrepareReactionSlug(DataFrameTask):
    """
    Prepare a reaction slug based on a recipe.
    The recipe is a list of VialRecipeComponents (vial ids and volumes), as returned by the GenerateComposition task.
    To maintain accuracy, does not allow the slug volume to exceed the syringe volume (no refills during slug
    preparation).

    Usage:
    call cls.run() to execute this task.
    """

    @classmethod
    def run(
        cls,
        platform: Platform,
        recipe: list[VialRecipeComponent],
        sampling_flowrate: float = 1.0,
        use_pump: SyringePump | None = None,
        in_vial: str | None = None,
        slug_volume: float = 500.0,
        bubble_volume: float = 50.0,
        bubble_flowrate: float = 0.5,
        clean_needle: bool = False,
        bubble_between_components: float = 0.0,
        plunge_speed: float = 500.0,
        dipping_speed: float | None = None,
        retract_speed: float | None = None,
        move_speed: float | None = None,
    ) -> None:
        """
        Prepare a reaction slug based on a recipe.
        The recipe is a list of VialRecipeComponents (vial ids and volumes), as returned by the GenerateComposition task.
        To maintain accuracy, does not allow the slug volume to exceed the syringe volume (no refills during slug
        preparation).

        @param platform: Platform
            The platform performing the experiment.
        @param recipe: list[VialRecipeComponent]
            List of vials components of the slug. Each component provides a volume value in uL corresponding to the
            volume which will need to be extracted from the vial to obtain the requested slug composition.
            The various components are processed in the order they appear on the list.
        @param sampling_flowrate: float = 1.0
            Flowrate to use for sucking the samples out of the vials in mL/min.
        @param use_pump: SyringePump | None = None
            Override the default syringe pump to use in combination with the sampler specified by the sample_id entry.
            By default, the pump specified in the configuration file is used.
        @param in_vial: str | None = None
            If a VialID is provided, the components are added one by one in the vial and the mixed slug is extracted
            from it at the end. In this case, the recipe must exceed the slug volume, as not all material can be
            aspirated from the vial.
        @param slug_volume: float = 500.0
            Volume of the slug to extract from the vial in which the slug was mixed.
            if 'in_vial' is None (default), this is ignored and the slug volume results from the recipe.
        @param bubble_volume: float = 50.0
            Volume of the bubble in uL. Set to 0.0 to disable creating the bubble.
        @param bubble_flowrate: float = 0.5
            Flowrate used for sucking the gas bubble out of the vial in mL/min.
        @param clean_needle: bool = False
            If set to true, the needle is cleaned after every ragent (not solvent) is sampled.
        @param bubble_between_components: float = 0.0
            Volume in uL of bubbles to place between components of the same slug. A small bubble can prevent diffusion
            errors during the sampling operation.
            When mixing into a vial (in_vial != None), this corresponds to an extra push for each component when
            dispensing into the vial.
        @param plunge_speed: float = 500.0
            The needle is moved down into the vial septum at this speed [mm/min].
        @param dipping_speed: float | None = None
            The needle is moved down from the vial headspace into the liquid at this speed.
            None uses default value from sampler.
        @param retract_speed: float | None = None
            The needle is moved up from the vial at this speed [mm/min]. None uses default value from sampler.
        @param move_speed: float | None = None
            The sampler moves at this speed [mm/min]. None uses default value from sampler.
        """
        return super().run(
            platform=platform,
            recipe=recipe,
            sampling_flowrate=sampling_flowrate,
            use_pump=use_pump,
            in_vial=in_vial,
            slug_volume=slug_volume,
            bubble_volume=bubble_volume,
            bubble_flowrate=bubble_flowrate,
            clean_needle=clean_needle,
            bubble_between_components=bubble_between_components,
            plunge_speed=plunge_speed,
            dipping_speed=dipping_speed,
            retract_speed=retract_speed,
            move_speed=move_speed,
        )

    @classmethod
    def _validate_input(
        cls,
        platform: Platform,
        recipe: list[VialRecipeComponent],
        sampling_flowrate: float = 1.0,
        use_pump: SyringePump | None = None,
        in_vial: str | None = None,
        slug_volume: float = 500.0,
        bubble_volume: float = 50.0,
        bubble_flowrate: float = 0.5,
        clean_needle: bool = False,
        bubble_between_components: float = 0.0,
        plunge_speed: float = 500.0,
        dipping_speed: float | None = None,
        retract_speed: float | None = None,
        move_speed: float | None = None,
    ) -> None:
        cls._validate_platform(platform)
        cls._validate_dataframe(platform.samples, in_vial)
        cls._validate_input_log(
            bubble_volume, lambda x: x >= 0, "Bubble volume must be >= 0."
        )
        cls._validate_input_log(
            bubble_between_components,
            lambda x: x >= 0,
            "Bubble volume betweeen components must be >= 0.",
        )
        cls._validate_input_log(
            recipe, list, f"Invalid type '{type(recipe)}' for argument 'recipe'."
        )
        volumes = []
        sample_ids = [in_vial] if in_vial is not None else []
        for vial in recipe:
            volumes.append(vial.volume)
            sample_ids.append(vial.vial_id)
        missing_ids = set(sample_ids) - set(platform.samples.index)
        cls._validate_input_log(
            missing_ids,
            lambda x: len(x) == 0,
            "Recipe requires sample_ids which are not found in samples dataframe"
            f" (not found: {missing_ids}).",
        )
        sampler = cls._get_sampler(platform=platform, sample_id=sample_ids[0])
        sampler_name = platform.samples.loc[sample_ids[0], "Sampler"]
        if use_pump is not None:
            pump = use_pump
        else:
            pump = cls._get_sampler_pump(platform=platform, sampler=sampler)
        cls._validate_input_log(
            pump,
            SyringePump,
            f"Syringe pump must be of type 'SyringePump', not '{type(use_pump)}'.",
        )
        cls._validate_input_log(
            platform.samples,
            lambda x: all(x.loc[sample_ids, "Sampler"] == sampler_name),
            "All samples used for preparing a reaction slug must be located in the same Sampler.",
        )
        cls._validate_input_log(
            volumes,
            lambda x: all([y >= 0.0 for y in x]),
            "Only positive volumes are allowed in recipe.",
        )
        recipe_volume = sum(volumes)
        highest_volume = recipe_volume
        if in_vial is not None:
            cls._validate_input_log(
                slug_volume,
                lambda x: x <= recipe_volume,
                f"Slug volume ({slug_volume} uL) must be lower than recipe volume ({recipe_volume} uL)"
                " when mixing in a vial.",
            )
            highest_volume = slug_volume
        cls._validate_input_log(
            highest_volume,
            lambda x: x <= pump.syringe_volume,
            f"Slug volume ({highest_volume} uL) exceeds maximum syringe volume ({pump.syringe_volume} uL).",
        )

    @classmethod
    def _execute(
        cls,
        platform: Platform,
        recipe: list[VialRecipeComponent],
        sampling_flowrate: float = 1.0,
        use_pump: SyringePump | None = None,
        in_vial: str | None = None,
        slug_volume: float = 500.0,
        bubble_volume: float = 50.0,
        bubble_flowrate: float = 0.5,
        clean_needle: bool = False,
        bubble_between_components: float = 0.0,
        plunge_speed: float = 500.0,
        dipping_speed: float | None = None,
        retract_speed: float | None = None,
        move_speed: float | None = None,
    ) -> None:
        sampler = cls._get_sampler(platform=platform, sample_id=recipe[0].vial_id)
        if use_pump is not None:
            pump = use_pump
        else:
            pump = cls._get_sampler_pump(platform=platform, sampler=sampler)
        # Empty pump to ensure entire volume is available.
        FillPump.run(pump=pump, fill=False)
        try:
            if bubble_volume > 0.0:
                # Suck in the bubble, but move plunger back to zero (still able to use entire volume).
                gas_vial = FindVial.run(
                    platform=platform, sampler=sampler, vial_type="Gas"
                )
                if gas_vial is None:
                    error = NoSuitableVialError(
                        f"No viable gas vial available on '{sampler}'",
                        vial_type="Gas",
                    )
                    cls.log(error)
                    raise error
                PumpSample.run(
                    platform=platform,
                    sample_id=gas_vial,
                    volume=-bubble_volume,
                    flowrate=bubble_flowrate,
                    needle_position=0.3,
                    use_pump=pump,
                    update_volume=False,
                    plunge_speed=plunge_speed,
                    dipping_speed=dipping_speed,
                    retract_speed=retract_speed,
                    move_speed=move_speed,
                )
                FillPump.run(pump=pump, fill=False)
            # Keep the pump enabled in between pumping actions to maintain micro-stepping accuracy
            pump["enable"] = "ON"
            # If rinsing is enabled, take the small bubble before dipping into the rinse vial.
            # If so, do not take the bubble again at the next component.
            bubble_already_present = False
            for vial in recipe:
                # a recipe might set an ingredient to zero, in this case we ignore it.
                if not vial.volume == 0.0:
                    microbubble = (
                        bubble_between_components if not bubble_already_present else 0.0
                    )
                    PumpSample.run(
                        platform=platform,
                        sample_id=vial.vial_id,
                        volume=-vial.volume,
                        flowrate=sampling_flowrate,
                        needle_position="bottom",
                        use_pump=pump,
                        delay_headspace_equilibration=0.5,
                        delay_before_dipping=0.1,
                        volume_before_dipping=-microbubble,
                        delay_after_pumping=0.1,
                        plunge_speed=plunge_speed,
                        dipping_speed=dipping_speed,
                        retract_speed=retract_speed,
                        move_speed=move_speed,
                    )
                    bubble_already_present = False
                    # If slug should be made in a vial, dump component in it.
                    if in_vial is not None:
                        PumpSample.run(
                            platform=platform,
                            sample_id=in_vial,
                            volume=vial.volume + microbubble,
                            flowrate=sampling_flowrate,
                            needle_position="bottom",
                            use_pump=pump,
                            delay_headspace_equilibration=0.5,
                            delay_before_dipping=0.0,
                            volume_before_dipping=0.0,
                            delay_after_pumping=0.1,
                            plunge_speed=plunge_speed,
                            dipping_speed=dipping_speed,
                            retract_speed=retract_speed,
                            move_speed=move_speed,
                        )
                    # If required, clean needle. If we took solvent, only clean if the needle touched the slug vial.
                    if clean_needle and (not vial.is_solvent or in_vial is not None):
                        CleanNeedle.run(
                            platform=platform,
                            sampler=sampler,
                            purge_volume=0.0,
                            use_pump=pump,
                            rinse_cycles=1,
                            plunge_speed=plunge_speed,
                            dipping_speed=dipping_speed,
                            volume_before_dipping=(
                                -bubble_between_components if in_vial is None else 0.0
                            ),
                            retract_speed=retract_speed,
                            move_speed=move_speed,
                        )
                        bubble_already_present = in_vial is None
            # The slug is ready, if in vial we take it to the loop
            if in_vial is not None:
                PumpSample.run(
                    platform=platform,
                    sample_id=in_vial,
                    volume=-slug_volume,
                    flowrate=sampling_flowrate,
                    needle_position="bottom",
                    use_pump=pump,
                    delay_headspace_equilibration=0.5,
                    delay_before_dipping=0.0,
                    volume_before_dipping=0.0,
                    delay_after_pumping=0.1,
                    plunge_speed=plunge_speed,
                    dipping_speed=dipping_speed,
                    retract_speed=retract_speed,
                    move_speed=move_speed,
                )
        finally:
            pump["enable"] = "OFF"


class MixSlugSampler(DataFrameTask):
    """
    Mix a prepared reaction slug in the sampler mixing loop.
    The sampler should equip a larger diameter length of tubing as close as possible to the needle.
    Within this section, the slug is moved up and down repeatedly to try and create a homogeneous composition.

    Usage:
    call cls.run() to execute this task.
    """

    @classmethod
    def run(
        cls,
        platform: Platform,
        sampler: Sampler,
        amplitude: float,
        flowrate: float,
        cycles: int,
        use_pump: SyringePump | None = None,
        margin_bottom: float = 50.0,
        margin_flowrate: float = 0.5,
        plunge_speed: float = 500.0,
        retract_speed: float | None = None,
        move_speed: float | None = None,
    ) -> None:
        """
        Mix a prepared reaction slug in the sampler mixing loop.
        The sampler should equip a larger diameter length of tubing as close as possible to the needle.
        Within this section, the slug is moved up and down repeatedly to try and create a homogeneous composition.

        @param platform: Platform
            The platform running the experiment.
        @param sampler: Sampler
            The sampler which will perform the operation.
        @param amplitude: float
            Amplitude of the mixing oscillation in uL.
        @param flowrate: float
            Top speed of the oscillation motion in mL/min.
        @param cycles: int
            Number of mixing cycles to perform. Each cycle corresponds to an up-down motion.
        @param use_pump: SyringePump | None = None
            Override the default syringe pump to use in combination with the sampler specified by the sample_id entry.
            By default, the pump specified in the configuration file is used.
        @param margin_bottom: float = 50.0
            Additional volume to suck before mixing. Ensures there is a small margin between the liquid interface and
            the end of the mixing chamber when the slug is moved.
        @param margin_flowrate: float = 0.5
            This flowrate is used to suck the initial margin which is added before mixing. This should be similar to
            the sampling flowrate to avoid segmentation of the slug.
        @param plunge_speed: float = 500.0
            The needle is moved down into the vial at this speed [mm/min].
        @param retract_speed: float | None = None
            The needle is moved up from the vial at this speed [mm/min]. None uses default value from sampler.
        @param move_speed: float | None = None
            The sampler moves at this speed [mm/min]. None uses default value from sampler.
        """
        return super().run(
            platform=platform,
            sampler=sampler,
            amplitude=amplitude,
            flowrate=flowrate,
            cycles=cycles,
            use_pump=use_pump,
            margin_bottom=margin_bottom,
            margin_flowrate=margin_flowrate,
            plunge_speed=plunge_speed,
            retract_speed=retract_speed,
            move_speed=move_speed,
        )

    @classmethod
    def _validate_input(
        cls,
        platform: Platform,
        sampler: Sampler,
        amplitude: float,
        flowrate: float,
        cycles: int,
        use_pump: SyringePump | None = None,
        margin_bottom: float = 20.0,
        margin_flowrate: float = 0.5,
        plunge_speed: float = 500.0,
        retract_speed: float | None = None,
        move_speed: float | None = None,
    ) -> None:
        cls._validate_platform(platform)
        # samples validated by FindVial
        cls._validate_input_log(
            amplitude, lambda x: x > 0.0, "Amplitude must be > 0.0 ."
        )
        cls._validate_input_log(
            margin_bottom, lambda x: x >= 0.0, "margin_bottom must be >= 0.0 ."
        )
        cls._validate_input_log(
            cycles, lambda x: x >= 0, "Number of cycles must be >= 0."
        )
        if use_pump is not None:
            cls._validate_input_log(
                use_pump,
                SyringePump,
                f"Syringe pump must be of type 'SyringePump', not '{type(use_pump)}'.",
            )

    @classmethod
    def _execute(
        cls,
        platform: Platform,
        sampler: Sampler,
        amplitude: float,
        flowrate: float,
        cycles: int,
        use_pump: SyringePump | None = None,
        margin_bottom: float = 20.0,
        margin_flowrate: float = 0.5,
        plunge_speed: float = 500.0,
        retract_speed: float | None = None,
        move_speed: float | None = None,
    ) -> None:
        # Find one vial for mixing and one for sucking in the margin gas bubble
        vial_error_type = ""
        mixing_vial = FindVial.run(
            platform=platform, sampler=sampler, vial_type="Mixing"
        )
        if mixing_vial is None:
            vial_error_type = "Mixing"
        gas_vial = mixing_vial
        if margin_bottom > 0.0:
            gas_vial = FindVial.run(platform=platform, sampler=sampler, vial_type="Gas")
            if gas_vial is None:
                vial_error_type = "Gas"
        if not vial_error_type == "":
            error = NoSuitableVialError(
                f"No viable {vial_error_type.lower()} vial available on '{sampler}'",
                vial_type=vial_error_type,
            )
            cls.log(error)
            raise error
        # Find the autosampler pump, empty it
        if use_pump is None:
            # noinspection PyTypeChecker
            pump = cls._get_sampler_pump(platform, sampler)
        else:
            pump = use_pump
        if pump is None:
            error = RuntimeError(f"SyringePump for sampler '{sampler}' not found.")
            cls.log(error)
            raise error
        FillPump.run(pump=pump, fill=False)
        # Suck in the optional margin
        if cycles == 0:
            amplitude = 0.0
        if margin_bottom > 0.0:
            PumpSample.run(
                platform=platform,
                sample_id=gas_vial,
                volume=-margin_bottom - amplitude,
                flowrate=margin_flowrate,
                needle_position=0.3,
                use_pump=pump,
                update_volume=False,
                plunge_speed=plunge_speed,
                retract_speed=retract_speed,
                move_speed=move_speed,
            )
        # if pump is less than  90% full, go to 90% to allow good dynamic range without refilling.
        excess_volume = pump["volume"] - pump.syringe_volume * 0.9
        if excess_volume < 0.0:
            PumpVolume.run(pump=pump, volume=excess_volume, flowrate=4.0, reverse=True)
        # Go to the mixing vial
        if cycles > 0:
            PumpSample.run(
                platform=platform,
                sample_id=mixing_vial,
                volume=amplitude,
                auxiliary_needle=True,
                flowrate=flowrate,
                needle_position="top",
                use_pump=pump,
                retract=False,
                update_volume=False,
                plunge_speed=plunge_speed,
                retract_speed=retract_speed,
                move_speed=move_speed,
            )
            # Mix it up
            MixSlug.run(
                pump=pump,
                amplitude=amplitude,
                flowrate=flowrate,
                cycles=cycles - 1,
                push_then_pull=False,
            )
        cls._set_travel_position(sampler=sampler)
