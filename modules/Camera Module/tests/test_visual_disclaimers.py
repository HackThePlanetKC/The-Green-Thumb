"""
test_visual_disclaimers.py - sanity checks for visual_disclaimers.py's
shared constants. No cv2/numpy required.

Run: python3 test_visual_disclaimers.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visual_disclaimers import FULL_DISCLAIMER, SHORT_DISCLAIMER  # noqa: E402

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


check("FULL_DISCLAIMER is a non-empty string", isinstance(FULL_DISCLAIMER, str) and len(FULL_DISCLAIMER) > 0)
check("FULL_DISCLAIMER mentions it is not diagnostic-grade", "not" in FULL_DISCLAIMER.lower() and "diagnostic" in FULL_DISCLAIMER.lower())
check("FULL_DISCLAIMER mentions false positives/negatives", "false positive" in FULL_DISCLAIMER.lower() and "false negative" in FULL_DISCLAIMER.lower())
check("FULL_DISCLAIMER tells the user to verify with a professional", "professional" in FULL_DISCLAIMER.lower())

check("SHORT_DISCLAIMER is a non-empty string", isinstance(SHORT_DISCLAIMER, str) and len(SHORT_DISCLAIMER) > 0)
check("SHORT_DISCLAIMER is meaningfully shorter than the full text", len(SHORT_DISCLAIMER) < len(FULL_DISCLAIMER) / 3)
check("SHORT_DISCLAIMER signals this is heuristic, not a diagnosis", "heuristic" in SHORT_DISCLAIMER.lower() and "diagnos" in SHORT_DISCLAIMER.lower())

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
