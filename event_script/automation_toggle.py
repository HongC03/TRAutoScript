"""Small desktop toggle for a cooperatively stopped automation run."""

import threading
import traceback
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from queue import Empty, SimpleQueue


def elapsed_text(seconds):
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


class LiveAutomationInfo:
    """Share progress and sent input events without calling Tk from workers."""

    def __init__(self, settings, up_retrigger_seconds):
        self.settings = dict(settings)
        self.up_retrigger_seconds = up_retrigger_seconds
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self._status = None
            self._final_status = None
            self._events = deque(maxlen=3)
            self._keys = {"Up": "Released", "Right": "Released"}
            self._right_presses = 0
            self._next_up_at = None

    def bind_status(self, status):
        with self._lock:
            self._status = status

    def finish(self):
        with self._lock:
            status = self._status
        snapshot = None if status is None else status.snapshot()
        with self._lock:
            self._final_status = snapshot
            self._status = None
            self._next_up_at = None

    def record_input(self, action, result):
        timestamp = datetime.now(timezone(timedelta(hours=8))).strftime("%H:%M:%S.%f")[:-3]
        outcome = "OK" if result is True else "FAILED" if result is False else "unconfirmed"
        with self._lock:
            self._events.append(f"{timestamp}  {action} [{outcome}]")
            for button in self._keys:
                if action.startswith(f"{button} DOWN") or action.startswith(f"{button} UP"):
                    down = action.startswith(f"{button} DOWN")
                    self._keys[button] = (
                        "Input failed" if result is False else "Down sent" if down else "Up sent"
                    )
                    if button == "Up":
                        self._next_up_at = (
                            time.monotonic() + self.up_retrigger_seconds
                            if down and result is not False and self.up_retrigger_seconds > 0 else None
                        )
                    elif down and result is not False:
                        self._right_presses += 1
                    break

    def snapshot(self):
        with self._lock:
            status = self._status
            final_status = self._final_status
            result = {
                "keys": dict(self._keys), "events": list(self._events),
                "right_presses": self._right_presses,
                "next_up_seconds": None if self._next_up_at is None else max(0, self._next_up_at - time.monotonic()),
            }
        result["progress"] = status.snapshot() if status is not None else final_status
        return result

    def display_rows(self):
        snapshot = self.snapshot()
        progress = snapshot["progress"] or {}
        stage = progress.get("current_stage")
        activity = getattr(stage, "value", stage) or "Starting"
        activity = activity.replace("Conan match", "match")
        if progress.get("game_state"):
            activity += f" ({progress['game_state']})"
        if progress.get("farm_workflow_pending"):
            farm = "Pending — waiting for match end"
        elif progress.get("next_farm_seconds") is not None:
            farm = f"In {elapsed_text(progress['next_farm_seconds'])}"
        else:
            farm = "Not scheduled"
        refresh = (
            "Disabled" if self.up_retrigger_seconds == 0 else "—"
            if snapshot["next_up_seconds"] is None else f"In {elapsed_text(snapshot['next_up_seconds'])}"
        )
        persistent = progress.get("persistent", {})
        press_word = "press" if snapshot["right_presses"] == 1 else "presses"
        return {
            "Activity": activity,
            "Session time": elapsed_text(progress.get("uptime_seconds", 0)),
            "Matches completed": f"{progress.get('matches_finished', 0)} this run / {persistent.get('total_matches_finished', 0)} total",
            "Last match end": progress.get("last_match_at") or "—",
            "Next farm": farm,
            "Up input": snapshot["keys"]["Up"],
            "Right input": f"{snapshot['keys']['Right']} / {snapshot['right_presses']} {press_word}",
            "Next Up refresh": refresh,
        }


class AutomationSession:
    """Allow a fresh run only after the previous worker has finished cleanup."""

    def __init__(self, run):
        self.run = run
        self.worker = None
        self.stop_event = None
        self.error = None
        self.exit_code = None

    @property
    def running(self):
        return self.worker is not None and self.worker.is_alive()

    def start(self):
        if self.running:
            return False
        self.stop_event = threading.Event()
        self.error = None
        self.exit_code = None
        self.worker = threading.Thread(target=self._run, name="up-right-automation")
        self.worker.start()
        return True

    def _run(self):
        try:
            self.exit_code = self.run(self.stop_event)
        except Exception as error:
            self.error = str(error)
            self.exit_code = 1
            traceback.print_exc()

    def stop(self):
        if self.stop_event is not None:
            self.stop_event.set()


def register_toggle_hotkey(hotkey, callback):
    """Match a release directly, ignoring unrelated held keys such as Up."""
    import keyboard

    steps = keyboard.parse_hotkey(hotkey)
    if len(steps) != 1 or not steps[0] or any(not group for group in steps[0]):
        raise ValueError("Use a single key or combination, such as f8 or ctrl+f8")
    required_keys = [set(group) for group in steps[0]]
    trigger_keys = required_keys[-1]
    pressed = set()
    armed = False

    def on_event(event):
        nonlocal armed
        if event.event_type == "down":
            pressed.add(event.scan_code)
            if all(group.intersection(pressed) for group in required_keys):
                armed = True
        elif event.event_type == "up":
            pressed.discard(event.scan_code)
            if event.scan_code in trigger_keys:
                fire = armed
                armed = False
                if fire:
                    callback()

    handle = keyboard.hook(on_event, suppress=False)
    return lambda: keyboard.unhook(handle)


def run_toggle_window(run, *, toggle_hotkey="f8", live_info=None):
    """Run Tk on the main thread; automation and cleanup run in a worker."""
    import tkinter as tk
    from event_script.toggle_view import ToggleDashboard

    session = AutomationSession(run)
    root = tk.Tk()
    root.title("Up / Right automation")
    root.attributes("-topmost", True)
    closing = False
    hotkey_requests = SimpleQueue()
    remove_hotkey = None

    def toggle():
        if session.running:
            session.stop()
        else:
            session.start()
        update()

    view = ToggleDashboard(
        root, toggle_hotkey, toggle,
        {} if live_info is None else live_info.settings,
    )

    def request_toggle():
        # The keyboard callback runs on a listener thread. Tk stays on its own
        # thread, and key presses during shutdown/cleanup are ignored.
        if closing or (session.running and session.stop_event.is_set()):
            return
        hotkey_requests.put(None)

    def close():
        nonlocal closing
        closing = True
        session.stop()
        update()

    def update():
        running = session.running
        if not running and closing:
            root.destroy()
            return False
        view.set_run_state(
            running, running and session.stop_event.is_set(), session.error,
        )
        if live_info is not None:
            view.update_info(live_info.display_rows(), live_info.snapshot(), running)
        return True

    def poll():
        if not closing:
            while True:
                try:
                    hotkey_requests.get_nowait()
                except Empty:
                    break
                action = "off" if session.running else "on"
                received_at = datetime.now(timezone(timedelta(hours=8))).strftime("%H:%M:%S")
                view.hotkey_received(received_at)
                print(f"* {toggle_hotkey.upper()} received — turning {action} *", flush=True)
                toggle()
        if update():
            root.after(100, poll)

    root.protocol("WM_DELETE_WINDOW", close)
    try:
        try:
            remove_hotkey = register_toggle_hotkey(toggle_hotkey, request_toggle)
            print(f"* {toggle_hotkey.upper()}: toggle Up/Right automation on or off *", flush=True)
        except Exception as error:
            view.hotkey_unavailable()
            print(f"* Could not register toggle hotkey {toggle_hotkey!r}: {error} *", flush=True)
        session.start()
        poll()
        root.mainloop()
    finally:
        session.stop()
        try:
            if remove_hotkey is not None:
                remove_hotkey()
        finally:
            if session.worker is not None:
                session.worker.join()
    return session.exit_code or 0
