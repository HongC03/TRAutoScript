"""Run the Conan AFK task while optionally reporting status through Telegram.

Configuration:
    Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the environment to enable
    Telegram commands. A Conan task starts when TARGET_IMAGE_NAME is detected
    and its configured action is triggered. A match is counted when
    MATCH_END_IMAGE_NAME appears.

The script can be run without Telegram configuration; the game automation will
continue and Telegram command polling will remain disabled.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import Callable
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pyautogui as gui
import pydirectinput as key
import pygetwindow as gw

from event_script.farm_automation import (
    FarmAutomation,
    FarmAutomationSettings,
    get_tales_runner_window,
)
from game_supervisor import (
    BASE_DIR,
    GameSupervisor,
    frontWindow,
    gameWindows,
)
from utils import (
    autoPressButton,
    capture_screen,
    locate_on_screen,
    triggerIfDetected,
    use_screen_capture,
)
from screen_capture import GameScreenCapture, ScreenCaptureUnavailable


# Paths and output.
ALARM_WINDOW_SCRIPT_PATH = BASE_DIR / "event_script" / "alarm_window.py"  # Alarm CLI.
CONAN_STATS_PATH = BASE_DIR / "conan_stats.json"  # Persistent Conan statistics file.
DEFAULT_TALESRUNNER_LAUNCHER_AUTO_DIR = (
    BASE_DIR.parent / "ai-agent-workspace" / "TalesRunner-launcher-auto"
)

# Conan detection and actions.
TARGET_IMAGE_NAME = "conan.png"  # Template for starting a Conan task.
ACTION = "click"  # Action for TARGET_IMAGE_NAME: "click" or "key".
KEYBOARD_KEY = None  # Key to press when ACTION is "key"; otherwise unused.
MATCH_CONFIDENCE = 0.89  # Minimum image-match confidence for all screen templates.
MATCH_END_IMAGE_NAME = "conan_end.png"  # Template marking a finished Conan task.

# Main-loop timing and window behavior.
def environment_positive_float(name, default):
    """Read a positive float setting supplied by a run configuration."""
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be a positive number") from error
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def environment_boolean(name, default):
    """Read a boolean setting supplied by a run configuration."""
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    value = raw_value.strip().casefold()
    if value in {"1", "true", "True", "yes", "on"}:
        return True
    if value in {"0", "false", "False", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true, false, yes, no, on, off, 1, or 0")


MATCH_END_WAIT_SECONDS = 20  # Grace period after Conan ends before farm starts.
LOOP_INTERVAL = environment_positive_float("CONAN_LOOP_INTERVAL", 3)
SWITCH_TO_PREVIOUS_WINDOW_ENABLED = environment_boolean(
    "CONAN_SWITCH_TO_PREVIOUS_WINDOW_ENABLED", False
)
SWITCH_TO_PREVIOUS_WINDOW_METHOD = "alt_tab"  # "alt_tab" Or "previous_window".
SHIFT_RANDOM_INTERVAL = 3  # Optional +/- jitter in automatic Conan Shift presses.

# Feature flags and diagnostics.
FARM_WORKFLOW_ENABLED = True  # Enable scheduled farm runs during Conan automation.

# Farm scheduling is owned by Conan; live farm settings are reusable.
SCHEDULED_IMAGE_INTERVAL_SECONDS = 1800  # Seconds between completed farm schedules.
FARM_SETTINGS = FarmAutomationSettings()


@dataclass(frozen=True)
class TaskProfile:
    """Customize task input and optional post-match stages."""

    target_image_name: str
    match_end_image_name: str = MATCH_END_IMAGE_NAME
    stats_path: Path = CONAN_STATS_PATH
    input_worker: Callable | None = None
    focus_game_window: Callable | None = None
    release_input: Callable | None = None
    on_task_triggered: Callable | None = None
    on_status_ready: Callable | None = None
    post_match_stage: object | None = None

# Telegram and alarm settings.
TELEGRAM_POLL_TIMEOUT = 5  # Telegram long-poll timeout in seconds.
TELEGRAM_RETRY_DELAY = 5  # Initial delay before retrying Telegram polling.
TELEGRAM_MAX_RETRY_DELAY = 60  # Cap repeated connection-failure backoff.
INACTIVITY_WARNING_SECONDS = 15 * 60  # Warn when completed activities stop.
ACTIVITY_MONITOR_INTERVAL_SECONDS = 1  # Independent of the game/image loop.
ALARM_WINDOW_DEFAULT_INTERVAL_SECONDS = 60  # Default recurring desktop alarm period.
ALARM_WINDOW_DEFAULT_TITLE = ""  # Default title for Telegram-started alarm windows.
ALARM_WINDOW_DEFAULT_MESSAGE = "The alarm interval has elapsed."  # Alarm text.


gui.useImageNotFoundException(False)


def get_active_window():
    """Return the currently focused window, if the desktop exposes one."""
    try:
        return gw.getActiveWindow()
    except (OSError, TypeError, AttributeError):
        return None


def is_game_window(window):
    """Return whether a window is one of the current Tales Runner windows."""
    if window is None:
        return False
    try:
        for game_window in gameWindows():
            if window is game_window:
                return True
            window_handle = getattr(window, "_hWnd", None)
            game_handle = getattr(game_window, "_hWnd", None)
            if (
                window_handle is not None
                and game_handle is not None
                and window_handle == game_handle
            ):
                return True
    except (AttributeError, OSError, RuntimeError, TypeError):
        return False
    return False


def switch_to_previous_window(previous_window=None):
    """Focus the configured post-loop window-switch target."""
    if not SWITCH_TO_PREVIOUS_WINDOW_ENABLED:
        return False

    if SWITCH_TO_PREVIOUS_WINDOW_METHOD == "previous_window":
        if previous_window is None or is_game_window(previous_window):
            return False
        frontWindow(previous_window)
        return True
    if SWITCH_TO_PREVIOUS_WINDOW_METHOD != "alt_tab":
        raise ValueError(
            "SWITCH_TO_PREVIOUS_WINDOW_METHOD must be 'alt_tab' "
            "or 'previous_window'"
        )

    key.press("shift")
    key.keyDown("alt")
    try:
        key.keyDown("tab")
        key.keyUp("tab")
        time.sleep(0.25)
    finally:
        key.keyUp("alt")
    return True


def automatic_shift_press_enabled():
    """Return whether the repeating Shift behavior should run."""
    return not (
        SWITCH_TO_PREVIOUS_WINDOW_ENABLED
        and SWITCH_TO_PREVIOUS_WINDOW_METHOD == "alt_tab"
    )


def load_dotenv_file():
    """Load simple KEY=VALUE settings from the repository-root .env file."""
    dotenv_path = BASE_DIR / ".env"
    if not dotenv_path.is_file():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and not os.environ.get(key):
            os.environ[key] = value


load_dotenv_file()


class ConanCommand(str, Enum):
    HELP = "/conanHelp"
    STATS = "/conanStats"
    SCREENSHOT = "/conanScreenshot"
    PAUSE = "/conanPause"
    RESUME = "/conanResume"
    STOP = "/conanStop"
    ALARM_WINDOW = "/alarmWindow"
    TR_LAUNCH = "/trLaunch"


def powershell_quote(value):
    """Quote one literal value for a PowerShell command string."""
    return "'" + str(value).replace("'", "''") + "'"


class TalesRunnerLauncherController:
    """Start the standalone launcher automation in a retained terminal."""

    def __init__(self, launcher_dir=None, powershell_path=None):
        configured_dir = os.environ.get("TALESRUNNER_LAUNCHER_AUTO_DIR")
        self.launcher_dir = Path(
            launcher_dir or configured_dir or DEFAULT_TALESRUNNER_LAUNCHER_AUTO_DIR
        ).expanduser()
        self.python_path = self.launcher_dir / ".venv" / "Scripts" / "python.exe"
        self.script_path = self.launcher_dir / "talesrunner_launcher_auto.py"
        self.powershell_path = Path(
            powershell_path
            or (
                Path(os.environ.get("SystemRoot", r"C:\Windows"))
                / "System32"
                / "WindowsPowerShell"
                / "v1.0"
                / "powershell.exe"
            )
        )

    def start(self, profile_number):
        """Start profile 1 or 2 without blocking Telegram command polling."""
        if profile_number not in {"1", "2"}:
            raise ValueError("profile must be 1 or 2")
        if not self.python_path.is_file():
            raise FileNotFoundError(
                f"Launcher Python environment not found: {self.python_path}"
            )
        if not self.script_path.is_file():
            raise FileNotFoundError(
                f"Launcher automation not found: {self.script_path}"
            )

        launcher_command = " ".join(
            (
                f"& {powershell_quote(self.python_path)}",
                powershell_quote(self.script_path),
                "--profile",
                powershell_quote(profile_number),
            )
        )
        command = [
            str(self.powershell_path),
            "-NoLogo",
            "-NoExit",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            launcher_command,
        ]
        subprocess.Popen(
            command,
            cwd=str(self.launcher_dir),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        return True


class AlarmWindowController:
    """Manage the integrated Tk alarm in a separate process."""

    def __init__(self, script_path=None):
        self.script_path = script_path or ALARM_WINDOW_SCRIPT_PATH
        self._lock = threading.Lock()
        self._process = None

    def _running_process_locked(self):
        if self._process is not None and self._process.poll() is None:
            return self._process
        self._process = None
        return None

    def start(
        self,
        interval_seconds=ALARM_WINDOW_DEFAULT_INTERVAL_SECONDS,
        start_time=None,
        title=ALARM_WINDOW_DEFAULT_TITLE,
        message=ALARM_WINDOW_DEFAULT_MESSAGE,
    ):
        """Start one recurring alarm process unless one is already running."""
        try:
            interval_seconds = float(interval_seconds)
        except (TypeError, ValueError) as error:
            raise ValueError("interval must be a positive number") from error
        if not interval_seconds > 0:
            raise ValueError("interval must be a positive number")
        if not self.script_path.is_file():
            raise FileNotFoundError(f"Alarm window script not found: {self.script_path}")

        command = [
            sys.executable,
            str(self.script_path),
            "--interval",
            str(interval_seconds),
            "--desktop-notification",
            "--telegram-notification",
            "--title",
            str(title),
            "--message",
            str(message),
        ]
        if start_time:
            command.extend(("--start-time", str(start_time)))

        with self._lock:
            if self._running_process_locked() is not None:
                return False
            self._process = subprocess.Popen(command, cwd=str(BASE_DIR))
        return True

    def stop(self):
        """Stop the current alarm process, if one is running."""
        with self._lock:
            process = self._running_process_locked()
            self._process = None
        if process is None:
            return False

        try:
            process.terminate()
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        except OSError:
            return False
        return True

    def is_running(self):
        with self._lock:
            return self._running_process_locked() is not None


class ConanStage(str, Enum):
    STARTING = "starting"
    CHECKING_GAME_STATE = "checking game state"
    CHECKING_SCHEDULED_IMAGE = "checking scheduled farm image"
    WAITING_FOR_MATCH = "waiting for Conan match"
    FARM_WORKFLOW_PENDING = "farm workflow pending until Conan match finishes"
    FARM_WORKFLOW = "running scheduled farm workflow"
    POST_MATCH_WORKFLOW_PENDING = "post-match workflow pending until match finishes"
    POST_MATCH_WORKFLOW = "running post-match workflow"
    MATCH_FINISHED = "match finished"
    WAITING_AFTER_MATCH_FINISHED = "waiting after match finished"
    PAUSED_BY_COMMAND = "paused by Telegram command"
    GAME_PAUSED = "game paused"
    SCREEN_UNAVAILABLE = "waiting for screen capture"


class PersistentConanStats:
    """Store all-time Conan counters in a small JSON file."""

    DEFAULT_DATA = {
        "total_runs": 0,
        "total_matches_finished": 0,
        "total_restarts": 0,
        "total_runtime_seconds": 0.0,
        "last_started_at": None,
        "last_match_at": None,
        "telegram_update_offset": None,
        "next_farm_at": None,
        "next_coupon_workflow_at": None,
        "last_coupon_workflow_completed_at": None,
    }

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("persistent stats must be a JSON object")
        except (OSError, ValueError, TypeError):
            return dict(self.DEFAULT_DATA)

        result = dict(self.DEFAULT_DATA)
        result.update(data)
        for key in (
            "total_runs",
            "total_matches_finished",
            "total_restarts",
        ):
            try:
                result[key] = max(0, int(result[key]))
            except (TypeError, ValueError):
                result[key] = self.DEFAULT_DATA[key]
        try:
            result["total_runtime_seconds"] = max(
                0.0, float(result["total_runtime_seconds"])
            )
        except (TypeError, ValueError):
            result["total_runtime_seconds"] = 0.0
        try:
            if result["next_farm_at"] is not None:
                result["next_farm_at"] = float(result["next_farm_at"])
        except (TypeError, ValueError):
            result["next_farm_at"] = None
        # Migrate the old Up/Right schedule key and the interim standalone
        # coupon timestamp into this single stats file.
        if result["next_coupon_workflow_at"] is None:
            result["next_coupon_workflow_at"] = data.get("next_my_room_workflow_at")
        if result["last_coupon_workflow_completed_at"] is None:
            result["last_coupon_workflow_completed_at"] = data.get("last_completed_at")
        result.pop("next_my_room_workflow_at", None)
        result.pop("last_completed_at", None)
        for key_name in (
            "next_coupon_workflow_at", "last_coupon_workflow_completed_at",
        ):
            try:
                value = result[key_name]
                if value is not None:
                    if isinstance(value, str):
                        value = datetime.fromisoformat(value).timestamp()
                    result[key_name] = float(value)
            except (TypeError, ValueError, OverflowError):
                result[key_name] = None
        return result

    def _save_locked(self):
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        data_to_save = dict(self._data)
        hong_kong_timezone = timezone(timedelta(hours=8))
        for key_name in (
            "next_coupon_workflow_at",
            "last_coupon_workflow_completed_at",
        ):
            timestamp = data_to_save[key_name]
            if timestamp is not None:
                data_to_save[key_name] = datetime.fromtimestamp(
                    float(timestamp), hong_kong_timezone
                ).isoformat(timespec="milliseconds")
        try:
            temporary_path.write_text(
                json.dumps(data_to_save, indent=2),
                encoding="utf-8",
            )
            temporary_path.replace(self.path)
        except OSError as error:
            print(f"Persistent Conan stats could not be saved: {error}", file=sys.stderr)

    def record_run_started(self):
        with self._lock:
            self._data["total_runs"] += 1
            self._data["last_started_at"] = datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            self._save_locked()

    def record_match_finished(self, timestamp):
        with self._lock:
            self._data["total_matches_finished"] += 1
            self._data["last_match_at"] = timestamp
            self._save_locked()

    def record_restart(self):
        with self._lock:
            self._data["total_restarts"] += 1
            self._save_locked()

    def record_runtime(self, seconds):
        with self._lock:
            self._data["total_runtime_seconds"] += max(0.0, seconds)
            self._save_locked()

    def record_telegram_update(self, offset):
        with self._lock:
            previous_offset = self._data.get("telegram_update_offset")
            if previous_offset is None or offset > previous_offset:
                self._data["telegram_update_offset"] = offset
                self._save_locked()

    def telegram_update_offset(self):
        with self._lock:
            return self._data.get("telegram_update_offset")

    def record_next_farm_at(self, timestamp):
        with self._lock:
            self._data["next_farm_at"] = (
                None if timestamp is None else float(timestamp)
            )
            self._save_locked()

    def next_farm_at(self):
        with self._lock:
            return self._data.get("next_farm_at")

    def record_next_coupon_workflow_at(self, timestamp):
        with self._lock:
            self._data["next_coupon_workflow_at"] = (
                None if timestamp is None else float(timestamp)
            )
            self._save_locked()

    def next_coupon_workflow_at(self):
        with self._lock:
            return self._data.get("next_coupon_workflow_at")

    def record_last_coupon_workflow_completed_at(self, timestamp):
        with self._lock:
            self._data["last_coupon_workflow_completed_at"] = (
                None if timestamp is None else float(timestamp)
            )
            self._save_locked()

    def last_coupon_workflow_completed_at(self):
        with self._lock:
            return self._data.get("last_coupon_workflow_completed_at")

    def snapshot(self):
        with self._lock:
            return dict(self._data)


class ConanStatus:
    """Thread-safe progress state shared by the game loop and Telegram worker."""

    def __init__(self, persistent_stats=None, telegram_notifications_enabled=False):
        self._lock = threading.Lock()
        self.persistent_stats = persistent_stats
        self.matches_finished = 0
        self.current_stage = ConanStage.STARTING
        self.game_state = None
        self.started_at = time.monotonic()
        self.last_match_at = None
        self.restarts = 0
        self.next_farm_at = None
        self.farm_workflow_pending = False
        self.telegram_notifications_enabled = telegram_notifications_enabled
        self._notifications = deque()
        self._last_progress_description = "script startup"
        self._watchdog_reference_at = self.started_at
        self._watchdog_paused = False
        self._stuck_alerted = False
        self._farm_started_at = None

    def _queue_activity_locked(self, message, progress=False):
        """Record progress and enqueue logs without making network calls."""
        timestamp = datetime.now(timezone(timedelta(hours=8))).strftime(
            "%Y-%m-%d %H:%M:%S HKT"
        )
        if self.telegram_notifications_enabled:
            self._notifications.append(f"[{timestamp}] {message}")
        if progress:
            now = time.monotonic()
            self._watchdog_reference_at = now
            self._last_progress_description = f"{message} at {timestamp}"
            if self._stuck_alerted and self.telegram_notifications_enabled:
                self._notifications.append(
                    f"[{timestamp}] Conan activity resumed after the inactivity warning."
                )
            self._stuck_alerted = False

    def record_farm_started(self):
        with self._lock:
            self._farm_started_at = time.monotonic()
            self._queue_activity_locked("Farm workflow started.")

    def record_farm_finished(self, outcome):
        with self._lock:
            duration = (
                0 if self._farm_started_at is None
                else max(0, time.monotonic() - self._farm_started_at)
            )
            self._farm_started_at = None
            self._queue_activity_locked(
                f"Farm workflow {outcome} (duration: {format_duration(duration)}).",
                progress=outcome == "completed",
            )

    def _set_watchdog_paused_locked(self, paused, now):
        if self._watchdog_paused and not paused:
            # A deliberate pause gets a fresh 15-minute window on resume.
            self._watchdog_reference_at = now
            self._stuck_alerted = False
        self._watchdog_paused = paused

    def set_watchdog_paused(self, paused):
        with self._lock:
            self._set_watchdog_paused_locked(paused, time.monotonic())

    def check_inactivity(self, paused=False):
        """Queue one warning per idle period, even if the game loop is blocked."""
        with self._lock:
            now = time.monotonic()
            self._set_watchdog_paused_locked(paused, now)
            if paused or not self.telegram_notifications_enabled or self._stuck_alerted:
                return
            idle_seconds = now - self._watchdog_reference_at
            if idle_seconds < INACTIVITY_WARNING_SECONDS:
                return
            self._stuck_alerted = True
            self._queue_activity_locked(
                "Possible stuck Conan automation: no match end or successful farm "
                f"completion for {format_duration(idle_seconds)}.\n"
                f"Current stage: {self.current_stage.value}"
                + (f" ({self.game_state})" if self.game_state else "")
                + f"\nLast completed activity: {self._last_progress_description}"
            )

    def send_pending_notifications(self, limit=5):
        """Deliver from the worker; retain the first failed message for retry."""
        for _ in range(limit):
            with self._lock:
                if not self._notifications:
                    return
                message = self._notifications[0]
            if not send_telegram_message(message):
                return
            with self._lock:
                self._notifications.popleft()

    def set_stage(self, stage):
        if not isinstance(stage, ConanStage):
            raise TypeError("stage must be a ConanStage")
        with self._lock:
            self.current_stage = stage
            self.game_state = None

    def set_game_paused(self, game_state):
        with self._lock:
            self.current_stage = ConanStage.GAME_PAUSED
            self.game_state = game_state

    def record_match_finished(self):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self.matches_finished += 1
            self.current_stage = ConanStage.MATCH_FINISHED
            self.game_state = None
            self.last_match_at = timestamp
            self._queue_activity_locked(
                f"Conan match ended. Session matches finished: {self.matches_finished}.",
                progress=True,
            )
        if self.persistent_stats is not None:
            self.persistent_stats.record_match_finished(timestamp)

    def record_restart(self):
        with self._lock:
            self.restarts += 1
        if self.persistent_stats is not None:
            self.persistent_stats.record_restart()

    def set_farm_schedule(self, next_farm_at, pending=False):
        """Publish the next scheduled farm run to the Telegram status view."""
        with self._lock:
            self.next_farm_at = next_farm_at
            self.farm_workflow_pending = bool(pending)

    def snapshot(self):
        with self._lock:
            snapshot = {
                "matches_finished": self.matches_finished,
                "current_stage": self.current_stage,
                "game_state": self.game_state,
                "uptime_seconds": time.monotonic() - self.started_at,
                "last_match_at": self.last_match_at,
                "restarts": self.restarts,
                "next_farm_at": self.next_farm_at,
                "farm_workflow_pending": self.farm_workflow_pending,
                "last_completed_activity": self._last_progress_description,
                "inactivity_warning": self._stuck_alerted,
            }
        snapshot["next_farm_seconds"] = (
            None
            if snapshot["next_farm_at"] is None
            else max(0.0, snapshot["next_farm_at"] - time.monotonic())
        )
        snapshot["persistent"] = (
            self.persistent_stats.snapshot()
            if self.persistent_stats is not None
            else dict(PersistentConanStats.DEFAULT_DATA)
        )
        return snapshot


def stage_text(snapshot):
    stage = snapshot["current_stage"]
    text = stage.value
    if stage is ConanStage.GAME_PAUSED and snapshot["game_state"]:
        text = f"{text}: {snapshot['game_state']}"
    return text


def format_duration(seconds):
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes}m {seconds}s"


def format_stats_message(status):
    snapshot = status.snapshot()
    last_match = snapshot["last_match_at"] or "never"
    if snapshot["farm_workflow_pending"]:
        next_farm = (
            f"{format_duration(snapshot['next_farm_seconds'] or 0)} "
            "(pending; waiting for Conan match to finish)"
        )
    elif snapshot["next_farm_seconds"] is None:
        next_farm = "disabled"
    else:
        next_farm = format_duration(snapshot["next_farm_seconds"])
    return (
        "Conan stats\n"
        f"Matches finished: {snapshot['matches_finished']}\n"
        f"Current stage: {stage_text(snapshot)}\n"
        f"Next farm remaining: {next_farm}\n"
        f"Uptime: {format_duration(snapshot['uptime_seconds'])}\n"
        f"Last match: {last_match}\n"
        f"Last completed activity: {snapshot['last_completed_activity']}\n"
        f"Session restarts: {snapshot['restarts']}\n"
        f"Total matches: {snapshot['persistent']['total_matches_finished']}\n"
        f"Total runs: {snapshot['persistent']['total_runs']}\n"
        f"Total restarts: {snapshot['persistent']['total_restarts']}\n"
        f"Total runtime: {format_duration(snapshot['persistent']['total_runtime_seconds'])}"
    )


def telegram_help_message():
    return (
        "Conan commands\n"
        f"{ConanCommand.STATS.value} - show session and all-time statistics\n"
        f"{ConanCommand.SCREENSHOT.value} - send the current screen\n"
        f"{ConanCommand.PAUSE.value} - pause the automation loop\n"
        f"{ConanCommand.RESUME.value} - resume the automation loop\n"
        f"{ConanCommand.STOP.value} - stop the automation\n"
        f"{ConanCommand.TR_LAUNCH.value} 1|2 - launch and log in to TalesRunner\n"
        f"{ConanCommand.ALARM_WINDOW.value} [interval] [HH:MM] [message] - start alarm window\n"
        f"{ConanCommand.ALARM_WINDOW.value} status - show alarm window status\n"
        f"{ConanCommand.ALARM_WINDOW.value} stop - stop alarm window"
    )


def telegram_is_configured():
    load_dotenv_file()
    return bool(
        os.environ.get("TELEGRAM_BOT_TOKEN")
        and os.environ.get("TELEGRAM_CHAT_ID")
    )


def telegram_api_request(method, payload):
    """Call the Telegram Bot API without logging the bot token."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    data = urlencode(payload).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{bot_token}/{method}",
        data=data,
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        result = json.load(response)

    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram API request failed"))
    return result


def _telegram_error_reason(error):
    return getattr(error, "reason", error)


def send_telegram_message(message, chat_id=None):
    """Send a Telegram message and return whether it was accepted."""
    target_chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not target_chat_id:
        return False

    try:
        telegram_api_request(
            "sendMessage",
            {"chat_id": target_chat_id, "text": message},
        )
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as error:
        print(
            f"Telegram message failed: {_telegram_error_reason(error)}",
            file=sys.stderr,
        )
        return False
    return True


def send_telegram_screenshot(chat_id=None):
    """Capture the current screen and upload it to Telegram."""
    target_chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not target_chat_id or not bot_token:
        return False

    try:
        screenshot = capture_screen()
        image_buffer = BytesIO()
        screenshot.save(image_buffer, format="PNG")
        image_data = image_buffer.getvalue()

        boundary = f"----ConanStatus{uuid4().hex}".encode("ascii")
        body = b"".join(
            (
                b"--" + boundary + b"\r\n",
                b'Content-Disposition: form-data; name="chat_id"\r\n\r\n',
                str(target_chat_id).encode("utf-8"),
                b"\r\n--" + boundary + b"\r\n",
                b'Content-Disposition: form-data; name="photo"; '
                b'filename="conan_status.png"\r\n',
                b"Content-Type: image/png\r\n\r\n",
                image_data,
                b"\r\n--" + boundary + b"--\r\n",
            )
        )
        request = Request(
            f"https://api.telegram.org/bot{bot_token}/sendPhoto",
            data=body,
            headers={
                "Content-Type": (
                    f"multipart/form-data; boundary={boundary.decode('ascii')}"
                )
            },
            method="POST",
        )
        with urlopen(request, timeout=15) as response:
            result = json.load(response)

        if not result.get("ok"):
            raise RuntimeError(
                result.get("description", "Telegram screenshot failed")
            )
    except Exception as error:
        print(
            f"Telegram screenshot failed: {_telegram_error_reason(error)}",
            file=sys.stderr,
        )
        return False
    return True


def telegram_command_for_update(update):
    """Return (chat_id, command, arguments) for an authorized update."""
    message = update.get("message", {})
    chat = message.get("chat", {})
    text = message.get("text", "").strip()
    configured_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not configured_chat_id or str(chat.get("id")) != configured_chat_id:
        return None
    if not text:
        return None

    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    if not parts:
        return None

    command_text = parts[0].split("@", 1)[0].casefold()
    command = next(
        (
            candidate
            for candidate in ConanCommand
            if candidate.value.casefold() == command_text
        ),
        None,
    )
    if command is None:
        return None
    return chat["id"], command, parts[1:]


def authorized_command_for_update(update):
    """Return (chat_id, command) only for the configured Telegram chat."""
    command_info = telegram_command_for_update(update)
    if command_info is None:
        return None
    return command_info[:2]


def parse_alarm_window_arguments(arguments):
    """Parse /alarmWindow arguments into an action and configuration."""
    arguments = list(arguments)
    if not arguments:
        return {
            "action": "start",
            "interval_seconds": ALARM_WINDOW_DEFAULT_INTERVAL_SECONDS,
            "start_time": None,
            "message": ALARM_WINDOW_DEFAULT_MESSAGE,
        }

    action = arguments[0].casefold()
    if action in {"status", "stop", "off", "cancel"}:
        if len(arguments) != 1:
            raise ValueError(f"{action} does not accept additional arguments")
        return {"action": "stop" if action != "status" else "status"}
    if action == "start":
        arguments = arguments[1:]
        if not arguments:
            return parse_alarm_window_arguments(())

    try:
        interval_seconds = float(arguments[0])
    except (TypeError, ValueError) as error:
        raise ValueError(
            "usage: /alarmWindow [interval_seconds] [HH:MM] [message]"
        ) from error
    if not interval_seconds > 0:
        raise ValueError("interval_seconds must be greater than 0")

    start_time = None
    message_start = 1
    if len(arguments) > 1 and len(arguments[1]) == 5 and arguments[1][2] == ":":
        try:
            datetime.strptime(arguments[1], "%H:%M")
        except ValueError as error:
            raise ValueError("start time must use HH:MM format") from error
        start_time = arguments[1]
        message_start = 2

    message = " ".join(arguments[message_start:]).strip()
    return {
        "action": "start",
        "interval_seconds": interval_seconds,
        "start_time": start_time,
        "message": message or ALARM_WINDOW_DEFAULT_MESSAGE,
    }


def handle_telegram_command(
    update,
    status,
    pause_event,
    stop_event,
    alarm_window_controller=None,
    talesrunner_launcher_controller=None,
):
    """Handle one authorized command and return a text reply, if applicable."""
    command_info = telegram_command_for_update(update)
    if command_info is None:
        return None

    _, command, arguments = command_info
    if command is ConanCommand.HELP:
        return telegram_help_message()
    if command is ConanCommand.STATS:
        return format_stats_message(status)
    if command is ConanCommand.PAUSE:
        pause_event.set()
        status.set_watchdog_paused(True)
        status.set_stage(ConanStage.PAUSED_BY_COMMAND)
        return "Conan automation paused."
    if command is ConanCommand.RESUME:
        pause_event.clear()
        status.set_watchdog_paused(False)
        status.set_stage(ConanStage.CHECKING_GAME_STATE)
        return "Conan automation resumed."
    if command is ConanCommand.STOP:
        stop_event.set()
        pause_event.clear()
        if alarm_window_controller is not None:
            alarm_window_controller.stop()
        return "Stopping Conan automation."
    if command is ConanCommand.TR_LAUNCH:
        if len(arguments) != 1 or arguments[0] not in {"1", "2"}:
            return "Usage: /trLaunch 1|2"
        try:
            controller = (
                talesrunner_launcher_controller
                or TalesRunnerLauncherController()
            )
            controller.start(arguments[0])
            return (
                "TalesRunner launcher automation started for profile "
                f"{arguments[0]}. Its PowerShell terminal will remain open for logs."
            )
        except (FileNotFoundError, OSError, ValueError) as error:
            return f"Unable to start TalesRunner launcher: {error}"
    if command is ConanCommand.ALARM_WINDOW:
        try:
            options = parse_alarm_window_arguments(arguments)
            controller = alarm_window_controller or AlarmWindowController()
            if options["action"] == "status":
                state = "running" if controller.is_running() else "stopped"
                return f"Alarm window status: {state}."
            if options["action"] == "stop":
                stopped = controller.stop()
                return (
                    "Alarm window stopped."
                    if stopped
                    else "Alarm window is not running."
                )
            started = controller.start(
                interval_seconds=options["interval_seconds"],
                start_time=options["start_time"],
                title=ALARM_WINDOW_DEFAULT_TITLE,
                message=options["message"],
            )
            if not started:
                return "Alarm window is already running."
            return (
                "Alarm window started: every "
                f"{options['interval_seconds']:g} seconds"
                + (f", starting at {options['start_time']} HKT"
                   if options["start_time"] else "")
                + "."
            )
        except (FileNotFoundError, OSError, ValueError) as error:
            return f"Unable to start alarm window: {error}"
    if command is ConanCommand.SCREENSHOT:
        return None
    return None


def acknowledge_telegram_updates(offset):
    """Confirm processed updates so commands are not replayed after restart."""
    if offset is None:
        return
    try:
        telegram_api_request(
            "getUpdates",
            {"offset": offset, "timeout": 0},
        )
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as error:
        print(
            f"Telegram update acknowledgment failed: {_telegram_error_reason(error)}",
            file=sys.stderr,
        )


def initialize_telegram_offset(persistent_stats):
    """Discard queued commands when no previous Telegram offset exists."""
    if persistent_stats.telegram_update_offset() is not None:
        return

    try:
        result = telegram_api_request(
            "getUpdates",
            {"timeout": 0},
        )
        updates = result.get("result", [])
        update_ids = [
            update.get("update_id")
            for update in updates
            if update.get("update_id") is not None
        ]
        if update_ids:
            offset = max(update_ids) + 1
            persistent_stats.record_telegram_update(offset)
            acknowledge_telegram_updates(offset)
            print("* Ignored stale Telegram commands from before startup *")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as error:
        print(
            f"Telegram startup update cleanup failed: {_telegram_error_reason(error)}",
            file=sys.stderr,
        )


def monitor_conan_activity(status, pause_event, shutdown_event):
    """Send activity logs and watch for stalls independently of game polling."""
    while not shutdown_event.is_set():
        status.check_inactivity(paused=pause_event.is_set())
        status.send_pending_notifications()
        if shutdown_event.wait(ACTIVITY_MONITOR_INTERVAL_SECONDS):
            break
    # Farm cancellation/completion can be queued as the main loop shuts down.
    status.send_pending_notifications()


def poll_telegram_commands(
    status, pause_event, stop_event, persistent_stats=None,
    alarm_window_controller=None, talesrunner_launcher_controller=None,
):
    """Listen for authorized Telegram commands until the process stops."""
    offset = (
        persistent_stats.telegram_update_offset()
        if persistent_stats is not None
        else None
    )
    retry_delay = TELEGRAM_RETRY_DELAY
    polling_interrupted = False
    while not stop_event.is_set():
        payload = {"timeout": TELEGRAM_POLL_TIMEOUT}
        if offset is not None:
            payload["offset"] = offset

        try:
            result = telegram_api_request("getUpdates", payload)
            if polling_interrupted:
                print("* Telegram command polling recovered *")
                polling_interrupted = False
            retry_delay = TELEGRAM_RETRY_DELAY
            for update in result.get("result", []):
                update_id = update.get("update_id")
                if update_id is not None:
                    offset = update_id + 1
                    if persistent_stats is not None:
                        # Persist before handling the command so /conanStop
                        # cannot be replayed after a process restart.
                        persistent_stats.record_telegram_update(offset)

                command_info = authorized_command_for_update(update)
                if command_info is None:
                    continue

                chat_id, command = command_info
                if command is ConanCommand.SCREENSHOT:
                    if not send_telegram_screenshot(chat_id=chat_id):
                        send_telegram_message(
                            "Unable to capture or send the screenshot.",
                            chat_id=chat_id,
                        )
                    continue

                reply = handle_telegram_command(
                    update,
                    status,
                    pause_event,
                    stop_event,
                    alarm_window_controller=alarm_window_controller,
                    talesrunner_launcher_controller=talesrunner_launcher_controller,
                )
                if reply is not None:
                    send_telegram_message(reply, chat_id=chat_id)

            if stop_event.is_set():
                acknowledge_telegram_updates(offset)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as error:
            polling_interrupted = True
            print(
                "Telegram command polling failed; "
                f"retrying in {retry_delay} seconds: {_telegram_error_reason(error)}",
                file=sys.stderr,
            )
            if stop_event.wait(retry_delay):
                break
            retry_delay = min(retry_delay * 2, TELEGRAM_MAX_RETRY_DELAY)


def target_image_path(profile=None):
    image_name = TARGET_IMAGE_NAME if profile is None else profile.target_image_name
    return BASE_DIR / "scr" / image_name


def match_end_image_path(profile=None):
    image_name = MATCH_END_IMAGE_NAME if profile is None else profile.match_end_image_name
    return BASE_DIR / "scr" / image_name


def locate_on_primary_screen(image_path, confidence=MATCH_CONFIDENCE):
    """Locate an image on the primary screen."""
    return locate_on_screen(
        image_path,
        confidence=confidence,
    )


def validate_configuration(
    require_conan=True, require_schedule=True, require_farm=True, profile=None
):
    """Validate the settings required by normal or farm-only execution."""
    if require_conan:
        required_images = (
            (
                target_image_path(profile),
                "任務開始圖片",
                "TARGET_IMAGE_NAME",
            ),
            (
                match_end_image_path(profile),
                "任務結束圖片",
                "MATCH_END_IMAGE_NAME",
            ),
        )
        for image_path, image_label, setting_name in required_images:
            if not image_path.is_file():
                raise FileNotFoundError(
                    f"找不到{image_label}: {image_path}。"
                    f"請將圖片新增至 scr/ 並更新 {setting_name}。"
                )
        if ACTION not in {"click", "key"}:
            raise ValueError("ACTION 必須為 'click' 或 'key'")
        if ACTION == "key" and not KEYBOARD_KEY:
            raise ValueError("當 ACTION 為 'key' 時，必須設定 KEYBOARD_KEY")
        if MATCH_END_WAIT_SECONDS < 0:
            raise ValueError("MATCH_END_WAIT_SECONDS 必須大於或等於 0")
    if require_schedule and SCHEDULED_IMAGE_INTERVAL_SECONDS <= 0:
        raise ValueError("SCHEDULED_IMAGE_INTERVAL_SECONDS 必須大於 0")
    if require_farm:
        create_farm_automation().validate_configuration()


def create_farm_automation():
    """Use Conan's farm settings with the shared live automation module."""
    return FarmAutomation(FARM_SETTINGS)


def clear_expired_prompt():
    """Share the farm prompt handling with Conan's match automation."""
    return create_farm_automation().clear_expired_prompt()


def run_farm_workflow(status=None, stop_event=None, pause_event=None):
    """Report Conan status while the reusable module performs the farm run."""
    if status is not None:
        status.set_stage(ConanStage.FARM_WORKFLOW)
        status.record_farm_started()
    try:
        completed = create_farm_automation().run(
            stop_event=stop_event,
            pause_event=pause_event,
        )
        if status is not None:
            cancelled = (
                (stop_event is not None and stop_event.is_set())
                or (pause_event is not None and pause_event.is_set())
            )
            status.record_farm_finished(
                "completed" if completed else "cancelled" if cancelled else "failed"
            )
        return completed
    except Exception as error:
        if status is not None:
            status.record_farm_finished(f"interrupted ({type(error).__name__})")
        raise
    except KeyboardInterrupt:
        if status is not None:
            status.record_farm_finished("cancelled")
        raise

def initialize_persistent_farm_schedule(persistent_stats, enabled=None):
    """Load the wall-clock farm schedule and detect an overdue farm run."""
    if enabled is None:
        enabled = FARM_WORKFLOW_ENABLED
    if not enabled:
        return None, False

    current_time = time.time()
    next_farm_at = persistent_stats.next_farm_at()
    if next_farm_at is None:
        next_farm_at = current_time + SCHEDULED_IMAGE_INTERVAL_SECONDS
        persistent_stats.record_next_farm_at(next_farm_at)

    next_trigger_at = time.monotonic() + max(0.0, next_farm_at - current_time)
    return next_trigger_at, current_time >= next_farm_at


def schedule_next_farm_run(persistent_stats=None):
    """Schedule the next farm run and persist its wall-clock timestamp."""
    next_farm_at = time.time() + SCHEDULED_IMAGE_INTERVAL_SECONDS
    if persistent_stats is not None:
        persistent_stats.record_next_farm_at(next_farm_at)
    return time.monotonic() + SCHEDULED_IMAGE_INTERVAL_SECONDS


def mark_farm_workflow_pending_if_enabled(next_trigger_at, pending=False, enabled=None):
    """Keep the farm flow disabled without changing Conan match automation."""
    if enabled is None:
        enabled = FARM_WORKFLOW_ENABLED
    if not enabled:
        return False
    return mark_farm_workflow_pending_if_due(next_trigger_at, pending=pending)


def mark_farm_workflow_pending_if_due(next_trigger_at, pending=False):
    """Mark a due farm run pending without interrupting the current match."""
    if pending or time.monotonic() < next_trigger_at:
        return pending
    print("* 農場流程已排程，等待 Conan 對戰結束 *")
    return True


def run_pending_farm_workflow_if_match_finished(
    next_trigger_at,
    pending,
    conan_match_finished,
    status=None,
    stop_event=None,
    pause_event=None,
    persistent_stats=None,
):
    """Run a pending farm flow only after Conan's match-finished image appears."""
    if not pending or not conan_match_finished:
        return next_trigger_at, pending

    run_farm_workflow(
        status=status,
        stop_event=stop_event,
        pause_event=pause_event,
    )
    if stop_event is not None and stop_event.is_set():
        return next_trigger_at, pending
    return schedule_next_farm_run(persistent_stats), False

def run_task_step(status=None, profile=None):
    """Detect and trigger the Conan task-start image."""
    if not clear_expired_prompt():
        return False
    triggered = triggerIfDetected(
        target_image_path(profile),
        action=ACTION,
        keyboard_key=KEYBOARD_KEY,
        confidence=MATCH_CONFIDENCE,
        region=None,
        scales=(0.75, 0.875, 1.125, 1.25, 1.5, 1.75, 2.0),
    )
    if triggered:
        print(f"* 已觸發 {target_image_path(profile).name} 的任務動作 *")
        if profile is not None and profile.on_task_triggered is not None:
            profile.on_task_triggered()
    return triggered


def is_match_end_visible(profile=None):
    """Return whether the configured Conan task-end image is on screen."""
    image_path = match_end_image_path(profile)
    if not image_path.is_file():
        return False
    try:
        return locate_on_primary_screen(image_path) is not None
    except ScreenCaptureUnavailable:
        raise
    except (OSError, TypeError, ValueError):
        return False


def detect_match_finished(status=None, previously_visible=False, profile=None):
    """Detect one Conan completion event and ignore a persistent end screen."""
    visible = is_match_end_visible() if profile is None else is_match_end_visible(profile)
    finished = visible and not previously_visible
    if finished:
        print(f"* 偵測到 {match_end_image_path(profile).name}，任務完成 *")
        if status is not None:
            status.record_match_finished()
    return visible, finished


def wait_after_match_finished(stop_event=None, pause_event=None):
    """Wait after a match while honoring stop and pause commands."""
    if MATCH_END_WAIT_SECONDS <= 0:
        return True

    print(
        f"* Match completed; waiting {MATCH_END_WAIT_SECONDS} seconds "
        "before the pending workflow *"
    )
    deadline = time.monotonic() + MATCH_END_WAIT_SECONDS
    while True:
        if stop_event is not None and stop_event.is_set():
            return False

        if pause_event is not None and pause_event.is_set():
            if stop_event is not None:
                if stop_event.wait(LOOP_INTERVAL):
                    return False
            else:
                time.sleep(LOOP_INTERVAL)
            continue

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True

        wait_seconds = min(LOOP_INTERVAL, remaining)
        if stop_event is not None:
            if stop_event.wait(wait_seconds):
                return False
        else:
            time.sleep(wait_seconds)


def parse_args(argv=None):
    """Parse optional command-line modes for the Conan automation script."""
    parser = argparse.ArgumentParser(
        description="Run Conan automation or a single farm-workflow test."
    )
    parser.add_argument(
        "--test-farm-workflow",
        action="store_true",
        help=(
            "Run only one live farm workflow; skips Conan polling, scheduling, "
            "Telegram, and automatic Shift pressing."
        ),
    )
    return parser.parse_args(argv)


def main(
    test_farm_workflow=False, *, profile=None,
    stop_event=None, farm_workflow_enabled=None,
):
    with use_screen_capture(GameScreenCapture(get_tales_runner_window)):
        control_options = {} if stop_event is None else {"stop_event": stop_event}
        return _main(
            test_farm_workflow, profile=profile,
            farm_workflow_enabled=farm_workflow_enabled, **control_options,
        )


def _main(
    test_farm_workflow=False, *, profile=None,
    stop_event=None, farm_workflow_enabled=None,
):
    load_dotenv_file()
    farm_enabled = (
        FARM_WORKFLOW_ENABLED
        if farm_workflow_enabled is None else farm_workflow_enabled
    )
    # The explicit one-shot workflow test always runs the farm regardless of
    # whether scheduled farming is disabled for this automation.
    farm_required = farm_enabled or test_farm_workflow
    profile_options = {} if profile is None else {"profile": profile}
    stats_path = CONAN_STATS_PATH if profile is None else profile.stats_path
    custom_input_worker = None if profile is None else profile.input_worker
    switch_windows = (
        SWITCH_TO_PREVIOUS_WINDOW_ENABLED
        if profile is None else False
    )
    post_match_stage = None if profile is None else profile.post_match_stage
    validate_configuration(
        require_conan=not test_farm_workflow,
        require_schedule=farm_enabled and not test_farm_workflow,
        require_farm=farm_required,
        **profile_options,
    )
    if test_farm_workflow:
        persistent_stats = PersistentConanStats(stats_path)
        print("* 執行單次農場流程測試 *")
        completed = run_farm_workflow()
        if completed:
            schedule_next_farm_run(persistent_stats)
            print("* 農場流程測試成功，已更新下一次農場時間 *")
        print("* 農場流程測試完成 *" if completed else "* 農場流程測試失敗 *")
        return 0 if completed else 1

    focus_options = (
        {} if profile is None or profile.focus_game_window is None
        else {"focus_game_window": profile.focus_game_window}
    )
    supervisor = GameSupervisor.from_config(
        screen_region=None,
        **focus_options,
    )
    persistent_stats = PersistentConanStats(stats_path)
    if post_match_stage is not None:
        post_match_stage.initialize_schedule(
            persistent_stats.next_coupon_workflow_at(),
            persistent_stats.record_next_coupon_workflow_at,
            last_completed_at=persistent_stats.last_coupon_workflow_completed_at(),
            save_last_completed_at=persistent_stats.record_last_coupon_workflow_completed_at,
        )
    persistent_stats.record_run_started()
    telegram_enabled = telegram_is_configured()
    status = ConanStatus(
        persistent_stats, telegram_notifications_enabled=telegram_enabled
    )
    if profile is not None and profile.on_status_ready is not None:
        profile.on_status_ready(status)
    shift_stop_event = threading.Event()
    pause_event = threading.Event()
    externally_controlled = stop_event is not None
    if stop_event is None:
        stop_event = threading.Event()
    shift_press_thread = None
    telegram_thread = None
    activity_thread = None
    activity_shutdown_event = None
    alarm_window_controller = AlarmWindowController()
    talesrunner_launcher_controller = TalesRunnerLauncherController()
    next_scheduled_image_at, farm_workflow_pending = (
        initialize_persistent_farm_schedule(persistent_stats, enabled=farm_enabled)
    )
    startup_farm_pending = farm_workflow_pending
    status.set_farm_schedule(
        next_scheduled_image_at if farm_enabled else None,
        pending=farm_workflow_pending,
    )
    conan_end_image_visible = False
    screen_unavailable = False

    def stop_repeating_input():
        shift_stop_event.set()
        if custom_input_worker is not None and shift_press_thread is not None:
            shift_press_thread.join(timeout=2)
        if profile is not None and profile.release_input is not None:
            profile.release_input()

    if not farm_enabled:
        print("* 農場流程已停用；Conan 任務將繼續執行 *")

    if telegram_enabled:
        initialize_telegram_offset(persistent_stats)
        telegram_thread = threading.Thread(
            target=poll_telegram_commands,
            args=(status, pause_event, stop_event, persistent_stats),
            kwargs={
                "alarm_window_controller": alarm_window_controller,
                "talesrunner_launcher_controller": talesrunner_launcher_controller,
            },
            name="telegram-commands",
            daemon=True,
        )
        telegram_thread.start()
        activity_shutdown_event = threading.Event()
        activity_thread = threading.Thread(
            target=monitor_conan_activity,
            args=(status, pause_event, activity_shutdown_event),
            name="conan-activity-monitor",
            daemon=True,
        )
        activity_thread.start()
        print("* Telegram Conan command monitor started *")
    else:
        print("* Telegram commands disabled: credentials are not configured *")

    try:
        while not stop_event.is_set():
            try:
                if post_match_stage is not None:
                    post_match_stage.update()
                if pause_event.is_set():
                    stop_repeating_input()
                    status.set_stage(ConanStage.PAUSED_BY_COMMAND)
                    stop_event.wait(LOOP_INTERVAL)
                    continue
                previous_window = (
                    get_active_window()
                    if SWITCH_TO_PREVIOUS_WINDOW_METHOD == "previous_window"
                    else None
                )
                if not focus_options and not clear_expired_prompt():
                    if custom_input_worker is not None:
                        stop_repeating_input()
                    stop_event.wait(LOOP_INTERVAL)
                    continue
                status.set_stage(ConanStage.CHECKING_GAME_STATE)
                game_state = supervisor.ensure_ready()
                if focus_options and game_state == "ready" and not clear_expired_prompt():
                    stop_repeating_input()
                    stop_event.wait(LOOP_INTERVAL)
                    continue
                if (
                    game_state == "ready"
                    and post_match_stage is not None
                    and post_match_stage.startup_pending
                ):
                    stop_repeating_input()
                    status.set_stage(ConanStage.POST_MATCH_WORKFLOW)
                    post_match_stage.run_on_start_if_pending(
                        stop_event=stop_event,
                        pause_event=pause_event,
                    )
                    continue
                if startup_farm_pending and game_state in ("ready", "started"):
                    stop_repeating_input()
                    status.set_stage(ConanStage.FARM_WORKFLOW)
                    run_farm_workflow(
                        status=status,
                        stop_event=stop_event,
                        pause_event=pause_event,
                    )
                    if stop_event.is_set():
                        continue
                    next_scheduled_image_at = schedule_next_farm_run(
                        persistent_stats
                    )
                    farm_workflow_pending = False
                    startup_farm_pending = False
                    status.set_farm_schedule(
                        next_scheduled_image_at,
                        pending=False,
                    )
                    continue

                if game_state == "ready":
                    status.set_stage(ConanStage.CHECKING_SCHEDULED_IMAGE)
                    farm_workflow_pending = mark_farm_workflow_pending_if_enabled(
                        next_scheduled_image_at,
                        pending=farm_workflow_pending,
                        enabled=farm_enabled,
                    )
                    status.set_farm_schedule(
                        next_scheduled_image_at if farm_enabled else None,
                        pending=farm_workflow_pending,
                    )

                    input_enabled = (
                        automatic_shift_press_enabled()
                        if custom_input_worker is None else not switch_windows
                    )
                    if input_enabled and (
                        shift_press_thread is None or not shift_press_thread.is_alive()
                    ):
                        shift_stop_event.clear()
                        if custom_input_worker is None:
                            shift_press_thread = threading.Thread(
                                target=autoPressButton,
                                kwargs={
                                    "button": "shift",
                                    "press_interval": 10,
                                    "random_interval": SHIFT_RANDOM_INTERVAL,
                                    "stop_condition": shift_stop_event.is_set,
                                },
                                daemon=True,
                            )
                        else:
                            shift_press_thread = threading.Thread(
                                target=custom_input_worker,
                                args=(shift_stop_event, pause_event, stop_event),
                                name="task-input",
                                daemon=True,
                            )
                        shift_press_thread.start()
                    status.set_stage(ConanStage.WAITING_FOR_MATCH)
                    run_task_step(**profile_options)
                    (
                        conan_end_image_visible,
                        conan_match_finished,
                    ) = detect_match_finished(
                        status,
                        previously_visible=conan_end_image_visible,
                        **profile_options,
                    )

                    if post_match_stage is not None:
                        post_match_stage.update()

                    if farm_workflow_pending and not conan_match_finished:
                        status.set_stage(ConanStage.FARM_WORKFLOW_PENDING)

                    post_match_pending = (
                        post_match_stage is not None and post_match_stage.pending
                    )
                    if post_match_pending and not conan_match_finished:
                        status.set_stage(ConanStage.POST_MATCH_WORKFLOW_PENDING)

                    if (farm_workflow_pending or post_match_pending) and conan_match_finished:
                        stop_repeating_input()
                        if shift_press_thread is not None and shift_press_thread.is_alive():
                            shift_press_thread.join(timeout=12)
                        status.set_stage(ConanStage.WAITING_AFTER_MATCH_FINISHED)
                        if wait_after_match_finished(
                            stop_event=stop_event,
                            pause_event=pause_event,
                        ):
                            if farm_workflow_pending:
                                (
                                    next_scheduled_image_at,
                                    farm_workflow_pending,
                                ) = run_pending_farm_workflow_if_match_finished(
                                    next_scheduled_image_at,
                                    farm_workflow_pending,
                                    conan_match_finished,
                                    status=status,
                                    stop_event=stop_event,
                                    pause_event=pause_event,
                                    persistent_stats=persistent_stats,
                                )
                            if post_match_stage is not None and post_match_stage.pending:
                                status.set_stage(ConanStage.POST_MATCH_WORKFLOW)
                                post_match_stage.run_if_pending(
                                    stop_event=stop_event,
                                    pause_event=pause_event,
                                )
                            status.set_farm_schedule(
                                next_scheduled_image_at,
                                pending=farm_workflow_pending,
                            )
                else:
                    stop_repeating_input()
                    conan_end_image_visible = False
                    if game_state == "restarted":
                        status.record_restart()
                    status.set_game_paused(game_state)
                    print(f"* 遊戲狀態為 {game_state}，任務已暫停 *")

                if switch_windows and not stop_event.is_set():
                    if custom_input_worker is not None:
                        stop_repeating_input()
                    switch_to_previous_window(previous_window)
            except ScreenCaptureUnavailable as error:
                stop_repeating_input()
                if status.snapshot()["current_stage"] is ConanStage.FARM_WORKFLOW:
                    startup_farm_pending = True
                status.set_stage(ConanStage.SCREEN_UNAVAILABLE)
                if not screen_unavailable:
                    print(f"* Screen capture unavailable; retrying: {error} *")
                screen_unavailable = True
            else:
                if screen_unavailable:
                    print("* Screen capture recovered; Conan automation resumed *")
                    screen_unavailable = False
            stop_event.wait(LOOP_INTERVAL)
    finally:
        stop_repeating_input()
        stop_event.set()
        if activity_shutdown_event is not None:
            activity_shutdown_event.set()
        alarm_window_controller.stop()
        persistent_stats.record_runtime(time.monotonic() - status.started_at)
        if telegram_thread is not None:
            telegram_thread.join(timeout=None if externally_controlled else 2)
        if activity_thread is not None:
            activity_thread.join(timeout=None if externally_controlled else 2)


if __name__ == "__main__":
    try:
        arguments = parse_args()
        sys.exit(
            main(
                test_farm_workflow=arguments.test_farm_workflow,
            )
        )
    except gui.ImageNotFoundException:
        pass
    except (FileNotFoundError, ValueError) as error:
        print(f"** {error} **")
    except KeyboardInterrupt:
        print("\n* 任務已停止 *")
