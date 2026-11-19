"""Record the Tales Runner client area for one automation session."""

import ctypes
import threading
import time
from collections import deque
from contextlib import ExitStack, contextmanager
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import ImageGrab

from event_script.afk_conan import get_active_window
from game_supervisor import gameWindows
from screen_capture import grab_window


RECORDINGS_DIR = Path.home() / "Videos" / "Screen Recordings"
FPS = 60
DWMWA_EXTENDED_FRAME_BOUNDS = 9


def capture_frame(window):
    image, (x, y) = grab_window(window)
    frame = np.asarray(image)
    if frame.any():
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    # Some GPU-rendered windows return black pixels from window-only capture.
    if get_active_window() == window:
        visible = ImageGrab.grab(
            bbox=(x, y, x + image.width, y + image.height), all_screens=True,
        )
        if get_active_window() == window:
            frame = np.asarray(visible)
            if frame.any():
                return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    return None


def client_crop(window, width, height):
    handle = wintypes.HWND(window._hWnd)
    client = wintypes.RECT()
    origin = wintypes.POINT()
    bounds = wintypes.RECT()
    user32 = ctypes.windll.user32
    if not user32.GetClientRect(handle, ctypes.byref(client)) or not user32.ClientToScreen(
        handle, ctypes.byref(origin),
    ):
        return np.s_[:, :]
    result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        handle, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(bounds), ctypes.sizeof(bounds),
    )
    x, y = origin.x - bounds.left, origin.y - bounds.top
    right, bottom = x + client.right, y + client.bottom
    if result != 0 or not (0 <= x < right <= width and 0 <= y < bottom <= height):
        return np.s_[:, :]
    return np.s_[y:bottom, x:right]


@contextmanager
def window_source(window):
    frames = deque(maxlen=4)
    closed = threading.Event()
    latest = None
    dimensions = None
    crop = np.s_[:, :]

    def on_frame_arrived(frame, _control):
        nonlocal dimensions, crop
        size = (frame.width, frame.height)
        if size != dimensions:
            crop = client_crop(window, *size)
            dimensions = size
        # The native frame buffer is only valid during this callback.
        frames.append(cv2.cvtColor(frame.frame_buffer[crop], cv2.COLOR_BGRA2BGR))

    def on_closed():
        closed.set()

    def next_frame():
        nonlocal latest
        if closed.is_set():
            return None
        # Buffer short arrival bursts while keeping pending frames bounded.
        if frames:
            latest = frames.popleft()
        return latest

    try:
        from windows_capture import WindowsCapture

        capture = WindowsCapture(window_hwnd=window._hWnd, cursor_capture=False)
        capture.event(on_frame_arrived)
        capture.event(on_closed)
        controller = capture.start_free_threaded()
    except Exception as error:
        print(f"* Windows Graphics Capture unavailable; using slower foreground capture: {error} *", flush=True)
        yield lambda: capture_frame(window)
        return

    try:
        yield next_frame
    finally:
        if not controller.is_finished():
            controller.stop()


@contextmanager
def record_game_window():
    stop = threading.Event()
    worker = threading.Thread(target=record, args=(stop,), name="mcj-recording")
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join()


def record(stop):
    writer = None
    path = None
    wrote_frame = False
    previous_frame = None
    next_frame_at = 0.0
    interval = 1 / FPS
    source = ExitStack()
    current_window = None
    get_frame = None
    print("* MCJ recording enabled; waiting for Tales Runner *", flush=True)
    try:
        while not stop.is_set():
            try:
                window = next((window for window in gameWindows() if not window.isMinimized), None)
                if window != current_window:
                    source.close()
                    previous_frame = None
                    current_window = window
                    get_frame = source.enter_context(window_source(window)) if window is not None else None
                frame = get_frame() if get_frame is not None else None
            except OSError:
                frame = None  # The window may have closed between lookup and capture.
            if frame is None:
                previous_frame = None
                stop.wait(interval if get_frame is not None else 0.25)
                continue

            if writer is None:
                height, width = frame.shape[:2]
                size = (width - width % 2, height - height % 2)
                RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
                path = RECORDINGS_DIR / f"mcj_{datetime.now():%Y-%m-%d_%H%M%S_%f}.mp4"
                writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, size)
                if not writer.isOpened():
                    raise OSError(f"Could not open {path} for writing")
                print(f"* Recording Tales Runner to {path} *", flush=True)
            if (frame.shape[1], frame.shape[0]) != size:
                frame = cv2.resize(frame, size)

            now = time.monotonic()
            if previous_frame is None or now - next_frame_at > 1:
                next_frame_at = now
            # Fill missed samples to keep playback close to elapsed capture time.
            while next_frame_at + interval <= now:
                writer.write(previous_frame)
                next_frame_at += interval
            writer.write(frame)
            wrote_frame = True
            previous_frame = frame
            next_frame_at += interval
            stop.wait(max(0, next_frame_at - time.monotonic()))
    except (OSError, cv2.error) as error:
        print(f"* MCJ recording error: {error} *", flush=True)
    else:
        if writer is None:
            print("* No MCJ recording saved; no Tales Runner frames were captured *", flush=True)
    finally:
        try:
            source.close()
        finally:
            if writer is not None:
                writer.release()
                if wrote_frame:
                    print(f"* MCJ recording saved to {path} *", flush=True)
