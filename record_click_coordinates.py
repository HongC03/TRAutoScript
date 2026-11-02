"""Record manual and injected left-click coordinates on Windows.

Run with: python record_click_coordinates.py
Stop with Ctrl+C. Coordinates are appended to click_coordinates.csv.
"""

import csv
import ctypes
from ctypes import wintypes
from datetime import datetime
import os
from pathlib import Path


WH_MOUSE_LL = 14
HC_ACTION = 0
WM_LBUTTONDOWN = 0x0201
LLMHF_INJECTED = 0x00000001
OUTPUT_PATH = Path(__file__).resolve().with_name("click_coordinates.csv")


class POINT(ctypes.Structure):
    _fields_ = (("x", wintypes.LONG), ("y", wintypes.LONG))


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = (
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


def main():
    if os.name != "nt":
        raise OSError("This click recorder currently supports Windows only")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    hook_proc_type = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
    )

    user32.SetWindowsHookExW.argtypes = (
        ctypes.c_int, hook_proc_type, wintypes.HINSTANCE, wintypes.DWORD
    )
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.CallNextHookEx.argtypes = (
        ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
    )
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.UnhookWindowsHookEx.argtypes = (ctypes.c_void_p,)
    user32.GetMessageW.argtypes = (
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
    )
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
    user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
    kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    needs_header = not OUTPUT_PATH.exists()
    with OUTPUT_PATH.open("a", newline="", encoding="utf-8") as output_file:
        writer = csv.writer(output_file)
        if needs_header:
            writer.writerow(("timestamp", "x", "y", "injected"))

        def on_mouse_event(code, message, data):
            if code == HC_ACTION and message == WM_LBUTTONDOWN:
                event = ctypes.cast(
                    data, ctypes.POINTER(MSLLHOOKSTRUCT)
                ).contents
                timestamp = datetime.now().astimezone().isoformat(
                    timespec="milliseconds"
                )
                injected = bool(event.flags & LLMHF_INJECTED)
                writer.writerow((timestamp, event.pt.x, event.pt.y, injected))
                output_file.flush()
                source = "injected" if injected else "physical"
                print(
                    f"[{timestamp}] {source} click=({event.pt.x}, {event.pt.y})",
                    flush=True,
                )
            return user32.CallNextHookEx(None, code, message, data)

        callback = hook_proc_type(on_mouse_event)
        hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, callback, kernel32.GetModuleHandleW(None), 0
        )
        if not hook:
            raise ctypes.WinError(ctypes.get_last_error())

        print(f"Recording left-click events to: {OUTPUT_PATH}")
        print("Manual and injected clicks are recorded; press Ctrl+C to stop.")
        message = wintypes.MSG()
        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except KeyboardInterrupt:
            print("\nStopped recording.")
        finally:
            user32.UnhookWindowsHookEx(hook)


if __name__ == "__main__":
    main()
