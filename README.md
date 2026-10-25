# TRAutoScript

Standalone Tales Runner automation project extracted from TRAuto. It contains the Up/Right AFK automation (`afk_three_knight`), its shared task and Telegram engine, game supervision, Three Knight coupon and farm workflows, and the image templates those flows use.

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

The default task trigger is `scr/magic_map.png`, the match-end template is `scr/conan_end.png`, and the Three Knight coupon workflow runs at startup and then every 30 minutes after a match ends. To use a different trigger image:

```powershell
py -m event_script.afk_three_knight --target-image your_task.png
```

Images are resolved relative to `scr/`; absolute paths are also accepted. Use `--workflow farm` to run the scheduled farm flow instead of the default coupon redemption flow. Add `--status-window` for the desktop toggle and dashboard. Run `py -m event_script.afk_three_knight --help` to see all options.

The game must be open and the Windows desktop session unlocked. `game_supervisor.py` can also launch/restart Tales Runner when configured with credentials in a local `autoPWD.txt` containing `account=...` and `password=...`; keep that file private. The launcher shortcut is expected at `C:\Users\Public\Desktop\TalesRunner.lnk`.

Run, farm, and coupon workflow statistics are stored together in `three_knight_coupon_stats.json`. The `last_coupon_workflow_completed_at` field records the last successful coupon workflow; use `--stats-path` to change the file location. Farm click positions are in `farm_click_positions.json` and can be adjusted for your game window layout.
