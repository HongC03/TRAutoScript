"""Manually preview the verification-code screenshot used by OCR.

Run with: python test_ocr_capture.py
"""

import game_supervisor


def run_ocr_capture_preview():
    """Find the visible verification-code prompt and show its OCR input."""
    return game_supervisor.previewVerificationCodeCapture()


if __name__ == "__main__":
    run_ocr_capture_preview()
