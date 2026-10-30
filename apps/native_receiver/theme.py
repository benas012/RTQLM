from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from apps.native_receiver.constants import (
    COLORS,
    FONTS,
)


def configure_style(window: tk.Tk) -> None:
    window.configure(bg=COLORS["bg"])
    for name in ("TkDefaultFont", "TkTextFont"):
        tkfont.nametofont(name, root=window).configure(family="Segoe UI", size=10)
    style = ttk.Style(window)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", background=COLORS["bg"], foreground=COLORS["text"],
                    font=FONTS["body"], bordercolor=COLORS["border_subtle"],
                    lightcolor=COLORS["surface"], darkcolor=COLORS["surface"])
    style.configure("TFrame", background=COLORS["surface"])
    style.configure("Panel.TFrame", background=COLORS["surface"])
    style.configure("Sidebar.TFrame", background=COLORS["sidebar"])
    style.configure("Surface.TFrame", background=COLORS["surface"])
    style.configure("Toolbar.TFrame", background=COLORS["surface_elevated"])
    style.configure("Metric.TFrame", background=COLORS["surface_elevated"])
    style.configure("Status.TFrame", background=COLORS["surface"])
    style.configure("ControlGroup.TFrame", background=COLORS["surface_elevated"])
    style.configure("Diagnostics.TFrame", background=COLORS["bg"])
    style.configure("Analysis.TNotebook", background=COLORS["surface"], borderwidth=0,
                    bordercolor=COLORS["surface"], lightcolor=COLORS["surface"],
                    darkcolor=COLORS["surface"])
    style.configure(
        "Analysis.TNotebook.Tab",
        background=COLORS["surface_elevated"],
        foreground=COLORS["text_secondary"],
        padding=(20, 9),
        font=FONTS["body_bold"],
    )
    style.map(
        "Analysis.TNotebook.Tab",
        background=[("selected", COLORS["selected"]), ("active", COLORS["hover"])],
        foreground=[("selected", COLORS["text"]), ("active", COLORS["text"])],
        bordercolor=[("!disabled", COLORS["surface"])],
        lightcolor=[("!disabled", COLORS["surface"])],
        darkcolor=[("!disabled", COLORS["surface"])],
    )
    style.configure(
        "TLabelframe",
        background=COLORS["panel"],
        foreground=COLORS["text"],
        bordercolor=COLORS["border_subtle"],
        relief="solid",
    )
    style.configure(
        "TLabelframe.Label",
        background=COLORS["surface"],
        foreground=COLORS["text_secondary"],
        font=FONTS["body_bold"],
    )
    style.configure("TLabel", background=COLORS["surface"], foreground=COLORS["text"])
    style.configure(
        "Muted.TLabel",
        background=COLORS["surface"],
        foreground=COLORS["text_secondary"],
        font=FONTS["body"],
    )
    style.configure(
        "SidebarMuted.TLabel",
        background=COLORS["sidebar"],
        foreground=COLORS["text_secondary"],
        font=FONTS["body"],
    )
    style.configure(
        "Tooltip.TLabel",
        background=COLORS["panel_3"],
        foreground=COLORS["text"],
        bordercolor=COLORS["grid"],
        relief="solid",
        font=FONTS["body"],
    )
    style.configure(
        "SidebarTitle.TLabel",
        background=COLORS["sidebar"],
        foreground=COLORS["text"],
        font=FONTS["title"],
    )
    style.configure(
        "Title.TLabel",
        background=COLORS["panel"],
        foreground=COLORS["text"],
        font=("Segoe UI", 16, "bold"),
    )
    style.configure(
        "Section.TLabel",
        background=COLORS["panel_2"],
        foreground=COLORS["text"],
        font=FONTS["section"],
    )
    style.configure(
        "Toolbar.TLabel",
        background=COLORS["panel_2"],
        foreground=COLORS["text_secondary"],
        font=FONTS["body"],
    )
    style.configure(
        "Value.TLabel",
        background=COLORS["panel"],
        foreground=COLORS["text"],
        font=FONTS["mono"],
    )
    style.configure(
        "MetricValue.TLabel",
        background=COLORS["panel_2"],
        foreground=COLORS["text"],
        font=FONTS["mono_bold"],
    )
    style.configure(
        "StatusLabel.TLabel",
        background=COLORS["surface"],
        foreground=COLORS["text_secondary"],
        font=FONTS["small"],
    )
    style.configure(
        "StatusValue.TLabel",
        background=COLORS["surface"],
        foreground=COLORS["text"],
        font=FONTS["mono_bold"],
    )
    style.configure(
        "GroupLabel.TLabel",
        background=COLORS["surface_elevated"],
        foreground=COLORS["text_secondary"],
        font=FONTS["body_bold"],
    )
    style.configure(
        "TEntry",
        fieldbackground=COLORS["panel_2"],
        readonlybackground=COLORS["panel_2"],
        foreground=COLORS["text"],
        bordercolor=COLORS["grid"],
        insertcolor=COLORS["text"],
        padding=(7, 5),
        lightcolor=COLORS["panel_2"], darkcolor=COLORS["panel_2"],
        relief="flat",
    )
    style.map(
        "TEntry",
        fieldbackground=[
            ("readonly", COLORS["panel_2"]),
            ("focus", COLORS["panel_2"]),
            ("disabled", COLORS["panel"]),
        ],
        foreground=[("readonly", COLORS["text"]), ("disabled", COLORS["muted"])],
        bordercolor=[("focus", COLORS["accent"]), ("readonly", COLORS["grid"])],
    )
    style.configure(
        "TButton",
        background=COLORS["surface_elevated"],
        foreground=COLORS["text"],
        bordercolor=COLORS["border_subtle"],
        focuscolor=COLORS["accent"],
        focusthickness=1,
        padding=(12, 7),
        font=FONTS["body_bold"],
        relief="flat", lightcolor=COLORS["surface_elevated"],
        darkcolor=COLORS["surface_elevated"], borderwidth=1,
    )
    style.map(
        "TButton",
        background=[
            ("disabled", COLORS["surface"]),
            ("active", COLORS["hover"]),
            ("pressed", COLORS["surface_active"]),
        ],
        foreground=[("disabled", COLORS["text_disabled"])],
        bordercolor=[("focus", COLORS["accent"]), ("active", COLORS["border_strong"])],
    )
    style.configure(
        "Accent.TButton",
        background=COLORS["accent"],
        foreground=COLORS["bg_deep"],
        bordercolor=COLORS["accent"],
        lightcolor=COLORS["accent"], darkcolor=COLORS["accent"],
    )
    style.map(
        "Accent.TButton",
        background=[
            ("disabled", COLORS["surface_active"]),
            ("active", COLORS["accent_hover"]),
            ("pressed", COLORS["accent"]),
        ],
        foreground=[("disabled", COLORS["text_disabled"]), ("!disabled", COLORS["bg_deep"])],
    )
    style.configure(
        "Danger.TButton",
        background=COLORS["surface_elevated"],
        foreground=COLORS["danger"],
        bordercolor=COLORS["border_subtle"],
    )
    style.map(
        "Danger.TButton",
        background=[("active", "#3a2029"), ("pressed", "#46232e")],
        bordercolor=[("focus", COLORS["danger"]), ("active", COLORS["danger"])],
    )
    style.configure(
        "Active.TButton",
        background=COLORS["selected"],
        foreground=COLORS["text"],
        bordercolor=COLORS["accent"],
    )
    style.map(
        "Active.TButton",
        background=[("active", COLORS["selected"]), ("pressed", COLORS["surface_active"])],
    )
    style.configure("Compact.TButton", padding=(9, 5), font=FONTS["body"])
    style.configure("Quiet.TButton", background=COLORS["surface"],
                    bordercolor=COLORS["surface"], padding=(9, 5), font=FONTS["body"])
    style.configure("Hero.TLabel", background=COLORS["surface"],
                    foreground=COLORS["text"], font=FONTS["metric"])
    style.configure("Accent.TLabel", background=COLORS["surface"],
                    foreground=COLORS["accent"], font=FONTS["body_bold"])
    style.configure("Chip.TLabel", background=COLORS["surface_elevated"],
                    foreground=COLORS["text_secondary"], padding=(9, 5), font=FONTS["small"])
    style.configure("TPanedwindow", background=COLORS["bg"])
    style.configure(
        "TCheckbutton",
        background=COLORS["surface"],
        foreground=COLORS["text"],
        indicatorcolor=COLORS["panel_2"],
    )
    style.configure(
        "Toolbar.TCheckbutton",
        background=COLORS["surface_elevated"],
        foreground=COLORS["text"],
        indicatorcolor=COLORS["surface_active"],
        padding=(4, 2),
    )
    style.map(
        "Toolbar.TCheckbutton",
        indicatorcolor=[("selected", COLORS["accent"]), ("disabled", COLORS["surface"])],
        foreground=[("disabled", COLORS["text_disabled"])],
    )
    style.configure("CH1.Toolbar.TCheckbutton", foreground=COLORS["ch1"])
    style.configure("CH2.Toolbar.TCheckbutton", foreground=COLORS["ch2"])
    style.configure(
        "TCombobox",
        fieldbackground=COLORS["panel_2"],
        background=COLORS["panel_2"],
        foreground=COLORS["text"],
        bordercolor=COLORS["grid"],
        arrowcolor=COLORS["text"],
        selectbackground=COLORS["panel_2"],
        selectforeground=COLORS["text"],
        insertcolor=COLORS["text"],
        padding=(6, 5), arrowsize=12,
        lightcolor=COLORS["panel_2"], darkcolor=COLORS["panel_2"], relief="flat",
    )
    style.map(
        "TCombobox",
        fieldbackground=[
            ("readonly", COLORS["panel_2"]),
            ("focus", COLORS["panel_2"]),
            ("disabled", COLORS["panel"]),
        ],
        background=[
            ("readonly", COLORS["panel_2"]),
            ("active", COLORS["panel_3"]),
            ("disabled", COLORS["panel"]),
        ],
        foreground=[("readonly", COLORS["text"]), ("disabled", COLORS["muted"])],
        selectbackground=[
            ("readonly", COLORS["panel_2"]),
            ("focus", COLORS["panel_2"]),
        ],
        selectforeground=[("readonly", COLORS["text"]), ("focus", COLORS["text"])],
        bordercolor=[("focus", COLORS["accent"]), ("readonly", COLORS["grid"])],
        arrowcolor=[
            ("disabled", COLORS["muted"]),
            ("active", "#ffffff"),
            ("readonly", COLORS["text"]),
        ],
    )
    window.option_add("*TCombobox*Listbox.background", COLORS["panel_2"])
    window.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
    window.option_add("*TCombobox*Listbox.selectBackground", COLORS["accent"])
    window.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    style.configure(
        "Device.Treeview",
        background=COLORS["sidebar"],
        fieldbackground=COLORS["sidebar"],
        foreground=COLORS["text"],
        bordercolor=COLORS["border_subtle"],
        rowheight=66,
        relief="flat", lightcolor=COLORS["sidebar"], darkcolor=COLORS["sidebar"],
        font=FONTS["body"],
    )
    style.map(
        "Device.Treeview",
        background=[("selected", COLORS["selected"])],
        foreground=[("selected", COLORS["text"])],
        bordercolor=[("focus", COLORS["accent"])],
    )
    style.configure(
        "Device.Treeview.Heading",
        background=COLORS["sidebar"],
        foreground=COLORS["text_secondary"],
        relief="flat",
        font=FONTS["small"],
    )
    style.configure("TSeparator", background=COLORS["border_subtle"])
    style.configure("Reference.Horizontal.TProgressbar", background=COLORS["accent"],
                    troughcolor=COLORS["surface_active"], bordercolor=COLORS["surface"],
                    lightcolor=COLORS["accent"], darkcolor=COLORS["accent"], thickness=7)
    style.configure("Reference.TLabel", background=COLORS["surface"],
                    foreground=COLORS["text_secondary"], font=FONTS["small"])
    style.configure("Vertical.TScrollbar", background=COLORS["surface_active"],
                    troughcolor=COLORS["surface"], borderwidth=0, arrowsize=12)
