"""Run the Three Knight task flow with the MCJ arrow dance player."""

import argparse
import sys
from contextlib import nullcontext
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from event_script import afk_arrow_dance
from event_script.automation_toggle import run_with_toggle


TARGET_IMAGE_NAME = "mcj_dance.png"


class DanceStop:
    """Stop on task shutdown, Telegram pause, or automation shutdown."""

    def __init__(self, input_stop, pause, run_stop):
        self.input_stop = input_stop
        self.pause = pause
        self.run_stop = run_stop

    def is_set(self):
        return self.input_stop.is_set() or self.pause.is_set() or self.run_stop.is_set()

    def wait(self, seconds):
        if not self.is_set():
            self.input_stop.wait(seconds)
        return self.is_set()

    def set(self):
        self.run_stop.set()


def run_automation(arguments, stop_event):
    from event_script import afk_conan
    from event_script.afk_three_knight import run_task_flow

    worker_error = None

    def dance_input(input_stop, pause, run_stop):
        nonlocal worker_error
        try:
            afk_arrow_dance.run(
                play=not arguments.dry_run, debug=arguments.debug, crop=arguments.crop,
                stop=DanceStop(input_stop, pause, run_stop), hotkey=arguments.toggle_hotkey,
            )
        except Exception as error:
            worker_error = error
            run_stop.set()

    def focus_game(window):
        if not afk_conan.is_game_window(afk_conan.get_active_window()):
            afk_conan.frontWindow(window)
        return afk_conan.is_game_window(afk_conan.get_active_window())

    result = run_task_flow(
        arguments, stop_event=stop_event, workflows_enabled=False,
        input_worker=dance_input, focus_game_window=focus_game,
    )
    if worker_error is not None:
        raise worker_error
    return result


def main(argv=None):
    afk_arrow_dance.enable_dpi_awareness()
    from event_script.afk_conan import BASE_DIR
    from event_script.afk_three_knight import TOGGLE_HOTKEY, add_task_arguments

    parser = argparse.ArgumentParser(description=__doc__)
    add_task_arguments(
        parser, target_image=TARGET_IMAGE_NAME, stats_path=BASE_DIR / "mcj_stats.json",
    )
    afk_arrow_dance.add_capture_options(parser)
    parser.add_argument("--dry-run", action="store_true", help="Log dance answers without playing them.")
    parser.add_argument("--record", action="store_true",
                        help="Save a 60 FPS Tales Runner MP4 in Videos/Screen Recordings until MCJ exits.")
    parser.add_argument("--toggle-hotkey", default=TOGGLE_HOTKEY,
                        help="Global on/off hotkey (default: %(default)s).")
    arguments = parser.parse_args(argv)
    if not arguments.target_image:
        parser.error("set --target-image")
    if not arguments.toggle_hotkey.strip():
        parser.error("--toggle-hotkey must not be empty")
    recording = nullcontext()
    if arguments.record:
        from event_script.window_recording import record_game_window

        recording = record_game_window()
    try:
        with recording:
            return run_with_toggle(
                lambda stop_event: run_automation(arguments, stop_event),
                toggle_hotkey=arguments.toggle_hotkey,
            )
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
