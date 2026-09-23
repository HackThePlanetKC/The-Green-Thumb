"""
test_base_association.py - stub-based tests for
base_association.BaseAssociationManager, against a real config.py
pointed at a throwaway temp file (not the module's real config.json).

Run: python3 test_base_association.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as config_module  # noqa: E402
from base_association import BaseAssociationManager  # noqa: E402

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


class ConfigAtPath:
    """Binds config_module's load()/save() to one fixed throwaway path, matching the (config_module, path=...) shape BaseAssociationManager expects without it needing to know about paths at all."""

    def __init__(self, path):
        self._path = path

    def load(self):
        return config_module.load(self._path)

    def save(self, cfg):
        config_module.save(cfg, self._path)


with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "config.json")
    bound = ConfigAtPath(path)
    mgr = BaseAssociationManager(bound)

    check("starts empty", mgr.associated_base_ids() == [])

    mgr.set_associated_base_ids(["A1B2C3", "D4E5F6"])
    check("set_associated_base_ids() persists", mgr.associated_base_ids() == ["A1B2C3", "D4E5F6"])

    # de-dup, preserve first-seen order
    mgr.set_associated_base_ids(["D4E5F6", "A1B2C3", "D4E5F6"])
    check("de-dups while preserving order", mgr.associated_base_ids() == ["D4E5F6", "A1B2C3"])

    # replaces the full set, doesn't merge with the previous one
    mgr.set_associated_base_ids(["999999"])
    check("replaces the full set rather than merging with the previous one", mgr.associated_base_ids() == ["999999"])

    # other config (e.g. flash settings) untouched by association writes
    cfg = config_module.load(path)
    check("other config sections untouched", cfg["flash"]["low_light_threshold"] == config_module.DEFAULT_CONFIG["flash"]["low_light_threshold"])

    # a fresh manager pointed at the same file sees the persisted state (real round trip through disk, not just in-memory)
    mgr2 = BaseAssociationManager(bound)
    check("a second manager instance reads the same persisted state", mgr2.associated_base_ids() == ["999999"])

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
