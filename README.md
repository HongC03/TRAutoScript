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

## MCJ Macro Studio

Run this from the project root with the virtual environment active:

```powershell
python -m event_script.afk_mcj --target-image your_task.png
```

1. Click **Record**, switch to the game during the three-second countdown, then perform the key sequence. Press and release **F8** to finish.
2. Inspect the key down/up timeline. Use **Dry run** to check the replay timing in the debug log without sending keys.
3. Use **Replay in game** and switch to Tales Runner during the countdown to try the sequence once. Live replay stops if Tales Runner loses focus.
4. Click **Arm automation** to run the shared AFK workflow. It plays the macro once on the first successful task action, then waits for an unsuccessful check before it can fire again. Press **F8** or click **Stop** to cancel.

Use **Save** and **Open** to reuse a recording, or pass `--macro path\to\recording.json` at launch. The recorder captures supported keyboard keys, including arrows, and lists skipped keys in the debug log. MCJ uses the same workflow options as Three Knight and stores its statistics in `mcj_stats.json` by default.
