"""
test_grid_config.py - stub-based tests for grid_config.GridConfigManager,
against a real config.py pointed at a throwaway temp file.

Run: python3 test_grid_config.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as config_module  # noqa: E402
from base_association import BaseAssociationManager  # noqa: E402
from grid_config import GridConfigManager  # noqa: E402

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


class ConfigAtPath:
    def __init__(self, path):
        self._path = path

    def load(self):
        return config_module.load(self._path)

    def save(self, cfg):
        config_module.save(cfg, self._path)


with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "config.json")
    bound = ConfigAtPath(path)
    grid = GridConfigManager(bound)
    association = BaseAssociationManager(bound)

    check("starts at 1x1 with no cells", grid.get_grid() == {"rows": 1, "cols": 1, "cells": {}})

    grid.set_grid_dimensions(2, 4)
    check("set_grid_dimensions() persists non-square dims", grid.get_grid()["rows"] == 2 and grid.get_grid()["cols"] == 4)

    for bad in [(0, 2), (5, 2), (2, 5), (-1, 1)]:
        try:
            grid.set_grid_dimensions(*bad)
            raised = False
        except ValueError:
            raised = True
        check("set_grid_dimensions{} out of 1-4 range raises ValueError".format(bad), raised)

    association.set_associated_base_ids(["A1B2C3", "D4E5F6"])

    grid.set_cell_assignment(0, 0, "A1B2C3")
    grid.set_cell_assignment(0, 1, "A1B2C3")
    grid.set_cell_assignment(1, 2, "D4E5F6")
    check("cells are assigned to associated bases", grid.get_grid()["cells"] == {"0,0": "A1B2C3", "0,1": "A1B2C3", "1,2": "D4E5F6"})
    check("a base can be assigned multiple cells", grid.cells_for_base("A1B2C3") == [(0, 0), (0, 1)] or sorted(grid.cells_for_base("A1B2C3")) == [(0, 0), (0, 1)])
    check("assigned_bases() reports every base with at least one cell", grid.assigned_bases() == {"A1B2C3", "D4E5F6"})

    try:
        grid.set_cell_assignment(0, 0, "UNKNOWN99")
        raised = False
    except ValueError:
        raised = True
    check("assigning an unassociated base_id raises ValueError", raised)

    try:
        grid.set_cell_assignment(9, 9, "A1B2C3")
        raised = False
    except ValueError:
        raised = True
    check("assigning an out-of-bounds cell raises ValueError", raised)

    grid.set_cell_assignment(0, 0, None)
    check("assigning None clears a cell", "0,0" not in grid.get_grid()["cells"])

    # shrinking the grid drops now-out-of-bounds cells
    grid.set_grid_dimensions(1, 1)
    check("shrinking the grid drops out-of-bounds cell assignments", grid.get_grid()["cells"] == {})

    # cell_pixel_bbox: contiguous multi-cell base, evenly divided image
    grid2 = GridConfigManager(ConfigAtPath(os.path.join(d, "config2.json")))
    assoc2 = BaseAssociationManager(ConfigAtPath(os.path.join(d, "config2.json")))
    assoc2.set_associated_base_ids(["A1B2C3"])
    grid2.set_grid_dimensions(2, 2)
    grid2.set_cell_assignment(0, 0, "A1B2C3")
    grid2.set_cell_assignment(0, 1, "A1B2C3")
    bbox = grid2.cell_pixel_bbox("A1B2C3", 200, 200)
    check("cell_pixel_bbox() covers the top row of a 2x2 grid on a 200x200 image", bbox == (0, 0, 200, 100))
    check("cell_pixel_bbox() for an unassigned base returns None", grid2.cell_pixel_bbox("NOBODY", 200, 200) is None)

    # --- set_grid(): full-state replace in one atomic call ---
    path3 = os.path.join(d, "config3.json")
    bound3 = ConfigAtPath(path3)
    grid3 = GridConfigManager(bound3)
    assoc3 = BaseAssociationManager(bound3)
    assoc3.set_associated_base_ids(["A1B2C3", "D4E5F6"])

    grid3.set_grid(2, 2, {"0,0": "A1B2C3", "0,1": "A1B2C3", "1,0": "D4E5F6"})
    check("set_grid() applies dimensions and the full cell map together", grid3.get_grid() == {"rows": 2, "cols": 2, "cells": {"0,0": "A1B2C3", "0,1": "A1B2C3", "1,0": "D4E5F6"}})

    # a later call with a cell omitted clears it, rather than leaving it stale (full-state replace, not a diff)
    grid3.set_grid(2, 2, {"0,0": "D4E5F6"})
    check("set_grid() clears a cell omitted from a later call", grid3.get_grid()["cells"] == {"0,0": "D4E5F6"})

    # an invalid dimension is rejected before anything is written
    before = grid3.get_grid()
    try:
        grid3.set_grid(0, 2, {})
        raised = False
    except ValueError:
        raised = True
    check("set_grid() rejects an out-of-range dimension", raised)
    check("a rejected set_grid() dimension leaves the grid completely unchanged", grid3.get_grid() == before)

    # an unassociated base_id anywhere in cells is rejected ATOMICALLY - nothing (not even the valid
    # cells earlier in the dict, not even the new dimensions) is persisted, unlike the old per-cell-call
    # approach this replaced, which could partially apply a change before hitting an invalid cell
    try:
        grid3.set_grid(2, 2, {"0,0": "A1B2C3", "1,1": "NOT_A_REAL_BASE"})
        raised = False
    except ValueError:
        raised = True
    check("set_grid() rejects an unassociated base_id anywhere in cells", raised)
    check("a rejected set_grid() call leaves the grid completely unchanged (atomic, not partial)", grid3.get_grid() == before)

    # a cell key outside the new bounds is silently ignored, not an error - callers don't need to pre-filter
    grid3.set_grid(1, 1, {"0,0": "A1B2C3", "5,5": "D4E5F6"})
    check("set_grid() silently ignores an out-of-bounds cell key rather than raising", grid3.get_grid() == {"rows": 1, "cols": 1, "cells": {"0,0": "A1B2C3"}})

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
