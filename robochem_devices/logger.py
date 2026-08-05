"""
logger.py — OmniPlatypus Logger 대체 어댑터.

원본(omniplatypus/utilities/logger.py)은 colorama + slack_sdk 의존.
이 버전은 표준출력 + 파일 + 콜백(=Qt signal 훅)만 지원하며,
디바이스 계층이 기대하는 인터페이스(Logger.log_message)를 그대로 유지한다.

PyQt5 연동 예시:
    from robochem_devices.logger import Logger
    Logger.add_callback(lambda msg, origin, level: my_qobject.log_signal.emit(msg, origin, level))

수정: 2026-07 찬호 — Robochem_Flex(Apache-2.0) 코드에서 파생, 전면 재작성.
"""

import os
from time import strftime
from threading import Lock
from typing import Callable

_LEVEL_TAGS = {
    "error": "[ERROR]",
    "warning": "[WARN ]",
    "ok": "[ OK  ]",
    "none": "[     ]",
}


class Logger:
    """
    스레드 안전 로거. 디바이스 코드는 Logger.log_message(message, origin=..., level=...)만 호출한다.
    원본이 받던 kwargs(indent, priority, subfolder 등)는 받아서 무시한다 (호환 목적).
    """

    _lock = Lock()
    _callbacks: list[Callable[[str, str, str], None]] = []
    _logfile_path: str | None = None
    console_enabled: bool = True

    @classmethod
    def add_callback(cls, callback: Callable[[str, str, str], None]) -> None:
        """(message, origin, level)을 받는 콜백 등록. Qt signal emit을 여기 연결."""
        with cls._lock:
            cls._callbacks.append(callback)

    @classmethod
    def clear_callbacks(cls) -> None:
        with cls._lock:
            cls._callbacks.clear()

    @classmethod
    def set_logfile(cls, path: str | None) -> None:
        """파일 로그 활성화. None이면 비활성."""
        if path is not None:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        cls._logfile_path = path

    @classmethod
    def log_message(
        cls, message: "str | Exception", origin: str = "unknown", **kwargs
    ) -> None:
        """
        원본과 동일 시그니처. level kwarg만 해석, 나머지는 무시.
        Exception 객체가 오면 level을 error로 승격.
        """
        level = kwargs.get("level", "none")
        if isinstance(message, Exception):
            if level in ("none", "ok"):
                level = "error"
            message = f"{type(message).__name__}: {message}"
        if level not in _LEVEL_TAGS:
            level = "none"

        line = f"{strftime('%H:%M:%S')} {_LEVEL_TAGS[level]} [{origin}] {message}"

        with cls._lock:
            if cls.console_enabled:
                print(line, flush=True)
            if cls._logfile_path is not None:
                try:
                    with open(cls._logfile_path, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
                except OSError:
                    pass
            callbacks = list(cls._callbacks)

        # 콜백은 락 밖에서 실행 (Qt signal emit이 느려도 로깅 락을 안 잡게)
        for cb in callbacks:
            try:
                cb(str(message), origin, level)
            except Exception:
                pass
