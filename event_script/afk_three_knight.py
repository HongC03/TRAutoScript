"""Run the Conan-style task/farm flow with Up held and random Right presses.

Usage from the repository root:
    python -m event_script.afk_three_knight --target-image your_task.png

Images are relative to scr/, or can be absolute paths. Telegram uses the same
/conan commands as the shared engine. Statistics use three_knight_coupon_stats.json.
"""

import argparse
import math
import random
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Also support `python event_script/afk_three_knight.py` from any working directory.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pydirectinput as key

from event_script import afk_conan
from event_script.three_knight_coupon_workflow import (
    IntervalMatchEndStage,
    ThreeKnightCouponWorkflow,
)


# Set this to your image name if you prefer running without command-line flags.
TARGET_IMAGE_NAME = "magic_map.png"
MATCH_END_IMAGE_NAME = "conan_end.png"
STATS_PATH = afk_conan.BASE_DIR / "three_knight_coupon_stats.json"
RIGHT_INTERVAL_MIN_SECONDS = 0.0
RIGHT_INTERVAL_MAX_SECONDS = 2.0
RIGHT_HOLD_SECONDS = 0.2
UP_REARM_WAIT_SECONDS = 0.2
UP_RETRIGGER_SECONDS = 15.0  # Set to 0 to disable periodic Up refreshes.
CANCELLATION_POLL_SECONDS = 0.05
TOGGLE_HOTKEY = "f3"
WORKFLOW = "coupon"  # Choose "coupon" or "farm".
THREE_KNIGHT_COUPON_INTERVAL_SECONDS = 1800
THREE_KNIGHT_COUPON_ON_START = True


def log_key_action(action, result):
    """Log after injection; its result does not confirm the game's response."""
    timestamp = datetime.now(timezone(timedelta(hours=8))).strftime("%H:%M:%S.%f")[:-3]
    outcome = "True" if result is True else "False" if result is False else "unknown"
    print(f"[{timestamp} HKT] [INPUT] {action}; result={outcome}", flush=True)


class UpRightFocusController:
    """Manage worker-owned arrow input and the separate game focus callback."""

    def __init__(self, right_hold_seconds=None, up_retrigger_seconds=None, on_key_action=None):
        self._lock = threading.Lock()
        self._up_held = False
        self._rearm_requested = False
        self._next_up_rearm_at = None
        self.on_key_action = on_key_action
        self.right_hold_seconds = (
            RIGHT_HOLD_SECONDS if right_hold_seconds is None else right_hold_seconds
        )
        self.up_retrigger_seconds = (
            UP_RETRIGGER_SECONDS if up_retrigger_seconds is None else up_retrigger_seconds
        )

    def _schedule_up_rearm(self):
        self._next_up_rearm_at = (
            time.monotonic() + self.up_retrigger_seconds
            if self.up_retrigger_seconds > 0 else None
        )

    def log_input(self, action, result):
        log_key_action(action, result)
        if self.on_key_action is not None:
            self.on_key_action(action, result)

    def focus_game_window(self, window):
        if not afk_conan.is_game_window(afk_conan.get_active_window()):
            afk_conan.frontWindow(window)
        return True

    def start_up(self):
        """Start holding Up when the input worker starts, regardless of focus."""
        with self._lock:
            if not self._up_held:
                # Own cleanup even if injection raises or partially succeeds.
                self._up_held = True
                result = key.keyDown("up", _pause=False)
                self.log_input("Up DOWN (hold at worker start)", result)
                if result is False:
                    return False
                self._schedule_up_rearm()
        return True

    def release_up(self):
        with self._lock:
            self._rearm_requested = False
            self._next_up_rearm_at = None
            if self._up_held:
                result = key.keyUp("up")
                self.log_input("Up UP (release hold)", result)
                self._up_held = False

    def request_up_rearm(self):
        """Refresh our hold once after a task action can reset game input."""
        with self._lock:
            if self._up_held:
                self._rearm_requested = True

    def rearm_up_if_requested(self, wait_for, cancelled):
        """Refresh a requested or overdue hold in the input worker."""
        with self._lock:
            periodic_due = (
                self._up_held and self._next_up_rearm_at is not None
                and time.monotonic() >= self._next_up_rearm_at
            )
            if not self._rearm_requested and not periodic_due:
                return True
            reason = "after task action" if self._rearm_requested else "periodic refresh"
            self._rearm_requested = False
            self._next_up_rearm_at = None
            result = key.keyUp("up", _pause=False)
            self.log_input(f"Up UP (restart hold {reason})", result)
            self._up_held = False
            wait_for(UP_REARM_WAIT_SECONDS)
            if cancelled():
                return False
            self._up_held = True
            result = key.keyDown("up", _pause=False)
            self.log_input(f"Up DOWN (hold {reason})", result)
            if result is False:
                return False
            self._schedule_up_rearm()
            return True

    def run_input(self, input_stop_event, pause_event, stop_event):
        hold_up_and_press_right(
            input_stop_event, pause_event, stop_event, focus_controller=self,
            right_hold_seconds=self.right_hold_seconds,
        )


def hold_up_and_press_right(
    input_stop_event, pause_event, stop_event, *, focus_controller=None,
    right_hold_seconds=None,
):
    """Hold Up and wait a fresh uniform 0–2 seconds before every Right hold.

    Right's key-down/key-up gap is configurable. Interrupt both waits when any
    event is set, including Telegram pause/stop. Key
    release also runs if input or random-number generation raises an error.
    The input worker starts Up directly; focus does not gate Up refreshes.
    """
    def cancelled():
        return (
            input_stop_event.is_set()
            or pause_event.is_set()
            or stop_event.is_set()
        )

    input_ready = True

    def log_input(action, result):
        if focus_controller is None:
            log_key_action(action, result)
        else:
            focus_controller.log_input(action, result)

    def refresh_up():
        nonlocal input_ready
        if focus_controller is not None:
            input_ready = focus_controller.rearm_up_if_requested(
                lambda gap: wait_for(gap, refresh_up_hold=False), cancelled,
            )
        return input_ready

    def wait_for(seconds, *, refresh_up_hold=True):
        deadline = time.monotonic() + seconds
        while not cancelled():
            if refresh_up_hold and not refresh_up():
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            input_stop_event.wait(min(CANCELLATION_POLL_SECONDS, remaining))

    if right_hold_seconds is None:
        right_hold_seconds = RIGHT_HOLD_SECONDS
    if cancelled():
        if focus_controller is not None:
            focus_controller.release_up()
        return
    right_held = False
    try:
        if focus_controller is None:
            result = key.keyDown("up")
            log_key_action("Up DOWN (hold)", result)
        elif not focus_controller.start_up():
            return
        while not cancelled() and input_ready:
            delay = random.uniform(
                RIGHT_INTERVAL_MIN_SECONDS, RIGHT_INTERVAL_MAX_SECONDS,
            )
            wait_for(delay)
            if cancelled() or not input_ready:
                break
            right_held = True
            result = key.keyDown("right", _pause=False)
            log_input(
                f"Right DOWN (random wait: {delay:.3f}s; hold: {right_hold_seconds:.3f}s)", result,
            )
            wait_for(right_hold_seconds)
            result = key.keyUp("right", _pause=False)
            right_held = False
            log_input("Right UP (end hold)", result)
    finally:
        try:
            if right_held:
                result = key.keyUp("right", _pause=False)
                log_input("Right UP (cleanup)", result)
        finally:
            if focus_controller is None:
                result = key.keyUp("up")
                log_key_action("Up UP (release hold)", result)
            else:
                focus_controller.release_up()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the shared task/farm flow while holding Up and briefly holding Right."
    )
    parser.add_argument(
        "--target-image", default=TARGET_IMAGE_NAME,
        help="Trigger image relative to scr/, or an absolute path (required for normal runs).",
    )
    parser.add_argument(
        "--match-end-image", default=MATCH_END_IMAGE_NAME,
        help="Match-finished image used to count matches and trigger the selected workflow.",
    )
    parser.add_argument(
        "--right-hold-seconds", type=float, default=RIGHT_HOLD_SECONDS,
        help="Seconds between Right key-down and key-up (default: %(default)s).",
    )
    parser.add_argument(
        "--up-retrigger-seconds", type=float, default=UP_RETRIGGER_SECONDS,
        help="Refresh the Up hold every N seconds; 0 disables it (default: %(default)s).",
    )
    parser.add_argument(
        "--stats-path", "--coupon-stats-path", type=Path, default=STATS_PATH,
        help="Shared run, farm, and coupon workflow statistics file.",
    )
    parser.add_argument(
        "--three-knight-coupon-interval-seconds",
        "--my-room-workflow-interval-seconds",
        type=float,
        default=THREE_KNIGHT_COUPON_INTERVAL_SECONDS,
        help=(
            "Run the Three Knight coupon workflow after this many seconds, "
            "at the next match end; 0 disables repeats (default: %(default)s)."
        ),
    )
    parser.add_argument(
        "--three-knight-coupon-on-start", "--my-room-workflow-on-start",
        dest="three_knight_coupon_on_start",
        action="store_true",
        help=(
            "Allow a first or due Three Knight coupon run when the game is ready at startup."
        ),
    )
    parser.add_argument(
        "--no-three-knight-coupon-on-start", "--no-my-room-workflow-on-start",
        dest="three_knight_coupon_on_start",
        action="store_false",
        help="Do not run the coupon workflow at startup.",
    )
    parser.add_argument(
        "--workflow", choices=("coupon", "my-room", "farm"), default=WORKFLOW,
        help="Workflow to run after its interval elapses: coupon or farm (default: %(default)s).",
    )
    parser.set_defaults(three_knight_coupon_on_start=THREE_KNIGHT_COUPON_ON_START)
    parser.add_argument("--test-farm-workflow", action="store_true")
    parser.add_argument(
        "--toggle-hotkey", default=TOGGLE_HOTKEY,
        help="Global on/off hotkey for the toggle window (default: %(default)s).",
    )
    parser.add_argument(
        "--status-window", action="store_true",
        help="Show the desktop status and Turn On/Turn Off window.",
    )
    parser.add_argument(
        "--no-toggle-window", action="store_true",
        help=argparse.SUPPRESS,
    )
    arguments = parser.parse_args(argv)
    if arguments.workflow == "my-room":
        arguments.workflow = "coupon"
    if not arguments.toggle_hotkey.strip():
        parser.error("--toggle-hotkey must not be empty")
    if not math.isfinite(arguments.right_hold_seconds) or arguments.right_hold_seconds < 0:
        parser.error("--right-hold-seconds must be a finite, non-negative number")
    if not math.isfinite(arguments.up_retrigger_seconds) or arguments.up_retrigger_seconds < 0:
        parser.error("--up-retrigger-seconds must be a finite, non-negative number")
    if (
        not math.isfinite(arguments.three_knight_coupon_interval_seconds)
        or arguments.three_knight_coupon_interval_seconds < 0
    ):
        parser.error(
            "--three-knight-coupon-interval-seconds must be a finite, non-negative number"
        )
    if arguments.three_knight_coupon_on_start and arguments.workflow != "coupon":
        parser.error("--three-knight-coupon-on-start requires --workflow coupon")
    if not arguments.test_farm_workflow and not arguments.target_image:
        parser.error("set --target-image or TARGET_IMAGE_NAME in afk_three_knight.py")
    return arguments


def main(argv=None):
    arguments = parse_args(argv)
    if arguments.status_window and not arguments.no_toggle_window and not arguments.test_farm_workflow:
        from event_script.automation_toggle import LiveAutomationInfo, run_toggle_window
        live_info = LiveAutomationInfo({
            "Trigger image": arguments.target_image,
            "Match-end image": arguments.match_end_image,
            "Selected workflow": arguments.workflow,
            "Workflow interval": (
                (
                    "Disabled"
                    if arguments.three_knight_coupon_interval_seconds == 0
                    else f"Every {arguments.three_knight_coupon_interval_seconds:g}s, after match end"
                )
                if arguments.workflow == "coupon"
                else f"Every {afk_conan.SCHEDULED_IMAGE_INTERVAL_SECONDS:g}s, after match end"
            ),
            "Run coupon workflow at startup": (
                "Yes" if arguments.three_knight_coupon_on_start else "No"
            ),
            "Right timing": f"Hold {arguments.right_hold_seconds:g}s / random wait {RIGHT_INTERVAL_MIN_SECONDS:g}–{RIGHT_INTERVAL_MAX_SECONDS:g}s",
            "Up refresh": "Disabled" if arguments.up_retrigger_seconds == 0 else f"Every {arguments.up_retrigger_seconds:g}s / release gap {UP_REARM_WAIT_SECONDS:g}s",
        }, arguments.up_retrigger_seconds)
        return run_toggle_window(
            lambda stop_event: run_automation(arguments, stop_event=stop_event, live_info=live_info),
            toggle_hotkey=arguments.toggle_hotkey,
            live_info=live_info,
        )
    return run_automation(arguments)


def run_automation(arguments, *, stop_event=None, live_info=None):
    if live_info is not None:
        live_info.reset()
    focus_controller = UpRightFocusController(
        arguments.right_hold_seconds, arguments.up_retrigger_seconds,
        on_key_action=None if live_info is None else live_info.record_input,
    )
    post_match_stage = None
    if (
        arguments.workflow == "coupon"
        and (
            arguments.three_knight_coupon_interval_seconds > 0
            or arguments.three_knight_coupon_on_start
        )
        and not arguments.test_farm_workflow
    ):
        post_match_stage = IntervalMatchEndStage(
            arguments.three_knight_coupon_interval_seconds,
            ThreeKnightCouponWorkflow(),
            run_on_start=arguments.three_knight_coupon_on_start,
        )
    profile = afk_conan.TaskProfile(
        target_image_name=arguments.target_image or "unused-in-farm-only-mode.png",
        match_end_image_name=arguments.match_end_image,
        stats_path=arguments.stats_path,
        input_worker=focus_controller.run_input,
        focus_game_window=focus_controller.focus_game_window,
        release_input=focus_controller.release_up,
        on_task_triggered=focus_controller.request_up_rearm,
        on_status_ready=None if live_info is None else live_info.bind_status,
        post_match_stage=post_match_stage,
    )
    control_options = {} if stop_event is None else {"stop_event": stop_event}
    try:
        return afk_conan.main(
            test_farm_workflow=arguments.test_farm_workflow,
            farm_workflow_enabled=arguments.workflow == "farm",
            profile=profile,
            **control_options,
        )
    finally:
        if live_info is not None:
            live_info.finish()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except afk_conan.gui.ImageNotFoundException:
        pass
    except (FileNotFoundError, ValueError) as error:
        print(f"** {error} **", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n* 任務已停止 *")
