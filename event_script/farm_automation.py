"""Live farm automation reusable by Conan and other Tales Runner scripts.

FarmAutomation.run() performs one farm run. It does not schedule work, load
Telegram credentials, update Conan statistics, or start background workers.
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path

import pydirectinput as key

from event_script.farm_workflow import FarmWorkflow, FarmWorkflowConfig
from screen_capture import ScreenCaptureUnavailable
from utils import locate_on_screen, pressButton


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_FARM_WORKFLOW_CONFIG = FarmWorkflowConfig(
    initial_image="farm/farm.png",
    entry_image_names=("yes.png", "farm/enter_my_farm.png"),
    crops_start_image="farm/crops_management.png",
    crops_collection_image_names=("farm/crops_management_button_1.png",),
    crops_failure_image="farm/crops_management_failure.png",
    crops_confirmation_image="farm/crops_management_okButton.png",
    crops_failure_confirmation_image="okButton.png",
    cross_image_names=("farm_cross.png", "cross.png"),
    field_image_names=(
        "farm/farm_shop.png", "farm/field.png", "farm/target_field.png",
    ),
    purchase_image_names=(
        "farm/farm_cart.png", "buy.png", "farm/farm_cart_yes_button.png",
        "farm/farm_cart_yes_button.png", "okButton.png",
    ),
    return_image_names=("waiting_room.png", "yes.png", "special_event.png"),
)


@dataclass(frozen=True)
class FarmAutomationSettings:
    """Paths, image sequence, and timing for one script's farm automation.

    Replace workflow.return_image_names to choose another destination, or use
    an empty tuple to finish in the farm after closing its shop.
    """

    workflow: FarmWorkflowConfig = DEFAULT_FARM_WORKFLOW_CONFIG
    image_dir: Path = BASE_DIR / "scr"
    click_positions_path: Path = BASE_DIR / "farm_click_positions.json"
    expired_image_name: str = "expired.png"
    match_confidence: float = 0.89
    image_timeout_seconds: float = 20
    poll_interval_seconds: float = 0.5
    step_retries: int = 2
    target_field_delay_seconds: float = 3
    crop_confirmation_delay_seconds: float = 3
    purchase_step_delay_seconds: float = 1
    shift_click_delay_seconds: float = 0.3


def get_tales_runner_window():
    """Return the first usable Tales Runner window, if one is available."""
    from game_supervisor import gameWindows

    try:
        windows = gameWindows()
    except (AttributeError, OSError, RuntimeError):
        return None

    for window in windows:
        try:
            if float(window.width) > 0 and float(window.height) > 0:
                return window
        except (AttributeError, TypeError, ValueError):
            continue
    return None


def get_window_geometry(window):
    """Return a window's geometry, or None when its size is unusable."""
    try:
        left = float(window.left)
        top = float(window.top)
        width = float(window.width)
        height = float(window.height)
    except (AttributeError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return left, top, width, height


def _clear_game_prompt():
    from game_supervisor import clearPrompt

    return clearPrompt()


class FarmAutomation:
    """Run live farm steps with optional stop/pause events on each call.

    Uses the active utils screen-capture backend when the caller installed one.
    The caller owns game supervision, focus, capture recovery, and scheduling.
    """

    def __init__(self, settings=None, *, window_provider=None, clear_prompt=None):
        self.settings = settings if settings is not None else FarmAutomationSettings()
        self.window_provider = (
            window_provider if window_provider is not None else get_tales_runner_window
        )
        self.clear_prompt = clear_prompt if clear_prompt is not None else _clear_game_prompt

    def validate_configuration(self):
        settings = self.settings
        if settings.image_timeout_seconds < 0:
            raise ValueError("image_timeout_seconds must be zero or greater")
        if settings.step_retries < 1:
            raise ValueError("step_retries must be at least 1")
        if settings.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be greater than zero")
        if settings.shift_click_delay_seconds < 0:
            raise ValueError("shift_click_delay_seconds must be zero or greater")
        image_path = self.farm_image_path(settings.workflow.initial_image)
        if not image_path.is_file():
            print(f"* 找不到定時圖片: {image_path}，定時農場流程將會被跳過 *")

    def locate_on_primary_screen(self, image_path):
        return locate_on_screen(image_path, confidence=self.settings.match_confidence)

    def run(self, *, stop_event=None, pause_event=None):
        """Run once; return False on failure or cancellation.

        Capture failures propagate so the caller can wait for frames and retry.
        Recovery uses the configured return images, including an empty sequence.
        """
        self.validate_configuration()
        print("* 開始農場流程 *")
        workflow = self.create_farm_workflow(
            stop_event=stop_event, pause_event=pause_event,
        )
        completed = workflow.run()
        cancelled = (
            (stop_event is not None and stop_event.is_set())
            or (pause_event is not None and pause_event.is_set())
        )
        if not completed and not cancelled:
            if self.clear_prompts_until_unblocked(
                stop_event=stop_event, pause_event=pause_event,
            ):
                print("* 開始返回流程 *")
                workflow.run_return_sequence()
        print("* 農場流程完成 *" if completed else "* 農場流程失敗 *")
        return completed

    def expired_image_path(self):
        return self.settings.image_dir / self.settings.expired_image_name

    def farm_image_path(self, image_name):
        return self.settings.image_dir / image_name

    def clear_expired_prompt(self):
        """Dismiss an expired-item prompt before the next automation action."""
        image_path = self.expired_image_path()
        if not image_path.is_file():
            return True

        try:
            expired_position = self.locate_on_primary_screen(image_path)
        except ScreenCaptureUnavailable:
            raise
        except (OSError, TypeError, ValueError):
            return True
        if expired_position is None:
            return True

        for image_name in self.settings.workflow.cross_image_names:
            cross_path = self.farm_image_path(image_name)
            if not cross_path.is_file():
                continue
            try:
                cross_position = self.locate_on_primary_screen(cross_path)
            except ScreenCaptureUnavailable:
                raise
            except (OSError, TypeError, ValueError):
                continue
            if cross_position is not None:
                pressButton(cross_position)
                print(f"* 偵測到 {self.settings.expired_image_name}，已點擊 {image_name} *")
                return True

        print(f"* 偵測到 {self.settings.expired_image_name}，等待關閉按鈕 *")
        return False

    def _wait_for_any_farm_image_result(
        self, image_names, stop_event=None, pause_event=None
    ):
        """Wait for any configured farm image and return its name and position."""
        image_names = tuple(image_names)
        image_paths = tuple(
            (image_name, self.farm_image_path(image_name))
            for image_name in image_names
            if self.farm_image_path(image_name).is_file()
        )
        names = ", ".join(image_names)
        if not image_paths:
            print(f"* 農場步驟缺少圖片模板: {names} *")
            return None

        started_at = time.monotonic()
        deadline = started_at + self.settings.image_timeout_seconds
        print(
            "* 等待農場圖片: "
            f"{names}（最多 {self.settings.image_timeout_seconds} 秒，"
            f"每 {self.settings.poll_interval_seconds} 秒檢查）*"
        )
        while True:
            if (stop_event is not None and stop_event.is_set()) or (
                pause_event is not None and pause_event.is_set()
            ):
                print(f"* 等待農場圖片已取消: {names} *")
                return None

            for image_name, image_path in image_paths:
                position = self.locate_on_primary_screen(image_path)
                if position is not None:
                    elapsed = time.monotonic() - started_at
                    print(f"* 找到農場圖片: {image_path.name}（{elapsed:.1f} 秒）*")
                    return image_name, position

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print(
                    f"* 等待農場圖片逾時: {names} "
                    f"（{self.settings.image_timeout_seconds} 秒）*"
                )
                return None
            time.sleep(min(self.settings.poll_interval_seconds, remaining))

    def wait_for_any_farm_image(
        self, image_names, stop_event=None, pause_event=None
    ):
        """Wait for any configured farm image and return its screen position."""
        result = self._wait_for_any_farm_image_result(
            image_names,
            stop_event=stop_event,
            pause_event=pause_event,
        )
        return result[1] if result is not None else None

    def wait_for_any_farm_image_name(
        self, image_names, stop_event=None, pause_event=None
    ):
        """Wait for any configured farm image and return the matched image name."""
        result = self._wait_for_any_farm_image_result(
            image_names,
            stop_event=stop_event,
            pause_event=pause_event,
        )
        return result[0] if result is not None else None

    def wait_for_farm_image_then_click(
        self, image_name, stop_event=None, pause_event=None
    ):
        """Wait for one farm button and click it once when it appears."""
        position = self.wait_for_any_farm_image(
            (image_name,),
            stop_event=stop_event,
            pause_event=pause_event,
        )
        if position is None:
            return False
        if not self.clear_expired_prompt():
            return False
        pressButton(position)
        print(f"* 已點擊農場圖片 {image_name} *")
        return True

    def wait_for_any_farm_image_then_click(
        self, image_names, stop_event=None, pause_event=None
    ):
        """Wait for any supplied farm button and click the detected one once."""
        position = self.wait_for_any_farm_image(
            image_names,
            stop_event=stop_event,
            pause_event=pause_event,
        )
        if position is None:
            return False
        if not self.clear_expired_prompt():
            return False
        pressButton(position)
        print(f"* 已點擊農場圖片之一: {', '.join(image_names)} *")
        return True

    def is_farm_image_visible(self, image_name):
        """Return whether one existing farm template is currently on screen."""
        image_path = self.farm_image_path(image_name)
        if not image_path.is_file():
            return False
        try:
            visible = self.locate_on_primary_screen(image_path) is not None
        except ScreenCaptureUnavailable:
            raise
        except (OSError, TypeError, ValueError):
            return False
        if visible:
            print(f"* 偵測到農作物管理失敗圖片: {image_name} *")
        return visible

    def click_initial_farm_button(self, stop_event=None, pause_event=None):
        """Wait for and click the initial farm button once."""
        return self.wait_for_farm_image_then_click(
            self.settings.workflow.initial_image,
            stop_event=stop_event,
            pause_event=pause_event,
        )

    def load_farm_click_positions(self):
        """Load farm positions and convert window-relative values to screen points."""
        try:
            data = json.loads(self.settings.click_positions_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return ()

        if isinstance(data, dict):
            raw_positions = data.get("positions")
            coordinate_mode = data.get("coordinate_mode", "screen")
        else:
            raw_positions = data
            coordinate_mode = "screen"
        if not isinstance(raw_positions, list):
            return ()

        geometry = None
        if coordinate_mode == "window_relative":
            geometry = get_window_geometry(self.window_provider())
            if geometry is None:
                print("* 找不到 Tales Runner 視窗，無法換算農場位置 *")
                return ()

        positions = []
        for item in raw_positions:
            if isinstance(item, dict):
                if item.get("enabled", True) is False:
                    continue
                x, y = item.get("x"), item.get("y")
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                x, y = item
            else:
                continue

            try:
                if coordinate_mode == "window_relative":
                    x = float(x)
                    y = float(y)
                    if not (0 <= x <= 1 and 0 <= y <= 1):
                        continue
                    left, top, width, height = geometry
                    positions.append(
                        (
                            int(round(left + x * width)),
                            int(round(top + y * height)),
                        )
                    )
                else:
                    positions.append((int(x), int(y)))
            except (TypeError, ValueError):
                continue
        return tuple(positions)

    def hold_shift_and_click_positions(
        self, positions, stop_event=None, pause_event=None
    ):
        """Hold Shift while left-clicking every recorded position once."""
        if not positions:
            print("* 沒有可用的農場 Shift-click 位置 *")
            return False

        if (stop_event is not None and stop_event.is_set()) or (
            pause_event is not None and pause_event.is_set()
        ):
            return False

        print(f"* 開始 Shift-click 農田位置，共 {len(positions)} 個 *")
        key.keyDown("shift")
        try:
            for x, y in positions:
                if (stop_event is not None and stop_event.is_set()) or (
                    pause_event is not None and pause_event.is_set()
                ):
                    return False
                key.click(x, y)
                if self.settings.shift_click_delay_seconds:
                    time.sleep(self.settings.shift_click_delay_seconds)
        finally:
            key.keyUp("shift")
        print("* Shift-click 農田位置完成 *")
        return True

    def run_farm_step_with_retry(
        self, description, operation, stop_event=None, pause_event=None
    ):
        """Run one farm step using the configured retry count."""
        for attempt in range(self.settings.step_retries):
            print(
                f"* 農場步驟開始: {description} "
                f"（第 {attempt + 1}/{self.settings.step_retries} 次）*"
            )
            if not self.clear_expired_prompt():
                continue
            if operation():
                print(f"* 農場步驟成功: {description} *")
                return True
            if (stop_event is not None and stop_event.is_set()) or (
                pause_event is not None and pause_event.is_set()
            ):
                print(f"* 農場步驟已取消: {description} *")
                return False
            if attempt + 1 < self.settings.step_retries:
                print(f"* 農場步驟失敗，重試一次: {description} *")
        print(f"* 農場步驟重試後仍失敗，結束農場流程: {description} *")
        return False

    def create_farm_workflow(self, stop_event=None, pause_event=None):
        """Build the farm sequence with this instance's screen automation hooks."""
        return FarmWorkflow(
            config=self.settings.workflow,
            run_step=lambda description, operation: self.run_farm_step_with_retry(
                description,
                operation,
                stop_event=stop_event,
                pause_event=pause_event,
            ),
            start_flow=lambda: self.click_initial_farm_button(stop_event, pause_event),
            click_image=lambda image_name: self.wait_for_farm_image_then_click(
                image_name,
                stop_event=stop_event,
                pause_event=pause_event,
            ),
            click_any_image=lambda image_names: self.wait_for_any_farm_image_then_click(
                image_names,
                stop_event=stop_event,
                pause_event=pause_event,
            ),
            is_image_visible=self.is_farm_image_visible,
            wait_for_crop_result=lambda image_names: self.wait_for_any_farm_image_name(
                image_names,
                stop_event=stop_event,
                pause_event=pause_event,
            ),
            load_click_positions=self.load_farm_click_positions,
            wait_before_target_field=lambda: (
                time.sleep(self.settings.target_field_delay_seconds) or True
            ),
            wait_after_crop_collection=lambda: (
                time.sleep(self.settings.crop_confirmation_delay_seconds) or True
            ),
            wait_between_purchase_steps=lambda: (
                time.sleep(self.settings.purchase_step_delay_seconds) or True
            ),
            shift_click_positions=lambda positions: self.hold_shift_and_click_positions(
                positions,
                stop_event=stop_event,
                pause_event=pause_event,
            ),
            on_crop_management_skipped=lambda: print(
                "* 農作物管理失敗，跳過收集並開始鋪設農田 *"
            ),
            on_crop_failure_detected=lambda: print(
                "* 農作物管理失敗流程：將點擊一般 okButton.png *"
            ),
        )

    def clear_prompts_until_unblocked(self, stop_event=None, pause_event=None):
        """Clear post-farm blockers until clearPrompt finds none remaining."""
        print("* 農場流程逾時，開始清除阻塞提示 *")
        while True:
            if (stop_event is not None and stop_event.is_set()) or (
                pause_event is not None and pause_event.is_set()
            ):
                print("* 清除農場阻塞提示已取消 *")
                return False
            if not self.clear_expired_prompt():
                return False
            if not self.clear_prompt():
                print("* 農場阻塞提示已清除 *")
                return True
            if self.settings.poll_interval_seconds:
                time.sleep(self.settings.poll_interval_seconds)
