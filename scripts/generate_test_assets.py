"""
Generate test assets for Module 2.5 verification:
1. Real EPFO Member Portal claim status screenshot.
2. Deliberately blank image (pure white canvas).
3. Corrupt file with broken PDF header.
"""

from __future__ import annotations

import os
from PIL import Image, ImageDraw, ImageFont

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "../tests/assets")
os.makedirs(ASSETS_DIR, exist_ok=True)


def create_claim_screenshot() -> str:
    """Create a realistic EPFO member portal claim status screenshot."""
    width, height = 900, 520
    img = Image.new("RGB", (width, height), color=(248, 249, 250))
    draw = ImageDraw.Draw(img)

    # Header bar (EPFO Navy Blue)
    draw.rectangle([(0, 0), (width, 70)], fill=(20, 50, 95))
    draw.text((30, 15), "EMPLOYEES' PROVIDENT FUND ORGANISATION, INDIA", fill=(255, 255, 255))
    draw.text((30, 38), "Unified Member Portal - View Claim Status & Track Dispatch", fill=(200, 220, 245))

    # White card container
    card_box = [(30, 90), (width - 30, height - 30)]
    draw.rectangle(card_box, fill=(255, 255, 255), outline=(220, 225, 230), width=2)

    # Card Title
    draw.text((50, 105), "Claim Details & Tracking History", fill=(30, 41, 59))
    draw.line([(50, 135), (width - 50, 135)], fill=(230, 235, 240), width=1)

    # Key-Value Fields
    fields = [
        ("Universal Account Number (UAN):", "101458923012"),
        ("Member Name:", "RAMESH CHANDRA VERMA"),
        ("Member ID:", "DLCPM00123450000067890"),
        ("Claim ID (Tracking Number):", "DLCPM2410150001289"),
        ("Claim Form Applied:", "Form 19 - Final Settlement (PF Withdrawal)"),
        ("Claim Receipt Date:", "15-OCT-2024"),
        ("Total Amount Claimed:", "Rs. 1,48,500.00"),
        ("Current Claim Status:", "REJECTED / RETURNED"),
        ("Rejection Reason:", "Member name on bank cancelled cheque does not match Aadhaar seeding."),
        ("Field Office:", "EPFO Regional Office, Delhi North (Wazirpur)"),
    ]

    y = 150
    for label, val in fields:
        draw.text((50, y), label, fill=(100, 116, 139))
        if "REJECTED" in val:
            # Red badge
            draw.rectangle([(295, y - 2), (480, y + 18)], fill=(254, 226, 226), outline=(239, 68, 68))
            draw.text((305, y), val, fill=(185, 28, 28))
        else:
            draw.text((300, y), val, fill=(15, 23, 42))
        y += 32

    path = os.path.join(ASSETS_DIR, "epfo_claim_screenshot.png")
    img.save(path, "PNG")
    print(f"Created claim screenshot: {path}")
    return path


def create_blank_image() -> str:
    """Create a completely blank image."""
    img = Image.new("RGB", (400, 400), color=(255, 255, 255))
    path = os.path.join(ASSETS_DIR, "blank_document.png")
    img.save(path, "PNG")
    print(f"Created blank image: {path}")
    return path


def create_corrupt_file() -> str:
    """Create a corrupt file with broken binary content."""
    path = os.path.join(ASSETS_DIR, "corrupt_document.pdf")
    with open(path, "wb") as f:
        f.write(b"%PDF-1.4\nGARBAGE_BYTES_CORRUPTED_STREAM\x00\xff\xfe\x01\x02\x03\xff")
    print(f"Created corrupt file: {path}")
    return path


if __name__ == "__main__":
    create_claim_screenshot()
    create_blank_image()
    create_corrupt_file()
