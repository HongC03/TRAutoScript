"""Reusable, UI-agnostic Tales Runner farm workflow orchestration."""

from dataclasses import dataclass
from functools import partial
from typing import Callable, Sequence


StepOperation = Callable[[], bool]
RunStep = Callable[[str, StepOperation], bool]
ClickImage = Callable[[str], bool]
ClickAnyImage = Callable[[Sequence[str]], bool]
IsImageVisible = Callable[[str], bool]
LoadClickPositions = Callable[[], object]
ShiftClickPositions = Callable[[object], bool]
WaitBeforeTargetField = Callable[[], bool]
WaitAfterCropCollection = Callable[[], bool]
WaitBetweenPurchaseSteps = Callable[[], bool]
CropManagementSkipped = Callable[[], None]
CropFailureDetected = Callable[[], None]
WaitForCropResult = Callable[[Sequence[str]], str | None]


@dataclass(frozen=True)
class FarmWorkflowConfig:
    """Image sequence and crop rules for one farm workflow variant."""

    initial_image: str
    entry_image_names: Sequence[str]
    crops_start_image: str
    crops_collection_image_names: Sequence[str]
    crops_failure_image: str
    crops_confirmation_image: str
    crops_failure_confirmation_image: str
    cross_image_names: Sequence[str]
    # The final field image is the target-field button and is handled after
    # the configurable pre-target delay.
    field_image_names: Sequence[str]
    purchase_image_names: Sequence[str]
    return_image_names: Sequence[str]
    crop_attempts: int = 2


class FarmWorkflow:
    """Run a farm flow using caller-provided screen automation operations.

    The class has no dependency on Conan, PyAutoGUI, or a particular retry
    policy. This lets other scripts reuse the same flow with their own image
    drivers, cancellation handling, and status reporting.
    """

    def __init__(
        self,
        config: FarmWorkflowConfig,
        run_step: RunStep,
        click_image: ClickImage,
        click_any_image: ClickAnyImage,
        is_image_visible: IsImageVisible,
        load_click_positions: LoadClickPositions,
        shift_click_positions: ShiftClickPositions,
        start_flow: StepOperation | None = None,
        wait_before_target_field: WaitBeforeTargetField | None = None,
        wait_after_crop_collection: WaitAfterCropCollection | None = None,
        wait_between_purchase_steps: WaitBetweenPurchaseSteps | None = None,
        on_crop_management_skipped: CropManagementSkipped | None = None,
        on_crop_failure_detected: CropFailureDetected | None = None,
        wait_for_crop_result: WaitForCropResult | None = None,
    ):
        self.config = config
        self._run_step = run_step
        self._click_image = click_image
        self._click_any_image = click_any_image
        self._is_image_visible = is_image_visible
        self._load_click_positions = load_click_positions
        self._shift_click_positions = shift_click_positions
        self._start_flow = start_flow or (
            lambda: self._click_image(self.config.initial_image)
        )
        self._wait_before_target_field = wait_before_target_field or (
            lambda: True
        )
        self._wait_after_crop_collection = wait_after_crop_collection or (
            lambda: True
        )
        self._wait_between_purchase_steps = wait_between_purchase_steps or (
            lambda: True
        )
        self._on_crop_management_skipped = on_crop_management_skipped
        self._on_crop_failure_detected = on_crop_failure_detected
        self._wait_for_crop_result = wait_for_crop_result

    def _run_image_sequence(
        self,
        image_names: Sequence[str],
        wait_between_steps: WaitBetweenPurchaseSteps | None = None,
    ) -> bool:
        image_names = tuple(image_names)
        for index, image_name in enumerate(image_names):
            if not self._run_step(
                image_name,
                partial(self._click_image, image_name),
            ):
                return False
            if wait_between_steps is not None and index < len(image_names) - 1:
                if not wait_between_steps():
                    return False
        return True

    def _skip_crop_management(self):
        """Report a non-fatal crop-management failure before field placement."""
        if self._on_crop_management_skipped is not None:
            self._on_crop_management_skipped()

    def run(self) -> bool:
        """Run the full farm flow and return whether every required step worked."""
        if not self._run_step(self.config.initial_image, self._start_flow):
            return False
        if not self._run_image_sequence(self.config.entry_image_names):
            return False
        crop_management_opened = self._run_step(
            self.config.crops_start_image,
            lambda: self._click_image(self.config.crops_start_image),
        )
        if crop_management_opened:
            crop_collection_failed = False
            crop_failure_detected = False
            for _ in range(self.config.crop_attempts):
                if self._is_image_visible(self.config.crops_failure_image):
                    crop_failure_detected = True
                    if self._on_crop_failure_detected is not None:
                        self._on_crop_failure_detected()
                    break
                if not self._run_image_sequence(
                    self.config.crops_collection_image_names
                ):
                    crop_collection_failed = True
                    self._skip_crop_management()
                    break
                # Each normal collection attempt has its own five-second wait
                # and farm-specific confirmation before the next attempt.
                if not self._wait_after_crop_collection():
                    crop_collection_failed = True
                    self._skip_crop_management()
                    break
                if self._wait_for_crop_result is None:
                    if not self._run_step(
                        self.config.crops_confirmation_image,
                        lambda: self._click_image(
                            self.config.crops_confirmation_image
                        ),
                    ):
                        crop_collection_failed = True
                        self._skip_crop_management()
                        break
                else:
                    crop_result = None

                    def run_crop_result():
                        nonlocal crop_result
                        crop_result = self._wait_for_crop_result(
                            (
                                self.config.crops_failure_image,
                                self.config.crops_confirmation_image,
                            )
                        )
                        if crop_result == self.config.crops_failure_image:
                            return True
                        if crop_result != self.config.crops_confirmation_image:
                            return False
                        return self._click_image(
                            self.config.crops_confirmation_image
                        )

                    if not self._run_step("crop result", run_crop_result):
                        crop_collection_failed = True
                        self._skip_crop_management()
                        break
                    if crop_result == self.config.crops_failure_image:
                        crop_failure_detected = True
                        if self._on_crop_failure_detected is not None:
                            self._on_crop_failure_detected()
                        break
            if crop_failure_detected and not self._run_step(
                self.config.crops_failure_confirmation_image,
                lambda: self._click_image(
                    self.config.crops_failure_confirmation_image
                ),
            ):
                self._skip_crop_management()
        else:
            self._skip_crop_management()

        # If crop management was opened, close that screen before entering the
        # shop. If it never opened, farm_shop is already the next target.
        if crop_management_opened and not self._run_step(
            "farm cross",
            lambda: self._click_any_image(self.config.cross_image_names),
        ):
            return False
        field_image_names = tuple(self.config.field_image_names)
        if not field_image_names:
            return False
        if not self._run_image_sequence(field_image_names[:-1]):
            return False

        # The target-field button must be detected and clicked before loading
        # and executing the recorded JSON positions.
        if not self._wait_before_target_field():
            return False
        target_field_image = field_image_names[-1]
        if not self._run_step(
            target_field_image,
            partial(self._click_image, target_field_image),
        ):
            return False

        positions = self._load_click_positions()
        if not self._run_step(
            "Shift-click positions",
            lambda: self._shift_click_positions(positions),
        ):
            return False

        if not self._run_image_sequence(
            self.config.purchase_image_names,
            wait_between_steps=self._wait_between_purchase_steps,
        ):
            return False
        if not self._run_step(
            "farm cross",
            lambda: self._click_any_image(self.config.cross_image_names),
        ):
            return False
        return self.run_return_sequence()

    def run_return_sequence(self) -> bool:
        """Leave the farm and return to the normal Conan flow."""
        return self._run_image_sequence(self.config.return_image_names)
