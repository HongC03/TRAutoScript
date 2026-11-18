"""Recording, file validation and cancellable playback for MCJ."""

import json
import math
import string
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import pydirectinput as keys


STOP_KEY = "f8"
PRINTABLE_KEYS = set(string.ascii_lowercase + string.digits + "`-=[]\\;',./")
PRINTABLE_SCAN_CODES = {keys.KEYBOARD_MAPPING[name]: name for name in PRINTABLE_KEYS}
KEY_NAMES = {
    *PRINTABLE_KEYS,
    "up", "down", "left", "right", "space", "enter", "escape", "tab",
    "backspace", "shiftleft", "shiftright", "ctrlleft", "altleft",
    *(f"f{i}" for i in range(1, 13)),
} - {STOP_KEY}
ALIASES = {
    "shift": "shiftleft", "left shift": "shiftleft", "right shift": "shiftright",
    "ctrl": "ctrlleft", "left ctrl": "ctrlleft",
    "alt": "altleft", "left alt": "altleft", "esc": "escape",
}


@dataclass(frozen=True)
class KeyEvent:
    offset: float
    key: str
    action: str


def load_macro(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Expected an MCJ macro file (version 1).")
    rows = data.get("events")
    if not isinstance(rows, list):
        raise ValueError("Missing macro events.")
    events = []
    held = set()
    last_offset = 0
    for index, row in enumerate(rows, 1):
        try:
            event = KeyEvent(**row)
            valid = (
                type(event.offset) in (int, float)
                and math.isfinite(event.offset)
                and event.offset >= last_offset
                and isinstance(event.key, str)
                and event.key in KEY_NAMES
                and event.action == ("up" if event.key in held else "down")
            )
        except (TypeError, OverflowError):
            valid = False
        if not valid:
            raise ValueError(f"Invalid key event at row {index}.")
        held.symmetric_difference_update({event.key})
        last_offset = event.offset
        events.append(event)
    if not events or held:
        raise ValueError("The macro needs at least one key press and must release every key.")
    return tuple(events)


def save_macro(path, events):
    data = {"version": 1, "events": [asdict(event) for event in events]}
    Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class Recorder:
    def __init__(self):
        self._started = time.monotonic()
        self._lock = threading.Lock()
        self._closed = False
        self._events = []
        self._held = {}
        self.skipped = set()

    def capture(self, raw):
        with self._lock:
            if self._closed or raw.name == STOP_KEY:
                return
            # The hook's printable name can change if Shift is released first.
            name = PRINTABLE_SCAN_CODES.get(raw.scan_code, ALIASES.get(raw.name, raw.name))
            if raw.is_keypad or name not in KEY_NAMES:
                self.skipped.add(raw.name or "unknown")
                return
            if raw.event_type == "down" and name not in self._held:
                self._held[name] = None
            elif raw.event_type == "up" and name in self._held:
                del self._held[name]
            else:
                return
            self._events.append(KeyEvent(time.monotonic() - self._started, name, raw.event_type))

    def finish(self):
        with self._lock:
            self._closed = True
            offset = time.monotonic() - self._started
            self._events.extend(KeyEvent(offset, name, "up") for name in self._held)
            self._held.clear()
            return tuple(self._events)


def play_macro(events, *, cancelled, log, has_focus=None):
    """Replay once. With no focus check, simulate timing and log without sending."""
    start = time.monotonic()
    held = {}
    try:
        for index, event in enumerate(events, 1):
            deadline = start + event.offset
            while True:
                if cancelled():
                    log("Replay stopped.")
                    return False
                if has_focus is not None and not has_focus():
                    log("Replay stopped because the target lost focus.")
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(0.05, remaining))
            if has_focus is not None:
                if event.action == "down":
                    held[event.key] = None
                    sent = keys.keyDown(event.key, _pause=False)
                else:
                    sent = keys.keyUp(event.key, _pause=False)
                    if sent:
                        del held[event.key]
                if not sent:
                    raise RuntimeError(f"Windows rejected {event.key} {event.action}.")
            log(f"#{index}  {event.offset:.3f}s  {event.key} {event.action}  "
                f"(actual {time.monotonic() - start:.3f}s)")
        log("Dry run complete." if has_focus is None else "Replay complete.")
        return True
    finally:
        for name in reversed(held):
            try:
                # Cleanup must still send key-up if the mouse fail-safe stopped replay.
                released = keys.keyUp.__wrapped__(name, _pause=False)
                log(f"Cleanup: {name} up {'sent' if released else 'failed'}.")
            except Exception as error:
                log(f"Cleanup failed for {name}: {error}")
