"""Navigate to My Room and redeem the Three Knight 3x point coupon."""

import math
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from event_script.farm_automation import FarmAutomation, FarmAutomationSettings
from PIL import Image
from utils import locate_on_screen, pressButton


BASE_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ThreeKnightCouponWorkflowSettings:
    """Image templates and polling settings for the Three Knight coupon workflow."""

    image_dir: Path = BASE_DIR / "scr"
    my_room_image: str = "my_room.png"
    my_favourite_image: str = "my_favourite.png"
    three_knight_point_image: str = "three_knight_point.png"
    use_image: str = "use.png"
    yes_image: str = "yes.png"
    ok_button_image: str = "okButton.png"
    return_image_names: tuple[str, ...] = (
        "waiting_room.png",
        "special_event.png",
    )
    match_confidence: float = 0.89
    small_template_confidence: float = 0.78
    small_template_scales: tuple[float, ...] = (0.75, 0.875, 1.125, 1.25, 1.5, 1.75, 2.0)
    small_template_image_names: tuple[str, ...] = (
        "my_favourite.png", "three_knight_point.png", "use.png",
    )
    scaled_template_image_names: tuple[str, ...] = (
        "my_room.png", "my_favourite.png", "three_knight_point.png",
        "use.png", "yes.png", "okButton.png", "waiting_room.png",
        "special_event.png",
    )
    workflow_timeout_seconds: float = 60
    image_timeout_seconds: float = 20
    poll_interval_seconds: float = 0.5
    click_delay_seconds: float = 1.0


class ThreeKnightCouponWorkflow:
    """Click the configured room/favourite controls and return to the event."""

    def __init__(self, settings=None, *, image_locator=None, click=None):
        self.settings = settings or ThreeKnightCouponWorkflowSettings()
        self.image_locator = image_locator or locate_on_screen
        self.click = click or pressButton
        self._template_cache = {}
        self._temporary_templates = tempfile.TemporaryDirectory(
            prefix="trauto-three-knight-coupon-"
        )
        self._recovery = FarmAutomation(FarmAutomationSettings(
            image_dir=self.settings.image_dir,
            match_confidence=self.settings.match_confidence,
            image_timeout_seconds=self.settings.image_timeout_seconds,
            poll_interval_seconds=self.settings.poll_interval_seconds,
        ))

    def _image_path(self, image_name):
        return self.settings.image_dir / image_name

    def _template_variants(self, image_name, image_path):
        if image_name not in self.settings.scaled_template_image_names:
            return (image_path,)
        if image_name in self._template_cache:
            return self._template_cache[image_name]

        variants = [image_path]
        output_dir = Path(self._temporary_templates.name)
        with Image.open(image_path) as template:
            for scale in self.settings.small_template_scales:
                width = max(1, round(template.width * scale))
                height = max(1, round(template.height * scale))
                resized = template.resize(
                    (width, height), Image.Resampling.LANCZOS,
                )
                scaled_path = output_dir / (
                    f"{Path(image_name).stem}_{scale:g}.png"
                )
                resized.save(scaled_path)
                variants.append(scaled_path)

        self._template_cache[image_name] = tuple(variants)
        return self._template_cache[image_name]

    @staticmethod
    def _cancelled(stop_event, pause_event):
        return (
            (stop_event is not None and stop_event.is_set())
            or (pause_event is not None and pause_event.is_set())
        )

    def _wait_after_click(self, *, stop_event=None, pause_event=None):
        """Give the game time to react after a click, while honoring cancellation."""
        deadline = time.monotonic() + max(0.0, self.settings.click_delay_seconds)
        while not self._cancelled(stop_event, pause_event):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(self.settings.poll_interval_seconds, remaining))
        return False

    def _wait_for_image(
        self, image_name, *, stop_event=None, pause_event=None, region=None,
        deadline=None,
    ):
        image_path = self._image_path(image_name)
        if not image_path.is_file():
            print(
                f"* Up/Right workflow image not found: {image_name} "
                f"(template missing: {image_path}) *"
            )
            return None

        if deadline is None:
            deadline = time.monotonic() + self.settings.image_timeout_seconds
        small_template = image_name in self.settings.small_template_image_names
        confidence = (
            self.settings.small_template_confidence
            if small_template else self.settings.match_confidence
        )
        template_paths = self._template_variants(image_name, image_path)
        while not self._cancelled(stop_event, pause_event):
            for template_path in template_paths:
                position = self.image_locator(
                    template_path,
                    confidence=confidence,
                    region=region,
                )
                if position is not None:
                    match_size = (
                        "resized" if template_path != image_path else "original-size"
                    )
                    print(
                        f"* Up/Right workflow image found: {image_name} "
                        f"(match={match_size}, confidence={confidence:.2f}) *"
                    )
                    return position
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print(
                    f"* Up/Right workflow image not found before timeout: {image_name} "
                    f"(confidence={confidence:.2f}) *"
                )
                return None
            time.sleep(min(self.settings.poll_interval_seconds, remaining))
        return None

    def _click_image(
        self, image_name, *, stop_event=None, pause_event=None, deadline=None,
    ):
        position = self._wait_for_image(
            image_name, stop_event=stop_event, pause_event=pause_event,
            deadline=deadline,
        )
        if position is None:
            return False
        if self._cancelled(stop_event, pause_event):
            return False
        self.click(position)
        print(f"* Up/Right workflow clicked {image_name} *")
        return self._wait_after_click(
            stop_event=stop_event, pause_event=pause_event,
        )

    def _click_use_inside_three_knight_point(
        self, *, stop_event=None, pause_event=None, deadline=None,
    ):
        outer = self._wait_for_image(
            self.settings.three_knight_point_image,
            stop_event=stop_event,
            pause_event=pause_event,
            deadline=deadline,
        )
        if outer is None:
            return False

        left, top, width, height = outer
        region = (left, top, width, height)
        use_position = self._wait_for_image(
            self.settings.use_image,
            stop_event=stop_event,
            pause_event=pause_event,
            region=region,
            deadline=deadline,
        )
        if use_position is None:
            return False
        if self._cancelled(stop_event, pause_event):
            return False
        self.click(use_position)
        print(
            f"* Up/Right workflow clicked {self.settings.use_image} "
            f"inside {self.settings.three_knight_point_image} *"
        )
        return self._wait_after_click(
            stop_event=stop_event, pause_event=pause_event,
        )

    def _return_sequence(self):
        """Include the confirmation shown after returning to the waiting room."""
        sequence = []
        for image_name in self.settings.return_image_names:
            sequence.append(image_name)
            if image_name == "waiting_room.png":
                sequence.append(self.settings.yes_image)
        return tuple(sequence)

    def _return_to_event(self, *, stop_event=None, pause_event=None):
        """Recover prompts and retry return clicks like the farm workflow."""
        print("* Returning to waiting room after incomplete Up/Right workflow *")
        if not self._recovery.clear_prompts_until_unblocked(
            stop_event=stop_event,
            pause_event=pause_event,
        ):
            return False

        returned = True
        for image_name in self._return_sequence():
            if not self._recovery.run_farm_step_with_retry(
                f"return {image_name}",
                lambda image_name=image_name: self._click_image(
                    image_name,
                    stop_event=stop_event,
                    pause_event=pause_event,
                ),
                stop_event=stop_event,
                pause_event=pause_event,
            ):
                returned = False
                break
        if returned:
            print("* Returned to special event; continuing normal workflow *")
        else:
            print("* Recovery clicks were incomplete; continuing normal workflow *")
        # Recovery was attempted, so the scheduler can proceed to the next cycle.
        return True

    def run(self, *, stop_event=None, pause_event=None):
        """Run the room sequence once; return False on failure or cancellation."""
        print("* Starting Three Knight coupon workflow *")
        deadline = time.monotonic() + self.settings.workflow_timeout_seconds
        sequence = (
            self.settings.my_room_image,
            self.settings.my_favourite_image,
        )
        for image_name in sequence:
            if not self._click_image(
                image_name, stop_event=stop_event, pause_event=pause_event,
                deadline=deadline,
            ):
                if self._cancelled(stop_event, pause_event):
                    return False
                print("* Three Knight coupon workflow did not complete within its time limit *")
                return self._return_to_event(
                    stop_event=stop_event, pause_event=pause_event,
                )

        if not self._click_use_inside_three_knight_point(
            stop_event=stop_event, pause_event=pause_event, deadline=deadline,
        ):
            if self._cancelled(stop_event, pause_event):
                return False
            print("* Three Knight coupon workflow did not complete within its time limit *")
            return self._return_to_event(
                stop_event=stop_event, pause_event=pause_event,
            )

        if not self._click_image(
            self.settings.yes_image,
            stop_event=stop_event,
            pause_event=pause_event,
            deadline=deadline,
        ):
            if self._cancelled(stop_event, pause_event):
                return False
            print("* Three Knight coupon workflow did not complete within its time limit *")
            return self._return_to_event(
                stop_event=stop_event, pause_event=pause_event,
            )

        if not self._click_image(
            self.settings.ok_button_image,
            stop_event=stop_event,
            pause_event=pause_event,
            deadline=deadline,
        ):
            if self._cancelled(stop_event, pause_event):
                return False
            print("* Three Knight coupon workflow did not complete within its time limit *")
            return self._return_to_event(
                stop_event=stop_event, pause_event=pause_event,
            )

        for image_name in self._return_sequence():
            if not self._click_image(
                image_name, stop_event=stop_event, pause_event=pause_event,
                deadline=deadline,
            ):
                if self._cancelled(stop_event, pause_event):
                    return False
                print("* Three Knight coupon workflow did not complete within its time limit *")
                return self._return_to_event(
                    stop_event=stop_event, pause_event=pause_event,
                )
        print("* Three Knight coupon workflow completed *")
        return True


class IntervalMatchEndStage:
    """Queue an independent workflow by interval and run it at match end."""

    def __init__(
        self, interval_seconds, workflow, *,
        run_on_start=False, clock=time.monotonic, wall_clock=time.time,
    ):
        if interval_seconds < 0 or (interval_seconds == 0 and not run_on_start):
            raise ValueError(
                "interval_seconds must be greater than zero unless run_on_start is enabled"
            )
        self.interval_seconds = interval_seconds
        self.workflow = workflow
        self.clock = clock
        self.wall_clock = wall_clock
        self.run_on_start = run_on_start
        self.next_due_at = None
        self.last_completed_at = None
        self._save_last_completed_at = None
        self._save_next_due_at = None
        self.pending = False
        self.startup_pending = False

    def initialize_schedule(
        self, next_due_at, save_next_due_at, *,
        last_completed_at=None, save_last_completed_at=None,
    ):
        """Restore saved timestamps and derive a due time when needed."""
        self._save_next_due_at = save_next_due_at
        self._save_last_completed_at = save_last_completed_at
        now = self.wall_clock()
        try:
            next_due_at = None if next_due_at is None else float(next_due_at)
        except (TypeError, ValueError):
            next_due_at = None
        try:
            last_completed_at = (
                None if last_completed_at is None else float(last_completed_at)
            )
        except (TypeError, ValueError):
            last_completed_at = None
        if next_due_at is not None and not math.isfinite(next_due_at):
            next_due_at = None
        if last_completed_at is not None and not math.isfinite(last_completed_at):
            last_completed_at = None
        self.last_completed_at = last_completed_at
        first_run = next_due_at is None and last_completed_at is None

        if self.interval_seconds == 0:
            next_due_at = None
            self._save_next_due_at(None)
        elif next_due_at is None:
            if last_completed_at is None:
                next_due_at = now + self.interval_seconds
            else:
                next_due_at = last_completed_at + self.interval_seconds
            self._save_next_due_at(next_due_at)
        remaining = (
            0.0 if next_due_at is None else max(0.0, next_due_at - now)
        )
        self.next_due_at = (
            None if next_due_at is None else self.clock() + remaining
        )
        schedule_due = next_due_at is not None and next_due_at <= now
        self.startup_pending = self.run_on_start and (first_run or schedule_due)
        self.pending = (
            schedule_due or self.startup_pending
        )
        if self.startup_pending:
            print("* Three Knight coupon workflow queued to run at startup *")
        elif self.pending:
            print("* Three Knight coupon workflow schedule is overdue *")
        else:
            print(
                f"* Three Knight coupon workflow next due in {remaining:.0f} seconds *"
            )

    def update(self):
        """Queue the workflow once its interval has elapsed."""
        if (
            self.interval_seconds > 0
            and not self.pending
            and self.next_due_at is not None
            and self.clock() >= self.next_due_at
        ):
            self.pending = True
            print("* Three Knight coupon workflow is due; waiting for match end *")
        return self.pending

    def run_if_pending(self, *, stop_event=None, pause_event=None):
        """Run after match end; keep it pending if it fails or is cancelled."""
        if not self.pending:
            return False
        print("* Running due Three Knight coupon workflow after match end *")
        return self._run_workflow(stop_event=stop_event, pause_event=pause_event)

    def run_on_start_if_pending(self, *, stop_event=None, pause_event=None):
        """Run once immediately when the game is ready at script startup."""
        if not self.startup_pending:
            return False
        print("* Running Three Knight coupon workflow at startup *")
        return self._run_workflow(stop_event=stop_event, pause_event=pause_event)

    def _run_workflow(self, *, stop_event=None, pause_event=None):
        completed = self.workflow.run(
            stop_event=stop_event,
            pause_event=pause_event,
        )
        self.startup_pending = False
        if completed:
            self.pending = False
            completed_at = self.wall_clock()
            self.last_completed_at = completed_at
            next_due_at = (
                None if self.interval_seconds == 0
                else completed_at + self.interval_seconds
            )
            self.next_due_at = (
                None if next_due_at is None
                else self.clock() + self.interval_seconds
            )
            if self._save_last_completed_at is not None:
                self._save_last_completed_at(completed_at)
            if self._save_next_due_at is not None:
                self._save_next_due_at(next_due_at)
        return completed
