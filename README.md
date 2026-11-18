# TRAutoScript

Standalone Tales Runner automation project extracted from TRAuto. It contains the Up and X AFK automation (`afk_three_knight`), its shared task and Telegram engine, game supervision, Three Knight coupon and farm workflows, and the image templates those flows use.

## Setup

Use Windows with Python 3.10 or newer. From this folder, create and activate a virtual environment, then install dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
```

Optional Telegram control uses `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Copy `.env.example` to `.env` and fill in your own values. Without these values the automation still runs, but Telegram commands are disabled. Do not commit `.env`.

## Run

From the project root:

```powershell
py -m event_script.afk_three_knight
```

The default task trigger is `scr/magic_map.png`, the match-end template is `scr/conan_end.png`, and the Three Knight coupon workflow runs at startup and then becomes eligible again after 30 minutes, at the next match end. To use a different trigger image:

```powershell
py -m event_script.afk_three_knight --target-image your_task.png
```

Images are resolved relative to `scr/`; absolute paths are also accepted. Use `--workflow farm` to run the scheduled farm flow instead of the default coupon redemption flow. Press F3 globally to turn automation off or start it again; use `--toggle-hotkey` to choose another key. Run `py -m event_script.afk_three_knight --help` to see all options.

To use a different Three Knight point coupon template, set `--three-knight-point-image` to a filename under `scr/` or an absolute image path:

```powershell
py -m event_script.afk_three_knight --three-knight-point-image three_knight_point_alt.png
```

Set the repeat interval in minutes with `--three-knight-coupon-interval-minutes` (for example, `--three-knight-coupon-interval-minutes 5` for five minutes). Use `0` to disable repeats.

The game must be open and the Windows desktop session unlocked. `game_supervisor.py` can also launch/restart Tales Runner when configured with credentials in a local `autoPWD.txt` containing `account=...` and `password=...`; keep that file private. The launcher shortcut is expected at `C:\Users\Public\Desktop\TalesRunner.lnk`.

Run, farm, and coupon workflow statistics are stored together in `three_knight_coupon_stats.json`. The `last_coupon_workflow_completed_at` field records the last successful coupon workflow; use `--stats-path` to change the file location. Farm click positions are in `farm_click_positions.json` and can be adjusted for your game window layout.

## MCJ dance automation

MCJ uses the Three Knight task trigger, game supervision, match tracking, and F3 toggle. It reads the instructor's dance moves and sends the matching arrow keys. Farm and coupon runs are disabled.

```powershell
python -m event_script.afk_mcj --debug
```

MCJ looks for the “和MCJ一起跳舞！” card using `scr/mcj_dance.png`. Use `--target-image` to override it. Use `--dry-run --debug` to inspect the detected poses without sending arrows. The shared task flow still runs in this mode. See **Arrow Dance** below to test the detector against a recording. MCJ stores its statistics in `mcj_stats.json` by default.

## Arrow Dance

Preview the detector on a recording:

```powershell
python -m event_script.afk_arrow_dance --video "C:\path\to\recording.mp4" --debug
```

The detector automatically recognizes the orange, blue, and pink instructors. The preview shows the timer and instructor regions, detected character and pose, and arrows recognized in the current round. Press **Space** to pause or resume, **N** to step while paused, and **Q** to quit. Omit `--debug` to scan the recording as fast as possible and print the recognition times.

For live play, launch the command below and focus Tales Runner before the next round. The script taps the matching arrow as soon as it recognizes each new pose. If the active round has no recognized pose after 0.85 seconds, it taps one random arrow and marks it as a guess. Press **F8** to stop.

```powershell
python -m event_script.afk_arrow_dance --play --debug
```

Omit `--play` to test live detection without sending keys. The detector is calibrated to the recording's framing. If the debug boxes are misaligned, use `--crop X Y WIDTH HEIGHT` in source pixels to trim the captured window or video to match the recording. Consecutive identical poses require a visible break so the script can count them separately.
