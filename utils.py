"""Reusable screen-detection and input helpers."""

import random
import time
from contextlib import contextmanager

import pyautogui as gui
import pydirectinput as key
from PIL import Image

_screen_capture = None


@contextmanager
def use_screen_capture(capture):
    """Share a capture backend with this run's automation and Telegram threads."""
    global _screen_capture
    previous = _screen_capture
    _screen_capture = capture
    try:
        yield
    finally:
        _screen_capture = previous


def capture_screen(region=None):
    """Capture through the active backend, or use the default desktop capture."""
    if _screen_capture is not None:
        return _screen_capture.screenshot(region=region)
    if region is None:
        return gui.screenshot()
    return gui.screenshot(region=region)


def locate_on_screen(image_path, confidence=0.89, region=None):
    """Locate an image on the primary screen or an optional region."""
    if _screen_capture is not None:
        return _screen_capture.locate(image_path, confidence=confidence, region=region)
    if region is None:
        return gui.locateOnScreen(
            str(image_path),
            confidence=confidence,
        )
    return gui.locateOnScreen(
        str(image_path),
        confidence=confidence,
        region=region,
    )


def locate_on_screen_scaled(
    image_path, confidence=0.89, region=None,
    scales=(0.75, 0.875, 1.125, 1.25, 1.5, 1.75, 2.0),
    return_scale=False,
):
    """Locate an image at original or resized dimensions.

    When ``return_scale`` is true, return ``(position, scale)``. A missing
    match returns ``(None, None)``.
    """
    position = locate_on_screen(
        image_path, confidence=confidence, region=region
    )
    if position is not None:
        return (position, 1.0) if return_scale else position

    with Image.open(image_path) as template:
        for scale in scales:
            resized = template.resize((
                max(1, round(template.width * scale)),
                max(1, round(template.height * scale)),
            ), Image.Resampling.LANCZOS)
            if _screen_capture is not None:
                position = _screen_capture.locate(
                    resized, confidence=confidence, region=region
                )
            elif region is None:
                position = gui.locateOnScreen(
                    resized, confidence=confidence
                )
            else:
                position = gui.locateOnScreen(
                    resized, confidence=confidence, region=region
                )
            if position is not None:
                return (position, scale) if return_scale else position
    return (None, None) if return_scale else None


def _validatePolling(timeout, interval):
    if timeout < 0:
        raise ValueError("timeout 必須大於或等於 0")
    if interval < 0:
        raise ValueError("interval 必須大於或等於 0")


def pressButton(button_position):
    """Click the center of a detected screen region."""
    mouse_position = gui.center(button_position)
    key.click(mouse_position[0], mouse_position[1])


def autoPressButton(
    button, press_interval, stop_condition=None, random_interval=None
):
    """Press a keyboard button repeatedly at a configurable interval.

    The first press happens immediately. When ``random_interval`` is set, each
    subsequent delay is randomly selected within ``press_interval`` plus or
    minus that value. For example, ``press_interval=10`` and
    ``random_interval=2`` produces delays from 8 to 12 seconds.

    The loop continues until ``stop_condition`` returns True; when it is
    omitted, the loop runs until interrupted by the caller.
    """
    if press_interval < 0:
        raise ValueError("press_interval 必須大於或等於 0")
    if random_interval is not None and random_interval < 0:
        raise ValueError("random_interval 必須大於或等於 0")
    if stop_condition is not None and not callable(stop_condition):
        raise TypeError("stop_condition 必須為可呼叫物件")

    first_press = True
    while stop_condition is None or not stop_condition():
        if not first_press:
            delay = press_interval
            if random_interval is not None:
                delay = random.uniform(
                    max(0, press_interval - random_interval),
                    press_interval + random_interval,
                )
            time.sleep(delay)
        key.press(button)
        first_press = False


def waitForImage(image_path, timeout=10, interval=0.25, confidence=0.89):
    """Wait until an image appears and return its position, or None on timeout."""
    _validatePolling(timeout, interval)
    deadline = time.monotonic() + timeout

    while True:
        position = gui.locateOnScreen(str(image_path), confidence=confidence)
        if position is not None:
            return position

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(interval, remaining))


def waitForImageToDisappear(
    image_path, timeout=10, interval=0.25, confidence=0.89
):
    """Wait until an image disappears; return False if it remains at timeout."""
    _validatePolling(timeout, interval)
    deadline = time.monotonic() + timeout

    while True:
        position = gui.locateOnScreen(str(image_path), confidence=confidence)
        if position is None:
            return True

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(interval, remaining))


def triggerIfDetected(
    image_path,
    action="click",
    keyboard_key=None,
    confidence=0.89,
    region=None,
    scales=None,
):
    """Trigger a mouse click or key press when an image is visible.

    Args:
        image_path: Path to the image that should trigger the action.
        action: Either ``"click"`` or ``"key"``.
        keyboard_key: Key passed to pydirectinput when action is ``"key"``.
        confidence: Image matching confidence from 0 to 1.

    Returns:
        True when the image was detected and the action was triggered.
    """
    if action not in {"click", "key"}:
        raise ValueError("action 必須為 'click' 或 'key'")
    if action == "key" and not keyboard_key:
        raise ValueError("當 action 為 'key' 時，必須設定 keyboard_key")

    if scales:
        button_position = locate_on_screen_scaled(
            image_path, confidence=confidence, region=region, scales=scales
        )
    else:
        button_position = locate_on_screen(
            image_path, confidence=confidence, region=region
        )
    if button_position is None:
        return False

    if action == "click":
        pressButton(button_position)
    else:
        key.press(keyboard_key)
    return True


def triggerAndConfirm(
    trigger_image,
    expected_image,
    action="click",
    keyboard_key=None,
    timeout=10,
    interval=0.25,
    confidence=0.89,
    expected_confidence=None,
    retries=3,
    retry_delay=0.5,
):
    """Trigger an action and confirm that the expected image appears.

    ``timeout`` applies to each triggered attempt. The function returns True
    only after an action has been triggered and the expected image is found.
    """
    _validatePolling(timeout, interval)
    if retries < 1:
        raise ValueError("retries 必須大於或等於 1")
    if retry_delay < 0:
        raise ValueError("retry_delay 必須大於或等於 0")

    confirmation_confidence = (
        confidence if expected_confidence is None else expected_confidence
    )
    action_was_triggered = False

    for attempt in range(retries):
        # The expected screen may finish loading between retry attempts.
        if action_was_triggered:
            expected_position = gui.locateOnScreen(
                str(expected_image), confidence=confirmation_confidence
            )
            if expected_position is not None:
                return True

        triggered = triggerIfDetected(
            trigger_image,
            action=action,
            keyboard_key=keyboard_key,
            confidence=confidence,
        )
        if triggered:
            action_was_triggered = True
            if waitForImage(
                expected_image,
                timeout=timeout,
                interval=interval,
                confidence=confirmation_confidence,
            ) is not None:
                return True

        if attempt < retries - 1:
            time.sleep(retry_delay)

    if not action_was_triggered:
        return False
    return (
        gui.locateOnScreen(
            str(expected_image), confidence=confirmation_confidence
        )
        is not None
    )
