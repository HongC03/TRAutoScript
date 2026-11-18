"""Run the task/farm flow with Up held and timed X holds.

Usage from the repository root:
    python -m event_script.afk_three_knight --target-image your_task.png

Images are relative to scr/, or can be absolute paths. Telegram uses the same
/conan commands as the shared engine. Statistics use three_knight_coupon_stats.json.
"""

import argparse
import math
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Also support `python event_script/afk_three_knight.py` from any working directory.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pydirectinput as key

import game_supervisor
from event_script import afk_conan
from event_script.three_knight_coupon_workflow import (
    IntervalMatchEndStage,
    ThreeKnightCouponWorkflow,
    ThreeKnightCouponWorkflowSettings,
)


# Set this to your image name if you prefer running without command-line flags.
TARGET_IMAGE_NAME = "mcr30.png"
MATCH_END_IMAGE_NAME = "conan_end.png"
STATS_PATH = afk_conan.BASE_DIR / "three_knight_coupon_stats.json"
X_HOLD_SECONDS = 2.0
X_RELEASE_GAP_SECONDS = 4.0
UP_REARM_WAIT_SECONDS = 0.2
UP_RETRIGGER_SECONDS = 15.0  # Set to 0 to disable periodic Up refreshes.
CANCELLATION_POLL_SECONDS = 0.05
TOGGLE_HOTKEY = "f3"
WORKFLOW = "farm"  # Choose "coupon" or "farm".
THREE_KNIGHT_COUPON_INTERVAL_MINUTES = 30
THREE_KNIGHT_COUPON_ON_START = False


def log_key_action(action, result):
    """Log after injection; its result does not confirm the game's response."""
    timestamp = datetime.now(timezone(timedelta(hours=8))).strftime("%H:%M:%S.%f")[:-3]
    outcome = "True" if result is True else "False" if result is False else "unknown"
    print(f"[{timestamp} HKT] [INPUT] {action}; result={outcome}", flush=True)


class UpXFocusController:
    """Manage the input worker and the separate game focus callback."""

    def __init__(self, up_retrigger_seconds=None, on_key_action=None):
        self._lock = threading.Lock()
        self._up_held = False
        self._rearm_requested = False
        self._next_up_rearm_at = None
        self.on_key_action = on_key_action
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
        """Hold Up and cycle X until the worker is paused or stopped."""
        def cancelled():
            return input_stop_event.is_set() or pause_event.is_set() or stop_event.is_set()

        x_held = False
        next_x_at = time.monotonic()

        def update_x():
            nonlocal x_held, next_x_at
            if time.monotonic() < next_x_at:
                return
            if x_held:
                result = key.keyUp("x", _pause=False)
                if result is not False:
                    x_held = False
                next_x_at = time.monotonic() + X_RELEASE_GAP_SECONDS
                self.log_input("X UP (end hold)", result)
            else:
                x_held = True
                result = key.keyDown("x", _pause=False)
                next_x_at = time.monotonic() + X_HOLD_SECONDS
                self.log_input("X DOWN (timed hold)", result)
            if result is False:
                raise RuntimeError("Windows rejected X input")

        def wait_for(seconds):
            deadline = time.monotonic() + seconds
            while not cancelled():
                update_x()
                now = time.monotonic()
                if now >= deadline:
                    return
                sleep_for = min(CANCELLATION_POLL_SECONDS, deadline - now, next_x_at - now)
                if sleep_for > 0:
                    input_stop_event.wait(sleep_for)

        try:
            if cancelled() or not self.start_up():
                return
            while not cancelled() and self.rearm_up_if_requested(wait_for, cancelled):
                wait_for(CANCELLATION_POLL_SECONDS)
        finally:
            try:
                if x_held:
                    try:
                        result = key.keyUp("x", _pause=False)
                    except key.FailSafeException:
                        # Release an owned key even when the mouse fail-safe stops the loop.
                        result = key.keyUp.__wrapped__("x", _pause=False)
                    self.log_input("X UP (cleanup)", result)
            finally:
                self.release_up()


def add_task_arguments(parser, *, target_image, stats_path, stats_flags=("--stats-path",)):
    parser.add_argument(
        "--target-image", default=target_image,
        help="Trigger image relative to scr/, or an absolute path (default: %(default)s).",
    )
    parser.add_argument(
        "--match-end-image", default=MATCH_END_IMAGE_NAME,
        help="Match-finished image used to count completed matches.",
    )
    parser.add_argument(
        "--ocr-resolution",
        choices=("auto", "1080p", "4k"),
        default="auto",
        help=(
            "OCR UI scale: auto-detect, force 1x for 1080p, or force 2x for 4K "
            "(default: %(default)s)."
        ),
    )
    parser.add_argument(
        *stats_flags, type=Path, default=stats_path,
        help="Statistics file for the run.",
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the task/farm flow with Up held and timed X holds."
    )
    add_task_arguments(
        parser, target_image=TARGET_IMAGE_NAME, stats_path=STATS_PATH,
        stats_flags=("--stats-path", "--coupon-stats-path"),
    )
    parser.add_argument(
        "--three-knight-point-image", default="three_knight_point.png",
        help=(
            "Three Knight point coupon image relative to scr/, or an absolute path "
            "(default: %(default)s)."
        ),
    )
    parser.add_argument(
        "--up-retrigger-seconds", type=float, default=UP_RETRIGGER_SECONDS,
        help="Refresh the Up hold every N seconds; 0 disables it (default: %(default)s).",
    )
    parser.add_argument(
        "--three-knight-coupon-interval-minutes",
        "--my-room-workflow-interval-minutes",
        type=float,
        default=THREE_KNIGHT_COUPON_INTERVAL_MINUTES,
        help=(
            "Run the Three Knight coupon workflow after this many minutes, "
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
        help="Global on/off hotkey (default: %(default)s).",
    )
    arguments = parser.parse_args(argv)
    if arguments.workflow == "my-room":
        arguments.workflow = "coupon"
    if not arguments.toggle_hotkey.strip():
        parser.error("--toggle-hotkey must not be empty")
    if not math.isfinite(arguments.up_retrigger_seconds) or arguments.up_retrigger_seconds < 0:
        parser.error("--up-retrigger-seconds must be a finite, non-negative number")
    if (
        not math.isfinite(arguments.three_knight_coupon_interval_minutes)
        or arguments.three_knight_coupon_interval_minutes < 0
    ):
        parser.error(
            "--three-knight-coupon-interval-minutes must be a finite, non-negative number"
        )
    if arguments.three_knight_coupon_on_start and arguments.workflow != "coupon":
        parser.error("--three-knight-coupon-on-start requires --workflow coupon")
    if not arguments.test_farm_workflow and not arguments.target_image:
        parser.error("set --target-image")
    return arguments


def main(argv=None):
    arguments = parse_args(argv)
    from event_script.automation_toggle import run_with_toggle
    return run_with_toggle(
        lambda stop_event: run_automation(arguments, stop_event=stop_event),
        toggle_hotkey=arguments.toggle_hotkey,
    )


def run_automation(arguments, *, stop_event=None):
    focus_controller = UpXFocusController(arguments.up_retrigger_seconds)
    return run_task_flow(
        arguments,
        stop_event=stop_event,
        input_worker=focus_controller.run_input,
        focus_game_window=focus_controller.focus_game_window,
        release_input=focus_controller.release_up,
        on_task_triggered=focus_controller.request_up_rearm,
    )


def run_task_flow(arguments, *, stop_event=None, workflows_enabled=True, **profile_options):
    game_supervisor.set_ocr_resolution(getattr(arguments, "ocr_resolution", "auto"))
    workflow = arguments.workflow if workflows_enabled else None
    test_farm_workflow = workflows_enabled and arguments.test_farm_workflow
    post_match_stage = None
    if (
        workflow == "coupon"
        and (
            arguments.three_knight_coupon_interval_minutes > 0
            or arguments.three_knight_coupon_on_start
        )
        and not test_farm_workflow
    ):
        post_match_stage = IntervalMatchEndStage(
            arguments.three_knight_coupon_interval_minutes * 60,
            ThreeKnightCouponWorkflow(ThreeKnightCouponWorkflowSettings(
                three_knight_point_image=arguments.three_knight_point_image,
            )),
            run_on_start=arguments.three_knight_coupon_on_start,
        )
    profile = afk_conan.TaskProfile(
        target_image_name=arguments.target_image or "unused-in-farm-only-mode.png",
        match_end_image_name=arguments.match_end_image,
        stats_path=arguments.stats_path,
        post_match_stage=post_match_stage,
        **profile_options,
    )
    control_options = {} if stop_event is None else {"stop_event": stop_event}
    return afk_conan.main(
        test_farm_workflow=test_farm_workflow,
        farm_workflow_enabled=workflow == "farm",
        profile=profile,
        **control_options,
    )


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
