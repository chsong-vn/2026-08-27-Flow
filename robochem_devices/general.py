"""
general.py — omniplatypus/utilities/general.py에서 디바이스/태스크 계층이 쓰는 함수만 발췌.

발췌 대상: parse_with_units, conversion_factor, run_all/ThreadEx,
          format_float, dict_to_str, path_to_configuration_folder
제거: numpy/pandas/ast 의존 (format_float의 np.isnan → value != value 트릭으로 대체)

설정 폴더: 환경변수 ROBOCHEM_CONFIG_DIR > ./config (원본은 패키지 내부 config 고정이었음)

수정: 2026-07 찬호 — Robochem_Flex(Apache-2.0) 코드에서 파생.
"""

import os
import re
from threading import Thread


def path_to_configuration_folder() -> str:
    """known_devices.json 등이 위치할 폴더. ROBOCHEM_CONFIG_DIR 환경변수로 지정 가능."""
    path = os.environ.get("ROBOCHEM_CONFIG_DIR", os.path.join(os.getcwd(), "config"))
    os.makedirs(path, exist_ok=True)
    return path


def format_float(value: float) -> str:
    """사람이 읽기 좋은 수 표현 (유효숫자 4자리)."""
    if value != value:  # NaN check without numpy
        return "NaN"
    return f"{value:.4g}"


def dict_to_str(value: dict, indentation: int = 0) -> str:
    """dict를 여러 줄 사람이 읽기 좋은 문자열로."""
    string = ""
    indentation_str = "  " * indentation
    for key, key_value in value.items():
        if isinstance(key_value, dict):
            formatted_value = dict_to_str(key_value, indentation + 1)
        elif isinstance(key_value, float):
            formatted_value = format_float(key_value)
        else:
            formatted_value = str(key_value)
        spacer = " "
        if "\n" in formatted_value:
            spacer = "\n"
        string += f"{indentation_str}{key}:{spacer}{formatted_value}\n"
    return string


_unit_prefixes = {
    "P": 1.0e15,
    "T": 1.0e12,
    "G": 1.0e9,
    "M": 1.0e6,
    "k": 1.0e3,
    "h": 1.0e2,
    "da": 1.0e1,
    "": 1.0,
    "d": 1.0e-1,
    "c": 1.0e-2,
    "m": 1.0e-3,
    "u": 1.0e-6,
    "n": 1.0e-9,
    "p": 1.0e-12,
    "f": 1.0e-15,
}

_numerical_pattern = re.compile(
    r"^\s*(?P<number>[+\-]?\d+(?:\.\d*)?(?:[eE]?[+\-]?\d+)?)\s*(?P<units>[a-zA-Z]+)"
)


def conversion_factor(from_units: str, to_units: str) -> float:
    """
    같은 기본단위에서 SI 접두사만 다른 두 단위 간 환산계수.
    예: conversion_factor('mL', 'uL') == 1000.0
    """
    from_units = from_units.replace("µ", "u")
    to_units = to_units.replace("µ", "u")
    if from_units == to_units:
        return 1.0

    def split(units: str) -> tuple[str, str]:
        # 가장 긴 접두사부터 매칭 (da 우선)
        for prefix in sorted(_unit_prefixes, key=len, reverse=True):
            if prefix and units.startswith(prefix) and len(units) > len(prefix):
                return prefix, units[len(prefix):]
        return "", units

    from_prefix, from_base = split(from_units)
    to_prefix, to_base = split(to_units)
    # 접두사를 뗐더니 기본단위가 다르면, 접두사 없는 해석도 시도
    if from_base != to_base:
        if from_units.endswith(to_units):
            from_prefix, from_base = from_units[: -len(to_units)], to_units
        elif to_units.endswith(from_units):
            to_prefix, to_base = to_units[: -len(from_units)], from_units
    if from_base != to_base:
        raise ValueError(f"Cannot convert between '{from_units}' and '{to_units}'.")
    if from_prefix not in _unit_prefixes or to_prefix not in _unit_prefixes:
        raise ValueError(f"Unsupported unit prefix in '{from_units}' or '{to_units}'.")
    return _unit_prefixes[from_prefix] / _unit_prefixes[to_prefix]


def parse_with_units(value: str, desired_units: str | None = None) -> tuple[float, str]:
    """
    '10.5mm', '1.0mL' 같은 단위 포함 문자열을 (수치, 단위)로 파싱.
    desired_units가 주어지면 SI 접두사 환산까지 수행.
    """
    match = _numerical_pattern.match(value)
    if match is None:
        raise ValueError(f"Unable to parse '{value}' as a number with units.")
    numerical_value = float(match.group("number"))
    given_units = match.group("units")
    if desired_units is None:
        return numerical_value, given_units
    return (
        numerical_value * conversion_factor(given_units, desired_units),
        desired_units,
    )


class ThreadEx(Thread):
    """run 중 발생한 예외를 저장했다가 raise_exception()으로 호출자 스레드에서 재발생."""

    def run(self):
        try:
            super().run()
        except Exception as e:
            self.exception = e

    def raise_exception(self):
        if hasattr(self, "exception") and self.exception is not None:
            raise self.exception


def run_all(*threads: Thread):
    """
    여러 스레드를 병렬 실행하고 전부 끝날 때까지 대기.
    어느 하나라도 예외를 던지면 호출자 스레드에서 첫 예외를 재발생.
    """
    for thread in threads:
        thread.__class__ = ThreadEx
        thread.exception = None
        thread.start()
    for thread in threads:
        thread.join()
    for thread in threads:
        if thread.exception is not None:
            thread.exception.add_note(f"Occurred in thread '{thread.name}'.")
            raise thread.exception
