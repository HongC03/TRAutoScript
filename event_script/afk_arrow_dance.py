"""Tap the matching arrow as each Tales Runner instructor pose is recognized."""

import argparse
import ctypes
import random
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np


# Coordinates measured on the supplied 1288 × 990 recording.
FRAME_SIZE = (1288, 990)
EYES = (500, 228, 780, 390)
SUIT = (490, 210, 790, 411)
TIMER = (319, 163, 463, 189)
STABLE_POSE_SECONDS = 0.065
DEMONSTRATION_IDLE_SECONDS = 0.18
RESET_SECONDS = 0.20
NO_POSE_GUESS_SECONDS = 0.85
ARROW_KEYS = ("up", "down", "left", "right")
MAX_ARROWS = 12
TAP_SECONDS = 0.035
DEBUG_WINDOW = "Arrow Dance Debug"

# Color coverage for each pose, measured on a 3-row by 5-column grid.
# Values are counts in hundreds of pixels at the reference frame size.
SUIT_POSES = {
    "blue": (
        ((80, 55, 140), (100, 255, 255)),
        {
            "up": (0, 1, 10, 2, 0, 0, 21, 11, 19, 0, 0, 6, 17, 0, 0),
            "down": (0, 5, 8, 0, 0, 0, 7, 9, 6, 0, 0, 12, 17, 3, 0),
            "left": (0, 6, 13, 0, 0, 0, 12, 12, 0, 0, 0, 14, 15, 0, 0),
            "right": (0, 0, 11, 7, 0, 0, 0, 11, 13, 0, 0, 0, 13, 17, 0),
        },
    ),
    "pink": (
        ((135, 38, 150), (172, 255, 255)),
        {
            "up": (0, 2, 9, 0, 0, 0, 17, 5, 12, 0, 0, 9, 15, 0, 0),
            "down": (0, 6, 9, 0, 0, 0, 2, 4, 2, 0, 0, 5, 14, 6, 0),
            "left": (0, 4, 13, 0, 0, 1, 3, 14, 0, 0, 0, 13, 15, 0, 0),
            "right": (0, 0, 16, 1, 0, 0, 3, 11, 4, 0, 0, 0, 26, 2, 0),
        },
    ),
}
SUIT_PROFILES = {
    color: (bounds, tuple(poses), np.array(tuple(poses.values())))
    for color, (bounds, poses) in SUIT_POSES.items()
}
MAX_SUIT_POSE_DISTANCE = 13
SUIT_SHIFT_X = 16
SUIT_SHIFT_Y = 12
SUIT_OFFSETS = np.array([
    (dx, dy)
    for dy in range(-SUIT_SHIFT_Y, SUIT_SHIFT_Y + 1, 4)
    for dx in range(-SUIT_SHIFT_X, SUIT_SHIFT_X + 1, 4)
])
SUIT_OFFSET_COST = np.abs(SUIT_OFFSETS).sum(axis=1) / 12


@dataclass(frozen=True)
class Observation:
    timer_pixels: int
    pose: str
    eyes: tuple
    instructor: str = "unknown"
    blue_pose_score: int | None = None

    @property
    def round_active(self):
        return self.timer_pixels > 400


def shifted_suit_coverage(frame, bounds):
    """Measure the suit at nearby grid positions in one mask."""
    x1, y1, x2, y2 = SUIT
    hsv = cv2.cvtColor(
        frame[y1 - SUIT_SHIFT_Y:y2 + SUIT_SHIFT_Y, x1 - SUIT_SHIFT_X:x2 + SUIT_SHIFT_X],
        cv2.COLOR_BGR2HSV,
    )
    mask = cv2.inRange(hsv, *bounds)
    integral = cv2.integral(mask, sdepth=cv2.CV_32S)
    left = SUIT_SHIFT_X + SUIT_OFFSETS[:, 0, None, None] + 60 * np.arange(5)[None, None, :]
    top = SUIT_SHIFT_Y + SUIT_OFFSETS[:, 1, None, None] + 67 * np.arange(3)[None, :, None]
    right = left + 60
    bottom = top + 67
    pixels = integral[bottom, right] - integral[top, right] - integral[bottom, left] + integral[top, left]
    return pixels.reshape(-1, 15) // 25500


def green_timer_mask(frame):
    blue, green, red = np.moveaxis(frame.astype(np.int16), -1, 0)
    return (green > 223) & (green - blue > 34) & (green - red > 18)


def blue_timer_offset(frame):
    """Locate the stable left edge of the timer to align the blue pose grid."""
    x, y = 292, 139
    mask = green_timer_mask(frame[y:210, x:422])
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8))
    left, _, width, _, area = max(stats[1:], key=lambda row: row[4], default=(0, 0, 0, 0, 0))
    if width < 34 or area < 350:
        return 0, 0
    rows, _ = np.nonzero(mask[:, left:left + 34])
    dx = x + int(left) - 319
    dy = y + round(float(np.median(rows))) - 175
    return (dx, dy) if abs(dx) <= 24 and abs(dy) <= 20 else (0, 0)


def suit_grid(mask):
    return np.count_nonzero(mask.reshape(3, 67, 5, 60), axis=(1, 3)).ravel() // 100


def observe(frame):
    """Read the timer and identify the instructor's current pose."""
    x1, y1, x2, y2 = TIMER
    pixels = int(np.count_nonzero(green_timer_mask(frame[y1:y2, x1:x2])))

    x1, y1, x2, y2 = SUIT
    hsv = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    for color, ((lower, upper), directions, templates) in SUIT_PROFILES.items():
        mask = cv2.inRange(hsv, lower, upper)
        coverage = suit_grid(mask)
        if coverage.sum() < 42:
            continue
        if color == "blue":
            dx, dy = blue_timer_offset(frame)
            if dx or dy:
                region = frame[y1 + dy:y2 + dy, x1 + dx:x2 + dx]
                coverage = suit_grid(cv2.inRange(cv2.cvtColor(region, cv2.COLOR_BGR2HSV), lower, upper))
        distances = np.abs(templates - coverage).sum(axis=1)
        best = int(np.argmin(distances))
        if distances[best] <= MAX_SUIT_POSE_DISTANCE:
            score = int(distances[best]) if color == "blue" else None
            return Observation(pixels, directions[best], (), color, score)
        if color == "pink":
            shifted = shifted_suit_coverage(frame, (lower, upper))
            scores = np.abs(shifted[:, None, :] - templates[None, :, :]).sum(axis=2)
            scores = scores + SUIT_OFFSET_COST[:, None]
            shift, best = np.unravel_index(scores.argmin(), scores.shape)
            if scores[shift, best] <= MAX_SUIT_POSE_DISTANCE:
                return Observation(pixels, directions[best], (), color)
        # Spark effects can briefly hide the cap during an otherwise clear pose.
        # For idle, require the cap as well as the suit to establish identity.
        if coverage[:5].sum() >= 8:
            return Observation(pixels, "idle", (), color)

    pose, eyes = orange_pose(frame)
    return Observation(pixels, pose, eyes, "orange" if pose != "unknown" else "unknown")


def orange_pose(frame):
    """Classify the orange instructor by the positions of its dark lenses."""
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

    pose = poses[0] if len(poses) == 1 else "idle" if idle and not poses else "unknown"
    return pose, eyes


@dataclass(frozen=True)
class ArrowTap:
    direction: str
    guessed: bool = False


class RoundReader:
    def __init__(self):
        self.state = "Waiting for a clear timer"
        self.sequence = []
        self.clear_since = None
        self.candidate = None
        self.candidate_since = 0.0
        self.last_pose = None
        self.previous_frame_at = None
        self.round_started_at = 0.0
        self.guess = None

    def feed(self, now, observation):
        """Emit a recognized pose or one fallback tap for an empty round."""
        frame_gap = 0.0 if self.previous_frame_at is None else now - self.previous_frame_at
        self.previous_frame_at = now
        if not observation.round_active:
            if self.clear_since is None:
                self.clear_since = now
            if now - self.clear_since >= RESET_SECONDS:
                self.state = "Waiting for round"
                self.sequence.clear()
                self.candidate = self.last_pose = None
                self.guess = None
            return None

        self.clear_since = None
        if self.state == "Waiting for round":
            self.state = "Reading instructor"
            self.round_started_at = now
        if self.state != "Reading instructor":
            return None

        pose = observation.pose
        if pose != self.candidate:
            self.candidate = pose
            self.candidate_since = now
        if (
            pose in ("idle", "unknown")
            and not self.sequence
            and self.guess is None
            and now - self.round_started_at >= NO_POSE_GUESS_SECONDS
        ):
            self.guess = random.choice(ARROW_KEYS)
            return ArrowTap(self.guess, guessed=True)
        duration = now - self.candidate_since
        # A clear blue pose can fit in a single frame when capture runs slowly.
        strong_blue_sample = (
            0.085 <= frame_gap <= 0.25
            and observation.blue_pose_score is not None
            and observation.blue_pose_score <= 7
        )
        if pose == "unknown" or (duration < STABLE_POSE_SECONDS and not strong_blue_sample):
            return None
        if pose == "idle":
            self.last_pose = None
            if self.sequence and duration >= DEMONSTRATION_IDLE_SECONDS:
                self.state = "Demonstration ended"
        elif pose != self.last_pose:
            if len(self.sequence) >= MAX_ARROWS:
                self.state = "Reached pose limit"
                return None
            self.last_pose = pose
            self.sequence.append(pose)
            return ArrowTap(pose)
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
    pose_region = SUIT if observation.instructor in SUIT_PROFILES else EYES
    for x1, y1, x2, y2 in (TIMER, pose_region):
        cv2.rectangle(view, (x1, y1), (x2, y2), (0, 255, 255), 2)
    for x, y, area in observation.eyes:
        cv2.circle(view, (x, y), 5, (0, 255, 255), -1)
        cv2.putText(view, str(area), (x + 7, y), 0, 0.4, (0, 255, 255), 1)
    cv2.rectangle(view, (0, 0), (1288, 83), (22, 20, 18), -1)
    mode = "LIVE KEYS" if live_play else "DRY RUN"
    controls = "Space pause  /  N step  /  Q quit" if video else f"{hotkey.upper()} / Q to stop"
    lines = (
        f"{mode}  {now:.2f}s  |  {reader.state}  |  {controls}",
        f"{observation.instructor.capitalize()} / {observation.pose}   "
        f"Timer pixels: {observation.timer_pixels}   "
        f"Recognized: {', '.join(reader.sequence) or '-'}"
        + (f"   Guess: {reader.guess}" if reader.guess else ""),
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


def tap_arrow(arrow, stop, has_focus):
    import pydirectinput as keyboard

    if stop.is_set() or not has_focus():
        return False
    try:
        if keyboard.keyDown(arrow, _pause=False) is False:
            raise RuntimeError(f"Windows rejected {arrow} down.")
        stop.wait(TAP_SECONDS)
    finally:
        if keyboard.keyUp.__wrapped__(arrow, _pause=False) is False:
            raise RuntimeError(f"Windows rejected {arrow} up.")
    return True


def run(*, video=None, play=False, debug=False, crop=None, stop=None, hotkey="f8"):
    if video is not None and play:
        raise ValueError("Video analysis cannot send keys.")
    reader = RoundReader()
    own_stop = stop is None
    if own_stop:
        stop = threading.Event()
    capture = None
    remove_hotkey = None
    window_handle = None
    start = time.monotonic()
    pose_count = 0
    guess_count = 0
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
            tap = reader.feed(now, observation)
            if tap is not None:
                guess_count += int(tap.guessed)
                pose_count += int(not tap.guessed)
                if play:
                    sent = tap_arrow(tap.direction, stop, lambda: afk_conan.get_active_window() == window)
                    action = "Sent" if sent else "Skipped tap"
                else:
                    action = "Would send"
                source = " (guess)" if tap.guessed else ""
                print(f"{now:6.2f}s  {action}: {tap.direction}{source}", flush=True)
            snapshot = reader.state, tuple(reader.sequence)
            if snapshot != previous:
                print(f"{now:6.2f}s  {reader.state}: {' '.join(reader.sequence)}", flush=True)
                previous = snapshot
            if debug:
                debug_frame(frame, observation, reader, now, live_play=play,
                            video=video is not None, hotkey=hotkey)
                debug_open = True
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
    print(f"Recognized {pose_count} arrow poses and guessed {guess_count} times.", flush=True)


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
