"""
grid_config.py - Grid layout config: how many regions the camera's
frame is divided into, and which associated base each region belongs
to.

Module-global (see config.py's grid key) - editable only via the
camera portal or HA (mqtt_presence.py), never from an individual
base's own portal. A thin wrapper around config.py, same pattern as
base_association.py: no separate storage file, one config.json for
the whole module.

Grid dimensions: up to 4x4, and deliberately NOT restricted to square
layouts - a 2x4 or 1x3 grid is just as valid as 3x3, so rows and cols
are independent bounds, not one shared "size" value.

Cell->base_id assignment: a base may be assigned multiple cells, and
those cells are expected to be contiguous (a base watches one
physical region of the frame) - this module does NOT enforce
contiguity itself. Validating "is this set of cells actually
contiguous" is a real algorithm (flood-fill/graph connectivity) that
adds meaningful complexity for a constraint the portal's own grid-click
UI already makes hard to violate by accident (clicking distant cells
for the same base is an unusual, deliberate action, not something a
simple form submission trips into). Documented here as a known
simplification - revisit if non-contiguous assignments turn out to be
a real problem in practice, not guessed at now.
"""

MAX_ROWS = 4
MAX_COLS = 4


def _cell_key(row, col):
    return "{},{}".format(row, col)


class GridConfigManager:
    def __init__(self, config_module):
        """
        config_module: the config.py module itself (or anything
        exposing load()/save() with the same signature) - injected for
        the same testability reason as BaseAssociationManager. See
        tests/test_grid_config.py.
        """
        self._config_module = config_module

    def get_grid(self):
        """Returns {"rows": int, "cols": int, "cells": {"r,c": base_id}}."""
        cfg = self._config_module.load()
        grid = cfg.get("grid", {})
        return {
            "rows": grid.get("rows", 1),
            "cols": grid.get("cols", 1),
            "cells": dict(grid.get("cells", {})),
        }

    def set_grid_dimensions(self, rows, cols):
        """
        Sets the grid size (1-4 rows, 1-4 cols, independently - non-
        square layouts like 2x4 or 1x3 are fully supported). Shrinking
        the grid drops any now-out-of-bounds cell assignments rather
        than leaving orphaned entries a smaller grid can never display
        or edit again.
        """
        if not (1 <= rows <= MAX_ROWS) or not (1 <= cols <= MAX_COLS):
            raise ValueError("grid dimensions must be between 1 and {}x{}".format(MAX_ROWS, MAX_COLS))

        cfg = self._config_module.load()
        cfg["grid"]["rows"] = rows
        cfg["grid"]["cols"] = cols
        cfg["grid"]["cells"] = {
            key: base_id
            for key, base_id in cfg["grid"]["cells"].items()
            if self._in_bounds(key, rows, cols)
        }
        self._config_module.save(cfg)

    def set_cell_assignment(self, row, col, base_id):
        """
        Assigns cell (row, col) to base_id, or unassigns it if base_id
        is None. Validates the cell is within the current grid bounds
        and, when assigning (not clearing), that base_id is one of
        this module's associated bases - the grid can only ever point
        at a base this module actually monitors.
        """
        cfg = self._config_module.load()
        rows, cols = cfg["grid"]["rows"], cfg["grid"]["cols"]
        if not (0 <= row < rows) or not (0 <= col < cols):
            raise ValueError("cell ({}, {}) is outside the current {}x{} grid".format(row, col, rows, cols))

        key = _cell_key(row, col)
        if base_id is None:
            cfg["grid"]["cells"].pop(key, None)
        else:
            if base_id not in cfg.get("associated_base_ids", []):
                raise ValueError("{} is not an associated base".format(base_id))
            cfg["grid"]["cells"][key] = base_id
        self._config_module.save(cfg)

    def assigned_bases(self):
        """Returns the set of base_ids currently assigned to at least one cell."""
        return set(self.get_grid()["cells"].values())

    def cells_for_base(self, base_id):
        """Returns the list of (row, col) tuples currently assigned to base_id."""
        cells = []
        for key, assigned_id in self.get_grid()["cells"].items():
            if assigned_id == base_id:
                row, col = key.split(",")
                cells.append((int(row), int(col)))
        return cells

    def cell_pixel_bbox(self, base_id, image_width, image_height):
        """
        Returns (left, top, right, bottom) pixel coordinates covering
        base_id's assigned cell(s) within an image_width x image_height
        reference photo, or None if base_id has no cells assigned.

        Cells are divided evenly across the image (image_width/cols,
        image_height/rows). For a base with multiple cells, this
        returns the bounding box of its min/max row and col - the
        bounding box of a genuinely contiguous block is that block
        itself; for a non-contiguous assignment (not validated against,
        see module docstring) this would also include the cells
        between them, a known consequence of that same simplification.
        """
        cells = self.cells_for_base(base_id)
        if not cells:
            return None

        grid = self.get_grid()
        rows, cols = grid["rows"], grid["cols"]
        cell_w = image_width / float(cols)
        cell_h = image_height / float(rows)
        min_row = min(r for r, c in cells)
        max_row = max(r for r, c in cells)
        min_col = min(c for r, c in cells)
        max_col = max(c for r, c in cells)
        return (
            int(round(min_col * cell_w)),
            int(round(min_row * cell_h)),
            int(round((max_col + 1) * cell_w)),
            int(round((max_row + 1) * cell_h)),
        )

    @staticmethod
    def _in_bounds(cell_key, rows, cols):
        row, col = cell_key.split(",")
        return 0 <= int(row) < rows and 0 <= int(col) < cols
