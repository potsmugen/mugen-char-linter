#!/usr/bin/env python3
"""
Mugen Char Linter GUI - a Tkinter front-end for mugen_char_linter.py.
Handles CNS-family (.cns/.cmd) and AIR (.air) files in one unified list. A
file's role is taken from its .def [Files] entry when it was added that way
(authoritative regardless of extension); files added directly fall back to
guessing by extension. Can auto-discover a character's files (state files,
.cmd, and .air) from its .def file.

Requires mugen_char_linter.py to be in the same folder (or on PYTHONPATH).

Usage:
    python mugen_char_linter_gui.py
"""

import os
import platform
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from mugen_char_linter import (
    classify_file,
    count_air_garbage_lines,
    count_garbage_lines,
    discover_air_file_from_def,
    discover_state_files_from_def,
    is_ikemen_def,
    plan_output_paths,
    read_text_file,
    render_batch_summary,
    render_file_report,
    run_air_file,
    run_cns_file,
)

# Layout settings (pixels unless noted). The window sizes itself to fit.
FILE_LIST_ROWS = 10          # visible rows in the input file list at the starting size
FILE_LIST_MIN_ROWS = 3       # the window can't shrink the file list below this
LOG_ROWS = 12                # visible rows in the log at the starting size
LOG_MIN_ROWS = 3             # the window can't shrink the log below this
TOGGLE_BUTTON_WIDTH = 10     # "Select all" / "Deselect all", in characters
LOG_FILTER_WIDTH = 24        # log filter box, in characters
GAP_XS, GAP_S, GAP_M, GAP_L = 2, 4, 6, 8  # spacing scale for padx/pady
TOOLTIP_DELAY_MS = 500       # hover time before a description appears
TOOLTIP_WRAP = 320           # description text wraps at this width
TOOLTIP_OFFSET = 4           # gap between a control and its description
TOOLTIP_BACKGROUND, TOOLTIP_FOREGROUND = "#ffffe0", "#000000"


class Tooltip:
    """Shows a description after hovering a widget for TOOLTIP_DELAY_MS."""

    def __init__(self, widget, text):
        self.widget, self.text = widget, text
        self._after_id = None
        self._tip = None
        widget.bind('<Enter>', self._schedule, add='+')
        widget.bind('<Leave>', self._hide, add='+')
        widget.bind('<ButtonPress>', self._hide, add='+')

    def _schedule(self, _event=None):
        self._hide()
        self._after_id = self.widget.after(TOOLTIP_DELAY_MS, self._show)

    def _show(self):
        self._after_id = None
        self._tip = tip = tk.Toplevel(self.widget)
        tip.withdraw()  # hidden until positioned, or it flashes at the screen corner
        tip.wm_overrideredirect(True)
        tk.Label(tip, text=self.text, justify="left", wraplength=TOOLTIP_WRAP,
                 background=TOOLTIP_BACKGROUND, foreground=TOOLTIP_FOREGROUND,
                 relief="solid", borderwidth=1, padx=GAP_S, pady=GAP_XS).pack()
        tip.update_idletasks()
        # Below the widget, kept inside the screen.
        x = min(self.widget.winfo_rootx() + TOOLTIP_OFFSET,
                tip.winfo_screenwidth() - tip.winfo_reqwidth())
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + TOOLTIP_OFFSET
        if y + tip.winfo_reqheight() > tip.winfo_screenheight():
            y = self.widget.winfo_rooty() - tip.winfo_reqheight() - TOOLTIP_OFFSET
        tip.wm_geometry(f"+{max(x, 0)}+{y}")
        tip.deiconify()

    def _hide(self, _event=None):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        if self._tip:
            self._tip.destroy()
            self._tip = None


def open_file(path: str) -> None:
    """Open a file with the operating system's default application."""
    path = os.path.abspath(path)
    system = platform.system()
    try:
        if system == 'Windows':
            os.startfile(path)
        elif system == 'Darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])
    except OSError as e:
        messagebox.showerror("Couldn't open file", str(e))


def open_file_location(path: str) -> None:
    """Reveal a file in the OS file manager (or just open its folder)."""
    path = os.path.abspath(path)
    system = platform.system()
    try:
        if system == 'Windows':
            if os.path.exists(path):
                # Raw string, not a list: list2cmdline quotes "/select,<path>" as a whole when
                # the path has spaces, and Explorer then falls back to Documents.
                subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
            else:
                os.startfile(os.path.dirname(path))
        elif system == 'Darwin':
            subprocess.Popen(['open', '-R', path])
        else:
            subprocess.Popen(['xdg-open', os.path.dirname(path)])
    except OSError as e:
        messagebox.showerror("Couldn't open file location", str(e))


class MugenToolkitGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.withdraw()  # hidden until sized, so it doesn't flicker while measuring
        self.title("Mugen Char Linter")
        self._set_window_icon()

        self.input_paths = []  # list of str, backs the listbox
        self.file_types = {}   # path -> 'cns'|'air', set only for .def-discovered files
        self._full_log = []    # every logged entry, unfiltered - powers the log filter box
        self.output_mode = tk.StringVar(value="overwrite")  # or "folder"
        self.output_folder = tk.StringVar()
        self.make_backup = tk.BooleanVar(value=True)
        self.do_diff = tk.BooleanVar(value=False)  # write a '<output>.diff' per changed file
        self.removal_mode = tk.StringVar(value="comment")  # delete/comment - applies globally
        self.do_garbage_lines = tk.BooleanVar(value=True)  # CNS+CMD text and AIR files
        self.do_remove_tagged_lines = tk.BooleanVar(value=False)

        # CNS-family fixers
        self.do_prune = tk.BooleanVar(value=True)
        self.do_dedupe = tk.BooleanVar(value=True)
        self.do_headers = tk.BooleanVar(value=True)
        self.do_check_values = tk.BooleanVar(value=True)
        self.do_negative_persistent = tk.BooleanVar(value=True)
        self._cns_vars = [self.do_prune, self.do_dedupe, self.do_check_values,
                          self.do_negative_persistent, self.do_headers]

        # CMD (.cmd's own Command/Remap/Defaults sections) fixers
        self.do_prune_cmd = tk.BooleanVar(value=True)
        self.do_dedupe_cmd = tk.BooleanVar(value=True)
        self._cmd_vars = [self.do_prune_cmd, self.do_dedupe_cmd]

        # AIR fixers
        self.do_dedupe_actions = tk.BooleanVar(value=True)
        self.do_bake_fallthrough = tk.BooleanVar(value=True)
        self.do_flag_empty = tk.BooleanVar(value=True)
        self._air_vars = [self.do_dedupe_actions, self.do_bake_fallthrough, self.do_flag_empty]
        self._global_vars = [self.do_garbage_lines, self.do_remove_tagged_lines]

        self._build_widgets()
        self._fit_to_content()

    def _fit_to_content(self):
        """Measure the window at the minimum rows, then at the starting rows."""
        self.file_listbox.config(height=FILE_LIST_MIN_ROWS)
        self.log_text.config(height=LOG_MIN_ROWS)
        self.update_idletasks()
        min_width, min_height = self.winfo_reqwidth(), self.winfo_reqheight()
        # Each resizable section stops at its own minimum (pady counts toward the row).
        for row, frame in ((0, self.file_frame), (4, self.log_frame)):
            self.rowconfigure(row, minsize=frame.winfo_reqheight() + 2 * GAP_S)
        self.file_listbox.config(height=FILE_LIST_ROWS)
        self.log_text.config(height=LOG_ROWS)
        self.update_idletasks()
        screen_height = self.winfo_screenheight()
        self.minsize(min_width, min(min_height, screen_height))
        self.deiconify()
        if self.winfo_reqheight() > screen_height:
            self._maximize()  # let the OS fit it to the usable screen area

    def _maximize(self):
        try:
            self.state('zoomed')  # Windows, macOS
        except tk.TclError:
            try:
                self.attributes('-zoomed', True)  # X11
            except tk.TclError:
                pass

    def _set_window_icon(self):
        """Use the bundled PNG for the running Tk window when available."""
        icon_path = os.path.join(os.path.dirname(__file__), "assets", "icon.png")
        try:
            self._window_icon = tk.PhotoImage(file=icon_path)
            self.iconphoto(True, self._window_icon)
        except (OSError, tk.TclError):
            # Source checkouts without the optional artwork still run normally.
            self._window_icon = None

    # ------------------------------------------------------------------
    def _build_widgets(self):
        pad = {'padx': GAP_L, 'pady': GAP_S}

        # --- Input files ---
        # Grid, not pack: pack squeezes the last section away before the others shrink.
        self.columnconfigure(0, weight=1)
        self.file_frame = file_frame = ttk.LabelFrame(self, text="Input files")
        file_frame.grid(row=0, column=0, sticky="nsew", **pad)
        self.rowconfigure(0, weight=1)

        list_row = ttk.Frame(file_frame)
        list_row.pack(fill="both", expand=True, padx=GAP_L, pady=(GAP_L, GAP_S))
        self.file_listbox = tk.Listbox(list_row, selectmode="extended",
                                       height=FILE_LIST_ROWS)
        self.file_listbox.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_row, orient="vertical", command=self.file_listbox.yview)
        scrollbar.pack(side="left", fill="y")
        self.file_listbox.config(yscrollcommand=scrollbar.set)
        self.file_listbox.bind('<Double-Button-1>', self._open_file_from_list)

        btn_col = ttk.Frame(file_frame)
        # Packed ahead of the list so shrinking squeezes the list, not the buttons.
        btn_col.pack(side="bottom", fill="x", padx=GAP_L, pady=(0, GAP_L), before=list_row)
        ttk.Button(btn_col, text="Add files...", command=self._add_files).pack(side="left")
        add_def = ttk.Button(btn_col, text="Add from .def...", command=self._add_from_def)
        add_def.pack(side="left", padx=(GAP_M, 0))
        Tooltip(add_def, "Add a character's state, command and animation files from its "
                         ".def file.")
        ttk.Button(btn_col, text="Remove selected", command=self._remove_selected).pack(
            side="left", padx=(GAP_M, 0))
        ttk.Button(btn_col, text="Clear all", command=self._clear_files).pack(
            side="left", padx=(GAP_M, 0))
        ttk.Button(btn_col, text="Open file location", command=self._open_selected_location).pack(
            side="left", padx=(GAP_M, 0))

        # --- CNS/CMD/AIR fixers, side by side to save vertical space ---
        fixers_row = ttk.Frame(self)
        fixers_row.grid(row=1, column=0, sticky="ew", **pad)

        cns_frame = ttk.LabelFrame(fixers_row, text="CNS checks")
        cns_frame.pack(side="left", fill="both", expand=True, padx=(0, GAP_S))
        self._build_toggle_row(cns_frame, self._cns_vars)
        self._check(cns_frame, "Unknown parameters", self.do_prune,
                    "Parameters that aren't recognized as valid for the type of state "
                    "controller, like typos or parameters from other state controller types.")
        self._check(cns_frame, "Duplicate parameters", self.do_dedupe,
                    "When a parameter appears more than once in the same state controller, "
                    "keep only the first one.")
        self._check(cns_frame, "Invalid values", self.do_check_values,
                    "Values that aren't recognized as valid for parameters with a fixed list "
                    "of options.")
        self._check(cns_frame, "No-op persistent", self.do_negative_persistent,
                    "'persistent' does nothing in negative states (-1, -2, -3), so it's "
                    "removed there.")
        self._check(cns_frame, "Normalize state headers", self.do_headers,
                    "Make each [State] header use the number of the [Statedef] it belongs to, "
                    "and tidy its formatting.")

        cmd_frame = ttk.LabelFrame(fixers_row, text="CMD checks")
        cmd_frame.pack(side="left", fill="both", expand=True, padx=GAP_S)
        self._build_toggle_row(cmd_frame, self._cmd_vars)
        self._check(cmd_frame, "Unknown parameters", self.do_prune_cmd,
                    "Parameters that aren't recognized as valid for the current block.")
        self._check(cmd_frame, "Duplicate parameters", self.do_dedupe_cmd,
                    "When a parameter appears more than once in the same block, keep only the "
                    "first one.")

        air_frame = ttk.LabelFrame(fixers_row, text="AIR checks")
        air_frame.pack(side="left", fill="both", expand=True, padx=(GAP_S, 0))
        self._build_toggle_row(air_frame, self._air_vars)
        self._check(air_frame, "Duplicate actions", self.do_dedupe_actions,
                    "When the same [Begin Action] number appears more than once, keep only the "
                    "first one.")
        self._check(air_frame, "Resolve empty actions", self.do_bake_fallthrough,
                    "Fill each empty animation with the content of the next animation, which "
                    "it would play anyway.")
        self._check(air_frame, "Report empty actions", self.do_flag_empty,
                    "List animations that are still empty, without changing them.")

        # --- Global checks + Removal mode + Output, side by side ---
        options_row = ttk.Frame(self)
        options_row.grid(row=2, column=0, sticky="ew", **pad)

        global_frame = ttk.LabelFrame(options_row, text="Global checks")
        global_frame.pack(side="left", fill="both", padx=(0, GAP_S))
        self._build_toggle_row(global_frame, self._global_vars)
        self._check(global_frame, "Garbage lines", self.do_garbage_lines,
                    "Lines that aren't recognized as valid code, like leftover notes or "
                    "broken text.")
        self._check(global_frame, "Delete tagged lines",
                    self.do_remove_tagged_lines,
                    "Delete lines that this tool commented out and tagged in an earlier run.")

        mode_frame = ttk.LabelFrame(options_row, text="When flagged for removal")
        mode_frame.pack(side="left", fill="both", padx=GAP_S)
        ttk.Radiobutton(mode_frame, text="Comment out", value="comment",
                         variable=self.removal_mode).pack(anchor="w", padx=GAP_L, pady=GAP_S)
        tag_radio = ttk.Radiobutton(mode_frame, text="Comment out and tag", value="tag",
                                    variable=self.removal_mode)
        tag_radio.pack(anchor="w", padx=GAP_L, pady=GAP_S)
        Tooltip(tag_radio, 'Comment out and add the reason, like "; [CNS Unknown Parameter] ...".')
        ttk.Radiobutton(mode_frame, text="Delete", value="delete",
                         variable=self.removal_mode).pack(anchor="w", padx=GAP_L, pady=GAP_S)

        out_frame = ttk.LabelFrame(options_row, text="Output")
        out_frame.pack(side="left", fill="both", expand=True, padx=(GAP_S, 0))
        ttk.Radiobutton(out_frame, text="Overwrite files", value="overwrite",
                         variable=self.output_mode, command=self._sync_output_state
                         ).grid(row=0, column=0, sticky="w", padx=GAP_L, pady=GAP_XS)
        self.backup_check = ttk.Checkbutton(out_frame, text="Create .bak backup",
                                             variable=self.make_backup)
        self.backup_check.grid(row=0, column=1, sticky="w", padx=GAP_L, pady=GAP_XS)
        Tooltip(self.backup_check, "Keep the original of each changed file as .bak, then "
                                   ".bak2, .bak3 on later runs.")

        ttk.Radiobutton(out_frame, text="Save copies to:", value="folder",
                         variable=self.output_mode, command=self._sync_output_state
                         ).grid(row=1, column=0, sticky="w", padx=GAP_L, pady=GAP_XS)
        self.folder_entry = ttk.Entry(out_frame, textvariable=self.output_folder, state="disabled")
        self.folder_entry.grid(row=1, column=1, sticky="ew", padx=(0, GAP_S), pady=GAP_XS)
        self.folder_browse_btn = ttk.Button(out_frame, text="Browse...",
                                             command=self._browse_folder, state="disabled")
        self.folder_browse_btn.grid(row=1, column=2, padx=(0, GAP_L), pady=GAP_XS)
        diff_check = ttk.Checkbutton(out_frame, text="Write .diff files", variable=self.do_diff)
        diff_check.grid(row=2, column=0, columnspan=3, sticky="w", padx=GAP_L,
                        pady=(GAP_M, GAP_XS))
        Tooltip(diff_check, "Save a .diff file next to each changed file, showing what "
                            "changed. Works with Analyze too.")
        out_frame.columnconfigure(1, weight=1)

        # --- Run buttons ---
        run_row = ttk.Frame(self)
        run_row.grid(row=3, column=0, pady=GAP_M)
        analyze_btn = ttk.Button(run_row, text="Analyze",
                                 command=lambda: self._run(dry_run=True))
        analyze_btn.pack(side="left", padx=GAP_S)
        Tooltip(analyze_btn, "Show what would change without editing any files.")
        run_btn = ttk.Button(run_row, text="Run", command=lambda: self._run(dry_run=False))
        run_btn.pack(side="left", padx=GAP_S)
        Tooltip(run_btn, "Apply the changes to the files.")

        # --- Log ---
        self.log_frame = log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.grid(row=4, column=0, sticky="nsew", **pad)
        self.rowconfigure(4, weight=1)
        log_btn_row = ttk.Frame(log_frame)
        log_btn_row.pack(fill="x", padx=GAP_S, pady=(GAP_S, 0))
        ttk.Button(log_btn_row, text="Clear log", command=self._clear_log).pack(side="right")
        self.log_filter = tk.StringVar()
        self.log_filter.trace_add("write", lambda *a: self._apply_log_filter())
        ttk.Label(log_btn_row, text="Filter:").pack(side="left")
        ttk.Entry(log_btn_row, textvariable=self.log_filter, width=LOG_FILTER_WIDTH).pack(
            side="left", padx=(GAP_S, 0))
        # width=1: let the controls above set the window width, not the log.
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap="word", state="disabled",
                                                  height=LOG_ROWS, width=1)
        self.log_text.pack(fill="both", expand=True, padx=GAP_S, pady=GAP_S)

    @staticmethod
    def _check(frame, text, variable, description):
        """Checkbox with a hover description."""
        box = ttk.Checkbutton(frame, text=text, variable=variable)
        box.pack(anchor="w", padx=GAP_L, pady=GAP_XS)
        Tooltip(box, description)

    def _build_toggle_row(self, frame, varlist):
        row = ttk.Frame(frame)
        row.pack(fill="x", padx=GAP_L, pady=(GAP_S, 0))
        ttk.Button(row, text="Select all", width=TOGGLE_BUTTON_WIDTH,
                   command=lambda: self._set_vars(varlist, True)).pack(side="left")
        ttk.Button(row, text="Deselect all", width=TOGGLE_BUTTON_WIDTH,
                   command=lambda: self._set_vars(varlist, False)).pack(
            side="left", padx=(GAP_S, 0))

    @staticmethod
    def _set_vars(varlist, value):
        for v in varlist:
            v.set(value)

    # ------------------------------------------------------------------
    def _sync_output_state(self):
        if self.output_mode.get() == "overwrite":
            self.folder_entry.config(state="disabled")
            self.folder_browse_btn.config(state="disabled")
            self.backup_check.config(state="normal")
        else:
            self.folder_entry.config(state="normal")
            self.folder_browse_btn.config(state="normal")
            self.backup_check.config(state="disabled")

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select file(s)",
            filetypes=[("M.U.G.E.N files", "*.cns *.cmd *.air *.def"), ("All files", "*.*")],
        )
        for path in paths:
            if path.lower().endswith('.def'):
                self._add_def_path(path)
            else:
                self._add_paths([path])

    def _add_from_def(self):
        def_path = filedialog.askopenfilename(
            title="Select character .def file",
            filetypes=[("DEF files", "*.def"), ("All files", "*.*")],
        )
        if def_path:
            self._add_def_path(def_path)

    def _add_def_path(self, def_path):
        try:
            ikemen_character = is_ikemen_def(def_path)
            if ikemen_character and not messagebox.askyesno(
                "Ikemen GO character",
                "WARNING:\nThis character's engine version is Ikemen GO. This linter "
                "targets M.U.G.E.N; if 'Remove unknown parameters' is enabled, "
                "Ikemen-exclusive parameters may be treated as unknown and removed.\n"
                "Continue?",
                default=messagebox.NO,
            ):
                return
            state_files = discover_state_files_from_def(def_path)
            air_file = discover_air_file_from_def(def_path)
        except OSError as e:
            messagebox.showerror("Error reading .def", str(e))
            return

        # Role comes from which .def key each path was found under -
        # authoritative regardless of what extension the file actually has.
        discovered = [(p, 'cns') for p in state_files]
        if air_file:
            discovered.append((air_file, 'air'))

        found = [(p, t) for p, t in discovered if os.path.isfile(p)]
        missing = [p for p, t in discovered if not os.path.isfile(p)]
        if not found:
            messagebox.showwarning(
                "No files found",
                "No state or animation files were found via this .def's [Files] section."
            )
            return
        self._add_paths([p for p, t in found], types=dict(found))

        if missing:
            messagebox.showwarning(
                "Some files missing",
                "These files are listed in the .def but weren't found on disk:\n\n"
                + "\n".join(missing)
            )

    @staticmethod
    def _path_key(path):
        # Dialogs and .def discovery spell the same path differently.
        return os.path.normcase(os.path.normpath(os.path.abspath(path)))

    def _add_paths(self, paths, types=None):
        types = {self._path_key(k): v for k, v in (types or {}).items()}
        for p in paths:
            key = self._path_key(p)
            existing = next((q for q in self.input_paths if self._path_key(q) == key), None)
            if existing is None:
                existing = os.path.normpath(os.path.abspath(p))
                self.input_paths.append(existing)
                self.file_listbox.insert("end", existing)
            if key in types:
                self.file_types[existing] = types[key]

    def _remove_selected(self):
        for idx in reversed(self.file_listbox.curselection()):
            self.file_listbox.delete(idx)
            removed_path = self.input_paths.pop(idx)
            self.file_types.pop(removed_path, None)

    def _clear_files(self):
        self.file_listbox.delete(0, "end")
        self.input_paths.clear()
        self.file_types.clear()

    def _open_file_from_list(self, event):
        index = self.file_listbox.nearest(event.y)
        bounds = self.file_listbox.bbox(index)
        if (not bounds or event.y < bounds[1]
                or event.y >= bounds[1] + bounds[3]):
            return
        self.file_listbox.selection_clear(0, 'end')
        self.file_listbox.selection_set(index)
        open_file(self.input_paths[index])

    def _open_selected_location(self):
        if not self.input_paths:
            messagebox.showinfo("No files", "Add a file to the list first.")
            return
        selection = self.file_listbox.curselection()
        index = selection[0] if selection else 0  # nothing selected: use the first file
        open_file_location(self.input_paths[index])

    def _browse_folder(self):
        path = filedialog.askdirectory(title="Choose output folder")
        if path:
            self.output_folder.set(path)

    def _log(self, text):
        self._full_log.append(text)
        if self._matches_filter(text):
            self.log_text.config(state="normal")
            self.log_text.insert("end", text + "\n")
            self.log_text.config(state="disabled")
            self.log_text.see("end")

    def _clear_log(self):
        self._full_log = []
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    def _matches_filter(self, text):
        needle = self.log_filter.get().strip().lower()
        return not needle or needle in text.lower()

    def _apply_log_filter(self):
        # Re-renders from the stored full log rather than re-running anything -
        # the filter only changes what's DISPLAYED, never what was logged.
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        for text in self._full_log:
            if self._matches_filter(text):
                self.log_text.insert("end", text + "\n")
        self.log_text.config(state="disabled")
        self.log_text.see("end")

    # ------------------------------------------------------------------
    def _run(self, dry_run=False):
        if not self.input_paths:
            messagebox.showerror("No files selected", "Please add at least one file.")
            return

        save_to_folder = self.output_mode.get() == "folder"
        output_folder = self.output_folder.get().strip()
        if save_to_folder:
            if not output_folder:
                messagebox.showerror("No output folder", "Please choose an output folder.")
                return
            if not os.path.isdir(output_folder):
                messagebox.showerror("Folder not found", f"'{output_folder}' does not exist.")
                return

        self._clear_log()

        if dry_run:
            self._log("=== DRY RUN - no files will be modified ===")
            self._log("")

        cns_files = [p for p in self.input_paths
                     if classify_file(p, self.file_types.get(p)) == 'cns']
        air_files = [p for p in self.input_paths
                     if classify_file(p, self.file_types.get(p)) == 'air']

        if cns_files or air_files:
            garbage_count = 0
            for path in cns_files:
                try:
                    garbage_count += count_garbage_lines(read_text_file(path).text)
                except OSError:
                    pass  # reported when the file is processed
            for path in air_files:
                try:
                    garbage_count += count_air_garbage_lines(read_text_file(path).text)
                except OSError:
                    pass
            if garbage_count > 0:
                self._log(f"Found {garbage_count} garbage line(s) across "
                          f"{len(cns_files) + len(air_files)} file(s).\n")

        totals = {'pruned': 0, 'duplicates_removed': 0, 'headers_normalized': 0,
                  'invalid_values_removed': 0, 'negative_persistent_removed': 0,
                  'garbage_lines_handled': 0, 'air_garbage_lines_handled': 0,
                  'cmd_pruned': 0, 'cmd_duplicates_removed': 0,
                  'duplicate_actions_removed': 0, 'empty_actions_baked': 0,
                  'empty_actions_flagged': 0, 'tagged_lines_removed': 0}
        processed, failed, diffs_written = 0, 0, 0
        output_paths = plan_output_paths(self.input_paths,
                                         output_folder if save_to_folder else None)

        for input_file in self.input_paths:
            output_file = output_paths[input_file]
            try:
                if classify_file(input_file, self.file_types.get(input_file)) == 'air':
                    log, stats, backup_path, diff_path, wrote = run_air_file(
                        input_file, output_file, self.do_dedupe_actions.get(),
                        self.do_bake_fallthrough.get(), self.do_flag_empty.get(),
                        self.do_garbage_lines.get(), self.removal_mode.get(),
                        self.make_backup.get(), self.do_diff.get(), dry_run,
                        do_remove_tagged_lines=self.do_remove_tagged_lines.get()
                    )
                else:
                    log, stats, backup_path, diff_path, wrote = run_cns_file(
                        input_file, output_file, self.do_prune.get(), self.do_dedupe.get(),
                        self.do_headers.get(), self.do_garbage_lines.get(),
                        self.do_prune_cmd.get(), self.do_dedupe_cmd.get(),
                        self.removal_mode.get(), self.make_backup.get(),
                        self.do_diff.get(), dry_run, self.do_check_values.get(),
                        self.do_remove_tagged_lines.get(),
                        self.do_negative_persistent.get()
                    )
            except Exception as e:  # windowed app: never fail silently
                self._log(f"=== {input_file} ===")
                self._log(f"  ERROR: {type(e).__name__}: {e}")
                self._log("")
                failed += 1
                continue

            processed += 1
            for key in totals:
                totals[key] += stats.get(key, 0)
            if diff_path:
                diffs_written += 1

            for line in render_file_report(
                input_file, log, stats, wrote, backup_path, diff_path, dry_run,
                output_file, heading=f"=== {input_file} ==="
            ):
                self._log(line)
            self._log("")

        for line in render_batch_summary(
            totals=totals, has_cns=bool(cns_files), has_air=bool(air_files),
            diffs_written=diffs_written, processed=processed, failed=failed,
            dry_run=dry_run, multi=True, do_diff=self.do_diff.get(),
            removal_mode=self.removal_mode.get(), heading="=== Batch summary ==="
        ):
            self._log(line)


if __name__ == '__main__':
    app = MugenToolkitGUI()
    app.mainloop()
