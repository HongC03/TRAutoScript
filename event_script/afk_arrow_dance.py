"""Read the Tales Runner instructor's poses and repeat them with arrow keys."""

import argparse
import ctypes
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np


# Coordinates measured on the supplied 1288 × 990 recording.
FRAME_SIZE = (1288, 990)
EYES = (500, 228, 780, 390)
TIMER = (319, 163, 463, 189)
STABLE_POSE_SECONDS = 0.065
ANSWER_IDLE_SECONDS = 0.18
RESET_SECONDS = 0.20
MIN_ARROWS = 3
MAX_ARROWS = 12
DEBUG_WINDOW = "Arrow Dance Debug"


@dataclass(frozen=True)
class Observation:
    timer_pixels: int
    pose: str
    eyes: tuple

    @property
    def round_active(self):
        return self.timer_pixels > 400


def observe(frame):
    """Classify the direction by the instructor's sunglass positions."""
    x1, y1, x2, y2 = EYES
    mask = cv2.inRange(frame[y1:y2, x1:x2], (0, 0, 0), (52, 56, 65))
    _, _, stats, centers = cv2.connectedComponentsWithStats(mask)
    eyes = tuple(
        (round(x + x1), round(y + y1), int(row[4]))
        for row, (x, y) in zip(stats[1:], centers[1:])
        if 230 <= row[4] <= 1900
    )

    def has_eye(x_range, y_range, min_area):
        return any(
            x_range[0] <= x <= x_range[1]
            and y_range[0] <= y <= y_range[1]
            and area >= min_area
            for x, y, area in eyes
        )

    poses = []
    if has_eye((555, 588), (296, 322), 850) and has_eye((520, 549), (300, 329), 230):
        poses.append("left")
    if has_eye((700, 729), (295, 321), 850) and has_eye((734, 770), (300, 329), 230):
        poses.append("right")
    if has_eye((589, 618), (261, 288), 850) and has_eye((646, 674), (248, 276), 850):
        poses.append("up")
    if has_eye((589, 617), (363, 386), 650) and has_eye((623, 650), (329, 351), 850):
        poses.append("down")
    idle_eyes = sorted((x, y) for x, y, area in eyes if area >= 850 and 287 <= y <= 330)
    idle = any(
        46 <= rx - lx <= 67 and abs(ry - ly) <= 15 and 578 <= lx <= 662
        for lx, ly in idle_eyes for rx, ry in idle_eyes
    )

    x1, y1, x2, y2 = TIMER
    blue, green, red = np.moveaxis(frame[y1:y2, x1:x2].astype(np.int16), -1, 0)
    pixels = int(np.count_nonzero((green > 223) & (green - blue > 34) & (green - red > 18)))
    pose = poses[0] if len(poses) == 1 else "idle" if idle and not poses else "unknown"
    return Observation(pixels, pose, eyes)


class RoundReader:
    def __init__(self):
        self.state = "Waiting for a clear timer"
        self.sequence = []
        self.clear_since = None
        self.candidate = None
        self.candidate_since = 0.0
        self.last_pose = None

    def feed(self, now, observation):
        """Return one answer per round, after the instructor returns to idle."""
        if not observation.round_active:
            if self.clear_since is None:
                self.clear_since = now
            if now - self.clear_since >= RESET_SECONDS:
                self.state = "Waiting for round"
                self.sequence.clear()
                self.candidate = self.last_pose = None
            return None

        self.clear_since = None
        if self.state == "Waiting for round":
            self.state = "Reading instructor"
        if self.state != "Reading instructor":
            return None

        pose = observation.pose
        if pose != self.candidate:
            self.candidate = pose
            self.candidate_since = now
        duration = now - self.candidate_since
        if pose == "unknown" or duration < STABLE_POSE_SECONDS:
            return None
        if pose == "idle":
            self.last_pose = None
            if self.sequence and duration >= ANSWER_IDLE_SECONDS:
                if len(self.sequence) < MIN_ARROWS:
                    self.state = "Skipped short sequence"
                    return None
                self.state = "Answered"
                return tuple(self.sequence)
        elif pose != self.last_pose:
            self.last_pose = pose
            self.sequence.append(pose)
            if len(self.sequence) > MAX_ARROWS:
                self.state = "Skipped long sequence"
        return None


def normalized_frame(frame, crop):
    if crop is not None:
        x, y, width, height = crop
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            raise ValueError("Crop requires nonnegative X/Y and positive width/height.")
        if x + width > frame.shape[1] or y + height > frame.shape[0]:
            raise ValueError(f"Crop exceeds the {frame.shape[1]}×{frame.shape[0]} source.")
        frame = frame[y:y + height, x:x + width]
    if (frame.shape[1], frame.shape[0]) == FRAME_SIZE:
        return frame
    return cv2.resize(frame, FRAME_SIZE, interpolation=cv2.INTER_LINEAR)


def debug_frame(frame, observation, reader, now, *, live_play, video, hotkey):
    view = frame.copy()
    for x1, y1, x2, y2 in (TIMER, EYES):
        cv2.rectangle(view, (x1, y1), (x2, y2), (0, 255, 255), 2)
    for x, y, area in observation.eyes:
        cv2.circle(view, (x, y), 5, (0, 255, 255), -1)
        cv2.putText(view, str(area), (x + 7, y), 0, 0.4, (0, 255, 255), 1)
    cv2.rectangle(view, (0, 0), (1288, 83), (22, 20, 18), -1)
    mode = "LIVE KEYS" if live_play else "DRY RUN"
    controls = "Space pause  /  N step  /  Q quit" if video else f"{hotkey.upper()} / Q to stop"
    lines = (
        f"{mode}  {now:.2f}s  |  {reader.state}  |  {controls}",
        f"Pose: {observation.pose}   Timer pixels: {observation.timer_pixels}   "
        f"Queue: {', '.join(reader.sequence) or '-'}",
    )
    for i, line in enumerate(lines):
        cv2.putText(view, line, (16, 30 + i * 34), 0, 0.72, (230, 239, 248), 2, cv2.LINE_AA)
    cv2.imshow(DEBUG_WINDOW, cv2.resize(view, (850, 653)))


def debug_key(delay):
    pressed = cv2.waitKey(delay) & 0xFF
    try:
        if cv2.getWindowProperty(DEBUG_WINDOW, cv2.WND_PROP_VISIBLE) < 1:
            return ord("q")
    except cv2.error:
        return ord("q")
    return pressed


def replay(sequence, stop, has_focus):
    import pydirectinput as keyboard

    for arrow in sequence:
        if stop.is_set() or not has_focus():
            return False
        try:
            if keyboard.keyDown(arrow, _pause=False) is False:
                raise RuntimeError(f"Windows rejected {arrow} down.")
            stop.wait(0.035)
        finally:
            if keyboard.keyUp.__wrapped__(arrow, _pause=False) is False:
                raise RuntimeError(f"Windows rejected {arrow} up.")
        if stop.wait(0.035):
            return False
    return True


def run(*, video=None, play=False, debug=False, crop=None, stop=None, hotkey="f8"):
    reader = RoundReader()
    own_stop = stop is None
    if own_stop:
        stop = threading.Event()
    capture = None
    remove_hotkey = None
    window_handle = None
    start = time.monotonic()
    answer_count = 0
    previous = None
    paused = False
    debug_open = False
    try:
        if video is not None:
            capture = cv2.VideoCapture(video)
            fps = capture.get(cv2.CAP_PROP_FPS)
            if not capture.isOpened() or fps <= 0:
                raise ValueError(f"Could not open video: {video}")
        else:
            from PIL import ImageGrab

            from event_script import afk_conan
            if own_stop:
                from event_script.automation_toggle import register_toggle_hotkey

                remove_hotkey = register_toggle_hotkey(hotkey, stop.set)
                print(f"Focus Tales Runner before the next round. {hotkey.upper()} stops the script.",
                      flush=True)

        while not stop.is_set():
            frame_started = time.monotonic()
            if capture is not None:
                frame_number = capture.get(cv2.CAP_PROP_POS_FRAMES)
                success, frame = capture.read()
                if not success:
                    break
                now = frame_number / fps
            else:
                window = afk_conan.get_active_window()
                if not afk_conan.is_game_window(window):
                    if window_handle is not None:
                        reader = RoundReader()
                        previous = None
                        window_handle = None
                        print("Waiting for Tales Runner to regain focus.", flush=True)
                    if debug_open and debug_key(1) in (ord("q"), 27):
                        stop.set()
                        break
                    stop.wait(0.05)
                    continue
                if window_handle != window._hWnd:
                    reader = RoundReader()
                    previous = None
                    window_handle = window._hWnd
                frame = cv2.cvtColor(
                    np.asarray(ImageGrab.grab(bbox=(window.left, window.top, window.right, window.bottom),
                                              all_screens=True)),
                    cv2.COLOR_RGB2BGR,
                )
                now = time.monotonic() - start

            frame = normalized_frame(frame, crop)
            observation = observe(frame)
            answer = reader.feed(now, observation)
            snapshot = reader.state, tuple(reader.sequence)
            if snapshot != previous:
                print(f"{now:6.2f}s  {reader.state}: {' '.join(reader.sequence)}", flush=True)
                previous = snapshot
            if debug:
                debug_frame(frame, observation, reader, now, live_play=play,
                            video=video is not None, hotkey=hotkey)
                debug_open = True
            if answer is not None:
                answer_count += 1
                if play:
                    sent = replay(answer, stop, lambda: afk_conan.get_active_window() == window)
                    print("Arrow sequence sent." if sent else "Replay interrupted.", flush=True)
                else:
                    print(f"{now:6.2f}s  Would send: {' '.join(answer)}", flush=True)
            if capture is None:
                if debug_open and debug_key(1) in (ord("q"), 27):
                    stop.set()
                    break
                stop.wait(0.01)
            elif debug:
                delay = max(1, int(1000 * (1 / fps - (time.monotonic() - frame_started))))
                while True:
                    pressed = debug_key(50 if paused else delay)
                    if pressed in (ord("q"), 27):
                        stop.set()
                        break
                    if pressed == ord(" "):
                        paused = not paused
                    if not paused or pressed == ord("n"):
                        break
    finally:
        if capture is not None:
            capture.release()
        if remove_hotkey is not None:
            remove_hotkey()
        if debug_open:
            cv2.destroyAllWindows()
    print(f"Detected {answer_count} answer sequences.", flush=True)


def enable_dpi_awareness():
    # Keep window coordinates in physical pixels on Windows display scaling.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass


def add_capture_options(parser):
    parser.add_argument("--debug", action="store_true", help="Show the detector and arrow queue.")
    parser.add_argument("--crop", type=int, nargs=4, metavar=("X", "Y", "WIDTH", "HEIGHT"),
                        help="Crop the source before detection if its framing differs.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", help="Analyze a recording without sending keys.")
    add_capture_options(parser)
    parser.add_argument("--play", action="store_true", help="Send arrows to the focused game.")
    args = parser.parse_args(argv)
    if args.video and args.play:
        parser.error("--video cannot send keys")
    if not args.video:
        enable_dpi_awareness()
    try:
        run(**vars(args))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
