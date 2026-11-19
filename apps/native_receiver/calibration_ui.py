"""Shared shot-noise reference progress display."""

from __future__ import annotations

from tkinter import ttk
from typing import Any

CALIBRATION_SECONDS = 10.0


class CalibrationProgress(ttk.Frame):
    def __init__(self, parent: Any, receiver: Any, *, length: int = 170) -> None:
        super().__init__(parent, style="Surface.TFrame")
        self.bar = ttk.Progressbar(
            self, variable=receiver.calibration_progress, maximum=CALIBRATION_SECONDS,
            length=length, mode="determinate", style="Reference.Horizontal.TProgressbar",
        )
        self.bar.pack(fill="x")
        ttk.Label(self, textvariable=receiver.calibration_text, style="Reference.TLabel",
                   width=27, anchor="e").pack(fill="x", pady=(3, 0))
