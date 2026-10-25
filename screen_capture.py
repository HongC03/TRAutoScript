"""Recoverable screen capture for monitor power transitions."""

import ctypes
from ctypes import wintypes

import pyautogui as gui
from PIL import Image


class ScreenCaptureUnavailable(OSError):
    """The desktop/game frame is temporarily unavailable; try again later."""


def _has_pixels(image):
    return (
        image.width > 0
        and image.height > 0
        and image.convert("RGB").getbbox() is not None
    )


def _grab_window(window):
    """Capture the client area and retain its current desktop coordinates."""
    from PIL import ImageGrab

    handle = int(window._hWnd)
    origin = wintypes.POINT(0, 0)
    if not ctypes.windll.user32.ClientToScreen(
        wintypes.HWND(handle), ctypes.byref(origin)
    ):
        raise OSError("Game window coordinates are unavailable")
    try:
        image = ImageGrab.grab(window=handle)
    except TypeError as error:
        raise ScreenCaptureUnavailable(
            "Game window capture requires Pillow 11.2.1 or newer"
        ) from error
    return image, (origin.x, origin.y)


class GameScreenCapture:
    """Use live desktop frames, falling back to the game's client area.

    No previous frame is reused: stale screenshots could trigger wrong clicks.
    Window capture still requires the game/GPU to render with the display off.
    """

    def __init__(self, window_provider):
        self.window_provider = window_provider

    def _frame(self):
        window = self.window_provider()
        try:
            image = gui.screenshot()
            if not _has_pixels(image):
                raise OSError("Desktop capture is empty or black")
            if window is not None:
                left, top = int(window.left), int(window.top)
                right = left + int(window.width)
                bottom = top + int(window.height)
                bounds = (
                    max(0, left), max(0, top),
                    min(image.width, right), min(image.height, bottom),
                )
                if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
                    raise OSError("Game is outside the primary display")
                if not _has_pixels(image.crop(bounds)):
                    raise OSError("Game area is black")
            return image, (0, 0)
        except (OSError, ValueError) as desktop_error:
            if window is None:
                raise ScreenCaptureUnavailable(str(desktop_error)) from desktop_error
            try:
                image, origin = _grab_window(window)
                if not _has_pixels(image):
                    raise OSError("Game window capture is empty or black")
                return image, origin
            except (OSError, ValueError) as window_error:
                raise ScreenCaptureUnavailable(
                    f"{desktop_error}; {window_error}"
                ) from window_error

    def _region_frame(self, region):
        image, origin = self._frame()
        if region is None:
            return image, origin
        left, top, width, height = map(int, region)
        x, y = left - origin[0], top - origin[1]
        if (
            x < 0 or y < 0 or width <= 0 or height <= 0
            or x + width > image.width or y + height > image.height
        ):
            raise ScreenCaptureUnavailable("Requested region is outside the captured frame")
        return image.crop((x, y, x + width, y + height)), (left, top)

    def screenshot(self, region=None):
        image, _ = self._region_frame(region)
        return image

    def locate(self, image_path, confidence=0.89, region=None):
        image, origin = self._region_frame(region)
        try:
            needle = (
                image_path
                if isinstance(image_path, Image.Image)
                else str(image_path)
            )
            position = gui.locate(needle, image, confidence=confidence)
        except ValueError as error:
            if "needle dimension(s) exceed" not in str(error):
                raise
            raise ScreenCaptureUnavailable(
                "Captured frame is smaller than the image template"
            ) from error
        if position is None:
            return None
        left, top, width, height = position
        return (left + origin[0], top + origin[1], width, height)
