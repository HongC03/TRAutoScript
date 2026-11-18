"""Global hotkey control for cooperatively stopped automation runs."""

import threading
import traceback
from queue import Empty, SimpleQueue


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



def run_with_toggle(run, *, toggle_hotkey="f3"):
    """Start immediately; restart only after the worker cleans up."""
    requests = SimpleQueue()
    worker = None
    stop_event = None
    stopping = False
    result = [0]

    def start():
        nonlocal worker, stop_event
        stop_event = threading.Event()

        def execute():
            try:
                result[0] = run(stop_event) or 0
            except Exception:
                traceback.print_exc()
                result[0] = 1

        worker = threading.Thread(target=execute, name="up-right-automation")
        worker.start()

    remove_hotkey = register_toggle_hotkey(toggle_hotkey, lambda: requests.put(None))
    try:
        print(f"* {toggle_hotkey.upper()}: toggle automation on or off *", flush=True)
        start()
        while True:
            try:
                requests.get(timeout=0.1)
            except Empty:
                if worker is not None and not worker.is_alive():
                    worker.join()
                    worker = None
                    if not stopping:
                        return result[0]
                    stopping = False
                    print("* Automation off; press hotkey to start *", flush=True)
                continue
            if stopping:
                continue
            if worker is not None:
                if not worker.is_alive():
                    worker.join()
                    return result[0]
                print(f"* {toggle_hotkey.upper()} received — turning off *", flush=True)
                stopping = True
                stop_event.set()
            else:
                print(f"* {toggle_hotkey.upper()} received — turning on *", flush=True)
                start()
    finally:
        if stop_event is not None:
            stop_event.set()
        remove_hotkey()
        if worker is not None:
            worker.join()
