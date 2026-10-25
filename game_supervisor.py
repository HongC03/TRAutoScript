"""Start Tales Runner, log in, restart after disconnects, and clear prompts.

This script reuses account/password from autoPWD.txt and image assets in scr/.
It is intended to run on Windows with the same dependencies as autoRace.py.
"""

from pathlib import Path
from statistics import mode
import time

import ddddocr as dd
import keyboard
import numpy as np
from PIL import Image
import pyautogui as gui
import pydirectinput as key
import pygetwindow as gw

from utils import (
    capture_screen,
    locate_on_screen,
    locate_on_screen_scaled,
    pressButton,
    triggerIfDetected,
)


BASE_DIR = Path(__file__).resolve().parent
GAME_TITLE = "Tales Runner"
LAUNCHER_TITLE = "跑Online"
TR_PATH = r"C:\Users\Public\Desktop\TalesRunner.lnk"
CHECK_INTERVAL = 1

_ocr = None
gui.useImageNotFoundException(False)

def asset(name):
    return str(BASE_DIR / "scr" / name)


def readCredentials(required=False):
    """Read account and password, requiring them only for auto-start."""
    config_paths = (BASE_DIR / "autoPWD.txt", BASE_DIR / "autoPWD.txt.txt")
    config_path = next((path for path in config_paths if path.is_file()), None)
    if config_path is None:
        if required:
            raise FileNotFoundError("找不到 autoPWD.txt")
        return "", ""

    values = {}
    with config_path.open("r", encoding="utf-8-sig") as config:
        for line in config:
            key_name, separator, value = line.partition("=")
            if separator and key_name.strip() in {"account", "password"}:
                values[key_name.strip()] = value.strip()

    account = values.get("account", "")
    password = values.get("password", "")
    if required and (not account or not password):
        raise ValueError(
            "啟用自動啟動時，autoPWD.txt 中的 account 與 password 皆不可為空"
        )

    if account:
        print(f"* 已讀取帳號: {account}")
    return account, password


def gameWindows():
    return gw.getWindowsWithTitle(GAME_TITLE)


def frontWindow(window):
    """Restore and activate a Tales Runner window."""
    try:
        window.restore()
        window.activate()
    except gw.PyGetWindowException:
        keyboard.send("alt+tab")
        time.sleep(0.5)


def itemExpired(region=None):
    if triggerIfDetected(asset("cross.png"), region=region):
        print("* 已按下紅叉 *")
        time.sleep(1)
        return True
    return False


def getOcr():
    global _ocr
    if _ocr is None:
        _ocr = dd.DdddOcr(beta=True, show_ad=False)
    return _ocr


def _verificationCodeRegion(find, scale=1.0):
    return (
        int(round(find[0] + 109 * scale)),
        int(round(find[1] - 7 * scale)),
        int(round(73 * scale)),
        int(round(48 * scale)),
    )


def _prepareVerificationCodeImage(image, threshold):
    pixels = np.array(image)
    for x in range(image.size[0]):
        for y in range(image.size[1]):
            red, green, blue = (int(value) for value in pixels[y, x])
            if (blue - red > threshold) or (blue - green > threshold):
                pixels[y, x] = [255, 255, 255]
            else:
                pixels[y, x] = [0, 0, 0]
    return Image.fromarray(pixels)


def previewVerificationCodeCapture(find=None, region=None, threshold=58):
    """Locate and show the verification-code crop and its OCR-ready image."""
    scale = 1.0
    if find is None:
        find, scale = locate_on_screen_scaled(
            asset("verification_code.png"),
            confidence=0.89,
            region=region,
            return_scale=True,
        )
    if find is None:
        screen_image = capture_screen()
        screen_image.show(title="Primary monitor screenshot")
        print(
            "Verification-code prompt was not found; "
            "showing the full primary-monitor screenshot instead."
        )
        return screen_image

    verification_image = capture_screen(
        region=_verificationCodeRegion(find, scale)
    )
    if scale != 1.0:
        verification_image = verification_image.resize((
            max(1, round(verification_image.width / scale)),
            max(1, round(verification_image.height / scale)),
        ), Image.Resampling.LANCZOS)
    ocr_image = _prepareVerificationCodeImage(verification_image, threshold)

    verification_image.show(title="Verification code capture")
    ocr_image.resize(
        (ocr_image.width * 4, ocr_image.height * 4)
    ).show(title=f"OCR input (threshold {threshold})")
    return ocr_image


def testVerificationCodeCapture():
    """Manually preview the visible verification-code prompt for OCR."""
    return previewVerificationCodeCapture()


def enterVerificationCode(find, region=None, scale=1.0):
    """Recognize and enter the two-digit verification code."""
    print("")
    key.moveTo(10, 10)

    threshold = 58
    results = []
    verification_region = _verificationCodeRegion(find, scale)
    verification_image = capture_screen(region=verification_region)
    if scale != 1.0:
        verification_image = verification_image.resize((
            max(1, round(verification_image.width / scale)),
            max(1, round(verification_image.height / scale)),
        ), Image.Resampling.LANCZOS)

    for _ in range(5):
        ocr_image = _prepareVerificationCodeImage(
            verification_image, threshold
        )
        result = getOcr().classification(ocr_image)
        if len(result) == 2:
            result = result.replace("o", "0").replace("O", "0")
            result = result.replace("i", "1").replace("I", "1")
            if result.isdigit():
                results.append(result)
        threshold += 2

    if results:
        digits = mode(results)
        print(f"* Verification code recognized: {digits} *")
    else:
        digits = "01"
        print("* Verification code recognition failed; entering 01 to retry *")

    for digit in digits:
        number_pos = locate_on_screen_scaled(
            asset(f"num{digit}.png"),
            confidence=0.89,
            region=region,
        )
        if number_pos is not None:
            pressButton(number_pos)
        else:
            key.click()
        key.moveTo(10, 10)
        time.sleep(0.3)

    if results:
        key.press("esc")
    key.moveTo(10, 10)
    print("")


def acceptFriendRequest(region=None):
    """Accept a visible friend request, or return None when none is visible."""
    friend_request_pos = locate_on_screen(
        asset("friend_request.png"),
        confidence=0.89,
        region=region,
    )
    if friend_request_pos is None:
        return None

    if not triggerIfDetected(
        asset("accept_button.png"), action="click", region=region
    ):
        return False

    print('* 已發現並按下好友邀請的“接受”鍵 *')
    return True


def clearPrompt(region=None):
    """Clear prompts and return whether any blocker was handled."""
    handled = False
    verification_pos, verification_scale = locate_on_screen_scaled(
        asset("verification_code.png"),
        confidence=0.89,
        region=region,
        return_scale=True,
    )
    if verification_pos is not None:
        print(
            f"* Verification-code prompt found (template scale: "
            f"{verification_scale:g}x) *"
        )
        enterVerificationCode(
            verification_pos,
            region=region,
            scale=verification_scale,
        )
        handled = True

    friend_request_accepted = acceptFriendRequest(region=region)
    if friend_request_accepted is not None:
        return handled or friend_request_accepted

    if itemExpired(region=region):
        handled = True
    if triggerIfDetected(
        asset("okButton.png"),
        action="click",
        region=region,
        scales=(0.75, 0.875, 1.125, 1.25, 1.5, 1.75, 2.0),
    ):
        print('* 已發現並按下“確認”鍵 *')
        handled = True
    if triggerIfDetected(
        asset("denyInvite.png"), action="click", region=region
    ):
        print('* 已發現並按下“取消”鍵 *')
        handled = True
    return handled


def closeGame():
    """Close every open game window before a restart."""
    while True:
        windows = gameWindows()
        if not windows:
            return
        print("* 關閉現有遊戲視窗 *")
        for window in windows:
            frontWindow(window)
            keyboard.send("alt+f4")
            time.sleep(1)
        time.sleep(8)


def launchGame(account, password):
    """Launch Tales Runner through PATH and fill in the login form."""
    while not gameWindows():
        print("* 開啟「執行」並啟動 TalesRunner *")
        keyboard.send("win+r")
        time.sleep(1)
        keyboard.write(TR_PATH, delay=0.05)
        key.press("enter")
        time.sleep(5)

        print("* 按 15 次 Enter 啟動遊戲 *")
        "add function to get TalesRunner in foreground"
        launcher_windows = gw.getWindowsWithTitle(LAUNCHER_TITLE)
        frontWindow(launcher_windows[0])
        for _ in range(15):
            key.press("enter")
            time.sleep(0.5)

        special_case = False
        for _ in range(50):
            if gameWindows():
                break
            special_windows = gw.getWindowsWithTitle(LAUNCHER_TITLE)
            if special_windows:
                special_case = True
                break
            time.sleep(1)

        if special_case:
            print("* 處理畫面縮放導致的啟動器錯誤 *")
            frontWindow(special_windows[0])
            key.press("enter")
            time.sleep(1)
            keyboard.send("win+r")
            time.sleep(1)
            keyboard.write(TR_PATH, delay=0.07)
            key.press("enter")
            time.sleep(20)
            for _ in range(15):
                key.press("enter")
                time.sleep(0.5)
            time.sleep(40)

        if not gameWindows():
            print("** 遊戲啟動失敗，準備重試 **")
            for _ in range(3):
                key.press("enter")
                key.press("esc")
            time.sleep(2)

    window = gameWindows()[0]
    frontWindow(window)
    print("* 輸入帳號與密碼 *")
    keyboard.write(account, delay=0.07)
    key.press("tab")
    keyboard.write(password, delay=0.07)
    key.press("enter")
    time.sleep(1)

    print("* 清除登入後的啟動提示 *")
    for _ in range(30):
        key.press("esc")
        time.sleep(1)

    if 'special_case' in locals() and special_case:
        for _ in range(2):
            keyboard.send("alt+enter")
            time.sleep(2)
    key.moveTo(10, 10)
    print("* Tales Runner 啟動及登入完成 *")


def restartGame(account, password):
    print("\n* 嘗試重新啟動 Tales Runner *")
    closeGame()
    launchGame(account, password)


class GameSupervisor:
    """Own optional game startup, restart detection, and prompt/OCR handling."""

    def __init__(
        self,
        account="",
        password="",
        auto_start=False,
        screen_region=None,
        focus_game_window=None,
    ):
        if auto_start and (not account or not password):
            raise ValueError(
                "啟用 auto_start 時必須提供 account 與 password"
            )

        self.account = account
        self.password = password
        self.auto_start = auto_start
        self.screen_region = screen_region
        self.focus_game_window = focus_game_window
        key.FAILSAFE = False
        gui.FAILSAFE = False
        key.PAUSE = 0.02

    @classmethod
    def from_config(cls, auto_start=False, screen_region=None, focus_game_window=None):
        """Create a supervisor, loading required credentials for auto-start."""
        return cls(
            *readCredentials(required=auto_start),
            auto_start=auto_start,
            screen_region=screen_region,
            focus_game_window=focus_game_window,
        )

    def ensure_ready(self):
        """Run one supervision tick and return the resulting game state."""
        windows = gameWindows()
        if not windows:
            if not self.auto_start:
                return "not_running"
            print("** 遊戲視窗已關閉，自動重新啟動 **")
            launchGame(self.account, self.password)
            return "started"

        if self.focus_game_window is None:
            frontWindow(windows[0])
        elif not self.focus_game_window(windows[0]):
            return "not_focused"
        if self.auto_start:
            offline_pos = locate_on_screen(
                asset("offline.png"),
                confidence=0.89,
                region=self.screen_region,
            )
            if offline_pos is not None:
                print("** 偵測到斷線，自動重新啟動 **")
                restartGame(self.account, self.password)
                return "restarted"

        clearPrompt(region=self.screen_region)
        return "ready"

    def run_forever(self, interval=CHECK_INTERVAL):
        """Continuously supervise the game until the process is interrupted."""
        if self.auto_start:
            feature_description = "自動啟動/重新啟動及提示清理"
        else:
            feature_description = "提示清理"
        print(f"* {feature_description}已啟用；按 Ctrl+C 停止 *")
        while True:
            self.ensure_ready()
            time.sleep(interval)


def main():
    GameSupervisor.from_config(auto_start=True).run_forever()


if __name__ == "__main__":
    try:
        main()
    except gui.ImageNotFoundException:
        pass
    except (FileNotFoundError, ValueError) as error:
        print(f"** {error} **")
    except KeyboardInterrupt:
        print("\n* 程式已停止 *")
