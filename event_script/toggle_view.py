"""Presentation for the Up/Right automation dashboard; Tk stays on its UI thread."""

import tkinter as tk


COLORS = {
    "background": "#F3F6FB", "panel": "#FFFFFF", "ink": "#17243B",
    "muted": "#68788F", "border": "#DEE6F0", "accent": "#2563EB",
    "accent_hover": "#1D4ED8", "green": "#147D52", "green_tint": "#E8F7EF",
    "amber": "#9A620E", "amber_tint": "#FFF3DB", "slate_tint": "#E8EEF6",
    "red": "#B93847", "red_tint": "#FFF0F2",
    "log": "#152238", "log_text": "#DDE7F6",
}


class ToggleDashboard:
    def __init__(self, root, toggle_hotkey, toggle, settings):
        self.root = root
        self.toggle_hotkey = toggle_hotkey.upper()
        self._last_events = None
        root.configure(bg=COLORS["background"])
        root.minsize(640, 1)
        root.resizable(True, True)
        body = tk.Frame(root, bg=COLORS["background"], padx=24, pady=20)
        body.pack(fill="both", expand=True)

        header = tk.Frame(body, bg=COLORS["background"])
        header.pack(fill="x", pady=(0, 18))
        title = tk.Frame(header, bg=COLORS["background"])
        title.pack(side="left")
        self.label(title, "TALES RUNNER AUTOMATION", fg=COLORS["muted"], font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.label(title, "Up / Right", font=("Segoe UI", 24, "bold")).pack(anchor="w", pady=(2, 0))
        self.badge = self.label(header, " STARTING ", bg=COLORS["slate_tint"], fg=COLORS["muted"], font=("Segoe UI", 9, "bold"), padx=12, pady=7)
        self.badge.pack(side="right")

        control = self.panel(body)
        control.pack(fill="x", pady=(0, 12))
        control_text = tk.Frame(control, bg=COLORS["panel"])
        control_text.pack(side="left", fill="x", expand=True)
        self.control_title = self.label(control_text, "Automation is starting", font=("Segoe UI", 11, "bold"))
        self.control_title.pack(anchor="w")
        self.control_hint = self.label(control_text, f"Press {self.toggle_hotkey} from any window", fg=COLORS["muted"], font=("Segoe UI", 9), wraplength=360)
        self.control_hint.pack(anchor="w", pady=(3, 0))
        self.button = tk.Button(
            control, text="Turn Off", command=toggle, font=("Segoe UI", 10, "bold"),
            bg=COLORS["accent"], fg="white", activebackground=COLORS["accent_hover"],
            activeforeground="white", disabledforeground=COLORS["muted"], relief="flat",
            borderwidth=0, cursor="hand2", padx=20, pady=10, takefocus=True,
        )
        self.button.pack(side="right", padx=(16, 0))

        metrics = tk.Frame(body, bg=COLORS["background"])
        metrics.pack(fill="x", pady=(0, 12))
        self.metrics = {}
        for column, (key, title_text, initial, caption) in enumerate((
            ("time", "SESSION TIME", "00:00:00", "Current run"),
            ("matches", "COMPLETED MATCHES", "0", "0 lifetime"),
            ("farm", "NEXT FARM", "—", "Not scheduled"),
        )):
            metrics.columnconfigure(column, weight=1, uniform="metrics")
            card = self.panel(metrics, padding=12)
            card.grid(row=0, column=column, sticky="nsew", padx=(0, 10) if column < 2 else 0)
            self.label(card, title_text, fg=COLORS["muted"], font=("Segoe UI", 8, "bold")).pack(anchor="w")
            value = self.label(card, initial, font=("Segoe UI", 20, "bold"))
            value.pack(anchor="w", pady=(4, 2))
            detail = self.label(card, caption, fg=COLORS["muted"], font=("Segoe UI", 9), wraplength=170)
            detail.pack(anchor="w")
            self.metrics[key] = (value, detail)

        activity = self.panel(body)
        activity.pack(fill="x", pady=(0, 12))
        self.label(activity, "LIVE ACTIVITY", fg=COLORS["muted"], font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.activity = self.label(activity, "Starting…", font=("Segoe UI", 11, "bold"), wraplength=550)
        self.activity.pack(anchor="w", pady=(5, 4))
        self.last_match = self.label(activity, "Last match end: —", fg=COLORS["muted"], font=("Segoe UI", 9))
        self.last_match.pack(anchor="w", pady=(0, 12))
        tk.Frame(activity, bg=COLORS["border"], height=1).pack(fill="x", pady=(0, 12))
        arrows = tk.Frame(activity, bg=COLORS["panel"])
        arrows.pack(fill="x")
        self.arrow_values = {}
        self.arrow_details = {}
        for column, arrow in enumerate(("Up", "Right")):
            arrows.columnconfigure(column, weight=1, uniform="arrows")
            cell = tk.Frame(arrows, bg=COLORS["panel"])
            cell.grid(row=0, column=column, sticky="w")
            self.label(cell, f"{arrow.upper()} INPUT", fg=COLORS["muted"], font=("Segoe UI", 8, "bold")).pack(anchor="w")
            value = self.label(cell, "Released", font=("Segoe UI", 11, "bold"))
            value.pack(anchor="w", pady=(3, 2))
            detail = self.label(cell, "—", fg=COLORS["muted"], font=("Segoe UI", 9))
            detail.pack(anchor="w")
            self.arrow_values[arrow], self.arrow_details[arrow] = value, detail

        log_header = tk.Frame(body, bg=COLORS["background"])
        log_header.pack(fill="x", pady=(2, 7))
        self.label(log_header, "Recent input", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.label(log_header, "HKT · sent events", fg=COLORS["muted"], font=("Segoe UI", 9)).pack(side="right")
        self.log = tk.Text(
            body, height=6, width=1, wrap="word", font=("Consolas", 9),
            bg=COLORS["log"], fg=COLORS["log_text"], relief="flat", borderwidth=0,
            padx=12, pady=12, highlightthickness=0, state="disabled", takefocus=False,
        )
        self.log.pack(fill="x", pady=(0, 12))
        self.log.tag_configure("time", foreground="#8398B6")
        self.log.tag_configure("ok", foreground="#80D5B1")
        self.log.tag_configure("failed", foreground="#FFA0A0")

        settings_area = tk.Frame(body, bg=COLORS["background"])
        settings_area.pack(fill="x", pady=(0, 10))
        settings_button = tk.Button(
            settings_area, text="Settings  ▸", font=("Segoe UI", 9, "bold"),
            bg=COLORS["background"], fg=COLORS["muted"], activebackground=COLORS["background"],
            activeforeground=COLORS["ink"], borderwidth=0, relief="flat", cursor="hand2", padx=0,
        )
        settings_button.pack(anchor="w")
        settings_panel = self.panel(settings_area)
        for row, (name, value) in enumerate(settings.items()):
            self.label(settings_panel, name, fg=COLORS["muted"], font=("Segoe UI", 9)).grid(row=row, column=0, sticky="nw", padx=(0, 20), pady=3)
            self.label(settings_panel, value, font=("Segoe UI", 9), wraplength=410).grid(row=row, column=1, sticky="w", pady=3)

        def show_settings():
            if settings_panel.winfo_manager():
                settings_panel.pack_forget()
                settings_button.configure(text="Settings  ▸")
            else:
                settings_panel.pack(fill="x", pady=(8, 0))
                settings_button.configure(text="Settings  ▾")
        settings_button.configure(command=show_settings)
        self.hotkey_label = self.label(body, f"{self.toggle_hotkey} ready · toggle on key release", fg=COLORS["muted"], font=("Segoe UI", 8), wraplength=560)
        self.hotkey_label.pack(anchor="w")
        self.label(body, "Input states reflect sent events, not the game's response.", fg=COLORS["muted"], font=("Segoe UI", 8)).pack(anchor="w", pady=(3, 0))

    @staticmethod
    def label(parent, text, **kwargs):
        options = {"bg": parent.cget("bg"), "fg": COLORS["ink"], "font": ("Segoe UI", 9), "anchor": "w", "justify": "left"}
        options.update(kwargs)
        return tk.Label(parent, text=text, **options)

    @staticmethod
    def panel(parent, padding=16):
        return tk.Frame(parent, bg=COLORS["panel"], padx=padding, pady=padding,
                        highlightbackground=COLORS["border"], highlightthickness=1)

    def set_run_state(self, running, stopping, error):
        if stopping:
            badge, ink, tint = "STOPPING", COLORS["amber"], COLORS["amber_tint"]
            title, hint = "Finishing this run", "Releasing keys and completing cleanup…"
        elif running:
            badge, ink, tint = "RUNNING", COLORS["green"], COLORS["green_tint"]
            title, hint = "Automation is active", f"Press {self.toggle_hotkey} from any window to stop"
        else:
            badge = "ERROR" if error else "STOPPED"
            ink = COLORS["red"] if error else COLORS["muted"]
            tint = COLORS["red_tint"] if error else COLORS["slate_tint"]
            title = "Automation is off" if not error else "Run ended with an error"
            hint = error or f"Press {self.toggle_hotkey} or Turn On to start again"
        self.badge.configure(text=badge, fg=ink, bg=tint)
        self.control_title.configure(text=title)
        self.control_hint.configure(text=hint)
        self.button.configure(
            text="Stopping…" if stopping else "Turn Off" if running else "Turn On",
            state="disabled" if stopping else "normal",
            bg=COLORS["slate_tint"] if stopping else COLORS["accent"],
            cursor="arrow" if stopping else "hand2",
        )

    def update_info(self, rows, snapshot, running):
        progress = snapshot.get("progress") or {}
        activity = rows.get("Activity", "Starting…")
        self.activity.configure(text=activity if running else f"Last activity: {activity}")
        self.last_match.configure(text=f"Last match end: {rows.get('Last match end', '—')}")
        self.metrics["time"][0].configure(text=rows.get("Session time", "00:00:00"))
        self.metrics["matches"][0].configure(text=str(progress.get("matches_finished", 0)))
        self.metrics["matches"][1].configure(text=f"{progress.get('persistent', {}).get('total_matches_finished', 0)} lifetime")
        farm = rows.get("Next farm", "Not scheduled")
        if activity == "running scheduled farm workflow":
            value, caption = "Running", "Farm workflow in progress"
        elif farm.startswith("Pending"):
            value, caption = "Pending", "After this match finishes"
        elif farm.startswith("In "):
            value, caption = farm[3:], "Time until scheduled run"
        else:
            value, caption = "—", "Not scheduled"
        self.metrics["farm"][0].configure(text=value)
        self.metrics["farm"][1].configure(text=caption)
        keys = snapshot.get("keys", {})
        for arrow in self.arrow_values:
            sent = keys.get(arrow, "Released")
            color = COLORS["green"] if sent == "Down sent" else COLORS["amber"] if sent == "Input failed" else COLORS["ink"]
            self.arrow_values[arrow].configure(text=sent, fg=color)
        self.arrow_details["Up"].configure(text=f"Next refresh: {rows.get('Next Up refresh', '—')}")
        count = snapshot.get("right_presses", 0)
        self.arrow_details["Right"].configure(text=f"{count} {'press' if count == 1 else 'presses'} this run")
        events = snapshot.get("events", [])
        if events != self._last_events:
            self._last_events = list(events)
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            if not events:
                self.log.insert("end", "Waiting for input events…", "time")
            for index, event in enumerate(events):
                if index:
                    self.log.insert("end", "\n")
                self.log.insert("end", event[:12], "time")
                body, separator, outcome = event[12:].rpartition("[")
                self.log.insert("end", body if separator else event[12:])
                if separator:
                    self.log.insert("end", "[" + outcome, "failed" if outcome.startswith("FAILED") else "ok")
            self.log.configure(state="disabled")

    def hotkey_received(self, timestamp):
        self.hotkey_label.configure(text=f"{self.toggle_hotkey} ready · last received {timestamp} HKT", fg=COLORS["muted"])

    def hotkey_unavailable(self):
        self.hotkey_label.configure(text="Hotkey unavailable · use the toggle button", fg=COLORS["amber"])
