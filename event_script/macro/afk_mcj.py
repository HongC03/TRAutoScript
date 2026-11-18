"""Launch the MCJ keyboard macro studio from the repository root."""

import sys

from event_script import afk_conan
from event_script.afk_three_knight import parse_args, run_task_flow

from .keyboard_macro import play_macro
from .macro_studio import MacroStudio


def game_focus_check():
    window = afk_conan.get_active_window()
    if not afk_conan.is_game_window(window):
        raise RuntimeError("Bring Tales Runner to the foreground before replay.")
    return lambda: afk_conan.get_active_window() == window


def run_mcj(arguments, events, stop_event, log):
    triggered_last_check = False

    def reset_trigger():
        nonlocal triggered_last_check
        triggered_last_check = False

    def on_task_result(triggered, pause_event, run_stop_event):
        nonlocal triggered_last_check
        new_trigger = triggered and not triggered_last_check
        triggered_last_check = triggered

        def cancelled():
            return pause_event.is_set() or run_stop_event.is_set()

        if new_trigger and not cancelled():
            log("Task action triggered. Playing macro once.")
            try:
                play_macro(events, cancelled=cancelled, log=log, has_focus=game_focus_check())
            except Exception as error:
                log(f"Replay failed: {error}")

    def focus_game(window):
        if not afk_conan.is_game_window(afk_conan.get_active_window()):
            afk_conan.frontWindow(window)
        return afk_conan.is_game_window(afk_conan.get_active_window())

    log(f"Waiting for task trigger: {arguments.target_image}")
    return run_task_flow(
        arguments,
        stop_event=stop_event,
        automatic_shift=False,
        on_task_result=on_task_result,
        release_input=reset_trigger,
        focus_game_window=focus_game,
    )


def main(argv=None):
    arguments = parse_args(argv, macro_mode=True)
    if arguments.test_farm_workflow:
        return run_task_flow(arguments)
    studio = MacroStudio(
        target_image=arguments.target_image,
        focus_check=game_focus_check,
        run_automation=lambda events, stop, log: run_mcj(arguments, events, stop, log),
    )
    if arguments.macro:
        studio.open_macro(arguments.macro)
    studio.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
