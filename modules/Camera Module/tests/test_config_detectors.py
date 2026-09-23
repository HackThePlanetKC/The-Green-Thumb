"""
test_config_detectors.py - deep-merge behavior for config.py's new
"detectors" key (module-global detector thresholds), including its
nested pest_indicators sub-dicts. No cv2/numpy required.

Run: python3 test_config_detectors.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as config_module  # noqa: E402

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


# --- a fresh load has every detector's full default threshold set, including nested pest_indicators sub-dicts ---
fresh = config_module.load("/nonexistent/path/for/detectors-fresh")
check("fresh config has a detectors key for all six", set(fresh["detectors"].keys()) == {
    "chlorosis", "necrosis", "spotting", "leaf_scorch", "powdery_mildew", "pest_indicators",
})
check("pest_indicators has all three nested sub-checks", set(fresh["detectors"]["pest_indicators"].keys()) == {"webbing", "pest_clusters", "stippling"})
check("chlorosis has its own threshold keys", "affected_threshold_pct" in fresh["detectors"]["chlorosis"])

# --- a saved file with only a PARTIAL override still gets every other default filled in on load (2-level-deep merge) ---
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "config.json")
    with open(path, "w") as f:
        json.dump({"detectors": {"chlorosis": {"affected_threshold_pct": 9.5}}}, f)

    loaded = config_module.load(path)
    check("a partial override keeps the user's custom value", loaded["detectors"]["chlorosis"]["affected_threshold_pct"] == 9.5)
    check("a partial override still fills in the OTHER chlorosis defaults", loaded["detectors"]["chlorosis"]["green_hue_min"] == config_module.DEFAULT_CONFIG["detectors"]["chlorosis"]["green_hue_min"])
    check("a partial override still fills in entirely untouched detectors (necrosis)", loaded["detectors"]["necrosis"] == config_module.DEFAULT_CONFIG["detectors"]["necrosis"])

    # a 3-level-deep partial override (pest_indicators.webbing) is also correctly merged
    with open(path, "w") as f:
        json.dump({"detectors": {"pest_indicators": {"webbing": {"edge_density_min": 0.5}}}}, f)
    loaded2 = config_module.load(path)
    check("a 3-level-deep partial override keeps the custom value", loaded2["detectors"]["pest_indicators"]["webbing"]["edge_density_min"] == 0.5)
    check("a 3-level-deep partial override fills in sibling webbing defaults", loaded2["detectors"]["pest_indicators"]["webbing"]["canny_low"] == config_module.DEFAULT_CONFIG["detectors"]["pest_indicators"]["webbing"]["canny_low"])
    check("a 3-level-deep partial override fills in untouched sibling sub-checks (pest_clusters/stippling)", (
        loaded2["detectors"]["pest_indicators"]["pest_clusters"] == config_module.DEFAULT_CONFIG["detectors"]["pest_indicators"]["pest_clusters"]
        and loaded2["detectors"]["pest_indicators"]["stippling"] == config_module.DEFAULT_CONFIG["detectors"]["pest_indicators"]["stippling"]
    ))

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
