"""Manually preview the verification-code screenshot used by OCR.

Run with: python test_ocr_capture.py --resolution auto|1080p|4k
"""

import argparse

import game_supervisor


def run_ocr_capture_preview(resolution="auto"):
    """Find the visible verification-code prompt and show its OCR input."""
    game_supervisor.set_ocr_resolution(resolution)
    return game_supervisor.previewVerificationCodeCapture()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resolution",
        choices=("auto", "1080p", "4k"),
        default="auto",
        help="OCR UI scale (default: auto).",
    )
    run_ocr_capture_preview(parser.parse_args().resolution)
