"""Dark Tk front end for recording and debugging MCJ macros."""

import threading
import tkinter as tk
import traceback
from datetime import datetime
from pathlib import Path
from queue import Empty, SimpleQueue
from tkinter import filedialog, ttk

import keyboard

from event_script.automation_toggle import register_toggle_hotkey

from .keyboard_macro import STOP_KEY, Recorder, load_macro, play_macro, save_macro


BG = "#101720"
PANEL = "#192432"
TEXT = "#e8eff8"
MUTED = "#a8bbcf"
ACCENT = "#84ccff"
FILE_TYPES = [("MCJ macro", "*.json"), ("All files", "*.*")]


class MacroStudio:
    def __init__(self, *, target_image, focus_check, run_automation):
        self.focus_check = focus_check
        self.run_automation = run_automation
        self.events = ()
        self.messages = SimpleQueue()
        self.stop_event = threading.Event()
        self.worker = None
        self.recorder = None
        self.record_hook = None
        self.timer = None
        self.mode = "Ready"
        self.closing = False
        self.root = tk.Tk()
        self.root.title("MCJ Macro Studio")
        self.root.configure(bg=BG)
        self.root.geometry("880x680")
        self.root.minsize(660, 500)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.build(target_image)
        self.remove_hotkey = None
        try:
            self.remove_hotkey = register_toggle_hotkey(
                STOP_KEY, lambda: self.messages.put(("stop", None)),
            )
        except Exception as error:
            self.log(f"F8 unavailable: {error}. Use the Stop button.")
        self.refresh()
        self.root.after(50, self.poll)

    def build(self, target_image):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=TEXT, borderwidth=0, rowheight=25)
        style.configure("Treeview.Heading", background="#273b50", foreground=TEXT)
        style.map("Treeview", background=[("selected", "#34516c")])

        body = tk.Frame(self.root, bg=BG, padx=22, pady=16)
        body.pack(fill="both", expand=True)

        def label(text, color=MUTED, font=("Segoe UI", 10)):
            tk.Label(body, text=text, bg=BG, fg=color, font=font).pack(anchor="w")

        label("MCJ Macro Studio", TEXT, ("Segoe UI", 23, "bold"))
        label(f"Auto replay  •  {target_image}  •  First successful check")
        self.status = tk.StringVar()
        tk.Label(body, textvariable=self.status, bg=BG, fg=ACCENT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(8, 2))
        self.info = tk.StringVar()
        tk.Label(body, textvariable=self.info, bg=BG, fg=MUTED,
                 font=("Segoe UI", 10)).pack(anchor="w")

        self.buttons = {}
        for actions in (
            (("Record", self.start_recording), ("Open", self.choose_macro),
             ("Save", self.write_macro)),
            (("Replay in game", lambda: self.start_playback(False)),
             ("Dry run", lambda: self.start_playback(True)),
             ("Arm automation", self.arm), ("Stop (F8)", self.stop)),
        ):
            bar = tk.Frame(body, bg=BG)
            bar.pack(fill="x", pady=(8, 0))
            for name, command in actions:
                button = tk.Button(
                    bar, text=name, command=command, bg=PANEL, fg=TEXT,
                    activebackground="#34516c", activeforeground=TEXT,
                    disabledforeground="#75879a", relief="flat",
                    padx=12, pady=5, font=("Segoe UI", 10),
                )
                button.pack(side="left", padx=(0, 6))
                self.buttons[name] = button

        tk.Frame(body, bg=BG, height=9).pack()
        label("Recorded events", TEXT, ("Segoe UI", 11, "bold"))
        table_frame = tk.Frame(body, bg=BG)
        table_frame.pack(fill="both", expand=True, pady=(4, 10))
        self.table = ttk.Treeview(
            table_frame, columns=("offset", "key", "action"), show="headings", height=7,
        )
        for column, title in (("offset", "Time (s)"), ("key", "Key"), ("action", "Action")):
            self.table.heading(column, text=title)
            self.table.column(column, width=100, anchor="center")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.table.pack(side="left", fill="both", expand=True)

        label("Debug log", TEXT, ("Segoe UI", 11, "bold"))
        log_frame = tk.Frame(body, bg=BG)
        log_frame.pack(fill="both", expand=True, pady=(4, 0))
        self.console = tk.Text(
            log_frame, height=7, bg=PANEL, fg=TEXT, font=("Consolas", 10),
            state="disabled", relief="flat", padx=8, pady=6, wrap="word",
        )
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.console.yview)
        self.console.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.console.pack(side="left", fill="both", expand=True)

    def run(self):
        self.root.mainloop()

    def log(self, message):
        self.messages.put(("log", f"{datetime.now():%H:%M:%S}  {message}"))

    def poll(self):
        for _ in range(200):
            try:
                kind, value = self.messages.get_nowait()
            except Empty:
                break
            if kind == "stop":
                self.stop()
            else:
                self.console.configure(state="normal")
                self.console.insert("end", value + "\n")
                if int(self.console.index("end").split(".")[0]) > 1500:
                    self.console.delete("1.0", "201.0")
                self.console.see("end")
                self.console.configure(state="disabled")
        if self.worker is not None and not self.worker.is_alive():
            self.worker.join()
            self.worker = None
            self.mode = "Ready"
            self.refresh()
        if self.closing and self.worker is None:
            self.root.destroy()
            return
        self.root.after(50, self.poll)

    def refresh(self):
        self.status.set(self.mode)
        ready = self.mode == "Ready" and not self.closing
        for name, button in self.buttons.items():
            enabled = ready and (bool(self.events) or name in ("Record", "Open"))
            if name == "Stop (F8)":
                enabled = self.mode not in ("Ready", "Stopping") and not self.closing
            button.configure(state="normal" if enabled else "disabled")

    def set_events(self, events, name):
        self.events = events
        self.table.delete(*self.table.get_children())
        for event in events:
            self.table.insert("", "end", values=(f"{event.offset:.3f}", event.key, event.action))
        duration = events[-1].offset if events else 0
        self.info.set(f"{name}  •  {len(events)} events  •  {duration:.3f}s")
        self.refresh()

    def choose_macro(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=FILE_TYPES)
        if path:
            self.open_macro(path)

    def open_macro(self, path):
        try:
            self.set_events(load_macro(path), Path(path).name)
            self.log(f"Opened {path}")
        except (OSError, ValueError) as error:
            self.log(f"Could not open macro: {error}")

    def write_macro(self):
        path = filedialog.asksaveasfilename(
            parent=self.root, defaultextension=".json", filetypes=FILE_TYPES,
        )
        if path:
            try:
                save_macro(path, self.events)
                self.info.set(f"{Path(path).name}  •  {len(self.events)} events  •  "
                              f"{self.events[-1].offset:.3f}s")
                self.log(f"Saved {path}")
            except OSError as error:
                self.log(f"Could not save macro: {error}")

    def start_recording(self):
        self.mode = "Recording starts in 3 seconds"
        self.refresh()
        self.log("Switch to the game. Press and release F8 to finish recording.")
        self.timer = self.root.after(3000, self.begin_capture)

    def begin_capture(self):
        self.timer = None
        recorder = Recorder()
        try:
            self.record_hook = keyboard.hook(recorder.capture)
        except Exception as error:
            self.mode = "Ready"
            self.refresh()
            self.log(f"Could not start recording: {error}")
            return
        self.recorder = recorder
        self.mode = "Recording"
        self.refresh()
        self.log("Recording keyboard input.")

    def stop(self):
        if self.timer is not None:
            self.root.after_cancel(self.timer)
            self.timer = None
            self.mode = "Ready"
        if self.recorder is not None:
            events = self.recorder.finish()
            skipped = self.recorder.skipped
            try:
                keyboard.unhook(self.record_hook)
            except Exception as error:
                self.log(f"Could not remove recorder hook: {error}")
            self.recorder = self.record_hook = None
            self.mode = "Ready"
            if events:
                self.set_events(events, "Unsaved recording")
            self.log(f"Recorded {len(events)} events.")
            if skipped:
                self.log(f"Skipped unsupported keys: {', '.join(sorted(skipped))}")
        if self.worker is not None:
            self.stop_event.set()
            self.mode = "Stopping"
        self.refresh()

    def start_worker(self, mode, run):
        self.stop_event.clear()
        self.mode = mode
        self.refresh()

        def execute():
            try:
                if run():
                    self.log("Run finished with an error.")
            except Exception as error:
                self.log(f"{mode} failed: {error}")
                traceback.print_exc()

        self.worker = threading.Thread(target=execute, name="mcj-macro")
        self.worker.start()

    def start_playback(self, dry_run):
        events = self.events

        def run():
            has_focus = None
            if not dry_run:
                self.log("Switch to Tales Runner within 3 seconds.")
                if self.stop_event.wait(3):
                    self.log("Replay stopped.")
                    return
                has_focus = self.focus_check()
            play_macro(events, cancelled=self.stop_event.is_set, log=self.log, has_focus=has_focus)

        self.start_worker("Dry run" if dry_run else "Manual replay", run)

    def arm(self):
        events = self.events
        self.start_worker(
            "Automation armed",
            lambda: self.run_automation(events, self.stop_event, self.log),
        )

    def close(self):
        self.closing = True
        self.stop()
        if self.remove_hotkey is not None:
            self.remove_hotkey()
