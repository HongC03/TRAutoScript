#!/usr/bin/env python3
# How to use on Windows:
#   1. Open Command Prompt.
#   2. Run this script with an interval in seconds, for example:
#        py "%USERPROFILE%\Desktop\TRautoRace\event_script\alarm_window.py" --interval 600
#   3. To align alarms to a Hong Kong start time, add --start-time HH:MM:
#        py "%USERPROFILE%\Desktop\TRautoRace\event_script\alarm_window.py" --start-time 09:00 --interval 600
#   4. Press Dismiss to close the current alarm, or Stop to exit the program.
#      Escape also exits the program while an alarm window is focused.
#   5. Optional Telegram notifications require these environment variables:
#        TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
#      Enable them with --telegram-notification. Use
#      --no-desktop-notification for Telegram-only operation.
#
"""Show a recurring desktop alarm window at a configurable interval.

Examples:
    python alarm_window.py --interval 60
    py alarm_window.py --start-time 09:00 --interval 600
    py alarm_window.py --start-time 09:00 --interval 7200 --message "Take a break"
"""

import argparse
import json
import math
import os
import signal
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


HKT = timezone(timedelta(hours=8), name="HKT")

# Defaults used when running without command-line flags, such as from PyCharm.
# Command-line flags override these values.
DEFAULT_DESKTOP_NOTIFICATION = False
DEFAULT_TELEGRAM_NOTIFICATION = True


def load_dotenv_file():
    """Load simple KEY=VALUE settings from a local .env file if present."""
    dotenv_path = Path(__file__).with_name(".env")
    if not dotenv_path.exists():
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
        if key:
            os.environ.setdefault(key, value)


def telegram_is_configured():
    return bool(
        os.environ.get("TELEGRAM_BOT_TOKEN")
        and os.environ.get("TELEGRAM_CHAT_ID")
    )


def send_telegram_notification(title, message):
    """Send an optional Telegram notification without exposing credentials."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return False

    data = urlencode({
        "chat_id": chat_id,
        "text": f"{title}\n{message}",
    }).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=data,
        method="POST",
    )

    try:
        with urlopen(request, timeout=10) as response:
            result = json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        print(f"Telegram notification failed: {error}", file=sys.stderr)
        return False

    if not result.get("ok"):
        print(f"Telegram notification failed: {result}", file=sys.stderr)
        return False
    return True


class AlarmApp:
    def __init__(
        self,
        interval_seconds,
        title,
        message,
        start_time=None,
        desktop_notification=True,
        telegram_notification=False,
    ):
        self.interval_seconds = interval_seconds
        self.interval_ms = max(1, round(interval_seconds * 1000))
        self.title = title
        self.message = message
        self.start_time = start_time
        self.desktop_notification = desktop_notification
        self.telegram_notification = telegram_notification
        self.root = tk.Tk()
        self.root.withdraw()
        self.previous_sigint_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self.handle_sigint)
        self.alarm_window = None
        self.next_alarm_id = None
        self.next_alarm_at = self.calculate_next_alarm()
        self.root.protocol("WM_DELETE_WINDOW", self.stop)
        self.schedule_next_alarm()

    def calculate_next_alarm(self):
        if self.start_time is None:
            return None

        now = datetime.now(HKT)
        start_today = datetime.combine(now.date(), self.start_time, tzinfo=HKT)
        if now <= start_today:
            return start_today

        elapsed_seconds = (now - start_today).total_seconds()
        elapsed_intervals = math.ceil(elapsed_seconds / self.interval_seconds)
        return start_today + timedelta(
            seconds=elapsed_intervals * self.interval_seconds
        )

    def schedule_next_alarm(self):
        if self.start_time is None:
            delay_ms = self.interval_ms
        else:
            delay_seconds = max(
                0, (self.next_alarm_at - datetime.now(HKT)).total_seconds()
            )
            delay_ms = max(1, round(delay_seconds * 1000))

        self.next_alarm_id = self.root.after(delay_ms, self.show_alarm)

    def advance_next_alarm(self):
        if self.start_time is None:
            return

        interval = timedelta(seconds=self.interval_seconds)
        self.next_alarm_at += interval
        now = datetime.now(HKT)
        while self.next_alarm_at <= now:
            self.next_alarm_at += interval

    def show_alarm(self):
        self.next_alarm_id = None
        self.advance_next_alarm()

        if self.telegram_notification:
            self.notify_telegram()

        # Do not create multiple alarm windows if one is still open.
        if self.alarm_window is not None and self.alarm_window.winfo_exists():
            self.schedule_next_alarm()
            return

        if not self.desktop_notification:
            self.schedule_next_alarm()
            return

        self.root.bell()
        self.alarm_window = tk.Toplevel(self.root)
        self.alarm_window.title(self.title)
        self.alarm_window.geometry("380x190")
        self.alarm_window.resizable(False, False)
        self.alarm_window.attributes("-topmost", True)
        self.alarm_window.protocol("WM_DELETE_WINDOW", self.dismiss_alarm)
        self.alarm_window.bind("<Escape>", lambda event: self.stop())

        self.center_window(self.alarm_window)

        frame = tk.Frame(self.alarm_window, padx=24, pady=20)
        frame.pack(fill="both", expand=True)

        tk.Label(
            frame,
            text=self.message,
            font=("Arial", 16, "bold"),
            wraplength=320,
        ).pack(expand=True)

        button_frame = tk.Frame(frame)
        button_frame.pack(pady=(8, 0))

        tk.Button(
            button_frame,
            text="Dismiss",
            width=12,
            command=self.dismiss_alarm,
        ).pack(side="left", padx=4)

        tk.Button(
            button_frame,
            text="Stop",
            width=12,
            command=self.stop,
        ).pack(side="left", padx=4)

        self.alarm_window.lift()
        self.alarm_window.focus_force()
        self.schedule_next_alarm()

    def notify_telegram(self):
        if not telegram_is_configured():
            return
        threading.Thread(
            target=send_telegram_notification,
            args=(self.title, self.message),
            daemon=True,
        ).start()

    def dismiss_alarm(self):
        if self.alarm_window is not None and self.alarm_window.winfo_exists():
            self.alarm_window.destroy()
        self.alarm_window = None

    def center_window(self, window):
        window.update_idletasks()
        width = window.winfo_width()
        height = window.winfo_height()
        x = (window.winfo_screenwidth() - width) // 2
        y = (window.winfo_screenheight() - height) // 2
        window.geometry(f"{width}x{height}+{x}+{y}")

    def handle_sigint(self, signum, frame):
        """Stop cleanly when Ctrl+C reaches the terminal process."""
        self.stop()

    def stop(self):
        if self.next_alarm_id is not None:
            self.root.after_cancel(self.next_alarm_id)
            self.next_alarm_id = None
        self.dismiss_alarm()
        signal.signal(signal.SIGINT, self.previous_sigint_handler)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def parse_start_time(value):
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "start time must use HH:MM format, for example 09:30"
        ) from error


def parse_args():
    load_dotenv_file()
    parser = argparse.ArgumentParser(
        description="Show a recurring desktop alarm window."
    )
    parser.add_argument(
        "--interval",
        "-i",
        type=float,
        default=60,
        help="Seconds between alarms (default: 60).",
    )
    parser.add_argument(
        "--start-time",
        type=parse_start_time,
        help="Optional daily start time in HKT (UTC+8), using HH:MM format.",
    )
    parser.add_argument(
        "--title",
        default="Alarm",
        help="Alarm window title.",
    )
    parser.add_argument(
        "--message",
        default="The alarm interval has elapsed.",
        help="Message shown in the alarm window.",
    )
    parser.add_argument(
        "--desktop-notification",
        dest="desktop_notification",
        action="store_true",
        help="Open desktop alarm windows.",
    )
    parser.add_argument(
        "--no-desktop-notification",
        dest="desktop_notification",
        action="store_false",
        help="Do not open desktop alarm windows.",
    )
    parser.add_argument(
        "--telegram-notification",
        "--telegram",
        dest="telegram_notification",
        action="store_true",
        help="Send a Telegram message when an alarm triggers.",
    )
    parser.add_argument(
        "--no-telegram-notification",
        dest="telegram_notification",
        action="store_false",
        help="Do not send Telegram messages.",
    )
    parser.set_defaults(
        desktop_notification=DEFAULT_DESKTOP_NOTIFICATION,
        telegram_notification=DEFAULT_TELEGRAM_NOTIFICATION,
    )
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be greater than 0")
    if args.telegram_notification and not telegram_is_configured():
        parser.error(
            "--telegram-notification requires TELEGRAM_BOT_TOKEN and "
            "TELEGRAM_CHAT_ID"
        )
    return args


def main():
    global tk
    args = parse_args()

    try:
        import tkinter as tk
    except ImportError:
        print(
            "Tkinter is not available. Install Python with Tcl/Tk support "
            "and try again."
        )
        return 1

    app = None
    try:
        app = AlarmApp(
            args.interval,
            args.title,
            args.message,
            start_time=args.start_time,
            desktop_notification=args.desktop_notification,
            telegram_notification=args.telegram_notification,
        )
        app.run()
    except tk.TclError as error:
        print(f"Unable to open the alarm window: {error}")
        return 1
    except KeyboardInterrupt:
        if app is not None:
            app.stop()
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
