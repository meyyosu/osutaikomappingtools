"""Bulk timing edits preserve spacing, selection scope and undo boundaries."""
from pathlib import Path
import sys
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from screens import TimingEditorFrame


class TimingBulkEditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.frame = TimingEditorFrame(
            self.root, SimpleNamespace(get_diff_files=lambda: (None, [])))
        self.frame._redraw_timeline = Mock()
        for offset, bpm in ((1000, 120), (2500, 150), (4000, 180)):
            self.frame._make_row(offset, 60000 / bpm, False, 4, 1, 0, 100, 0)
        self.frame._committed_state = self.frame._capture_state()
        self.frame._initial_state = self.frame._committed_state
        self.frame._sync_delete_button()

    def tearDown(self):
        self.frame._cancel_commit_job()
        self.frame.destroy()

    def select(self, *indices):
        for i, row in enumerate(self.frame.rows):
            row["sel_var"].set(i in indices)
        self.frame._on_selection_changed()

    def test_bulk_update_and_single_step_undo_redo(self):
        f = self.frame
        self.select(0, 1)
        before = f._capture_state()
        f.rows[0]["doff_var"].set("-25")
        f.rows[0]["nbpm_var"].set("160.125")
        f.rows[0]["ts_var"].set("3")
        f._flush_commit()
        for i, offset in enumerate((975, 2475)):
            row = f.rows[i]
            self.assertEqual(f._f_off(row["noff_var"]), offset)
            self.assertEqual(row["doff_var"].get(), "-25")
            self.assertEqual(row["nbpm_var"].get(), "160.125")
            self.assertEqual(row["ts_var"].get(), "3")
        self.assertEqual(f._capture_state()[2], before[2])
        after = f._capture_state()
        self.assertEqual(len(f._undo_stack), 1)
        f._undo()
        self.assertEqual(f._capture_state(), before)
        f._redo()
        self.assertEqual(f._capture_state(), after)

    def test_edit_only_copies_the_changed_column(self):
        f = self.frame
        self.select(0, 1)
        f.rows[0]["dbpm_var"].set("10")
        self.assertEqual(f.rows[1]["nbpm_var"].get(), "160")
        self.assertEqual(f._f_off(f.rows[1]["noff_var"]), 2500)
        f.rows[0]["noff_var"].set("00:01:500")
        self.assertEqual(f._f_off(f.rows[1]["noff_var"]), 1500)
        self.assertEqual(f.rows[1]["doff_var"].get(), "-1000")
        self.assertEqual(f.rows[1]["nbpm_var"].get(), "160")
        # Editing an unselected row must stay local.
        f.rows[2]["nbpm_var"].set("200")
        self.assertEqual(f.rows[0]["nbpm_var"].get(), "130")
        self.assertEqual(f.rows[1]["nbpm_var"].get(), "160")

    def test_partial_input_does_not_clear_other_selected_fields(self):
        f = self.frame
        self.select(0, 1)
        for field, partial in (("nbpm_var", ""), ("dbpm_var", "-"),
                               ("noff_var", "-"), ("doff_var", "-"),
                               ("ts_var", ""), ("ts_var", "101")):
            before = f._capture_state()[1]
            f.rows[0][field].set(partial)
            self.assertEqual(f._capture_state()[1], before)
        f.rows[0]["ts_var"].set("7")
        self.assertEqual(f.rows[1]["ts_var"].get(), "7")

    def test_select_all_tracks_rows_and_excludes_deleted(self):
        f = self.frame
        self.select(0)
        self.assertFalse(f.select_all_var.get())
        f.select_all_chk._toggle()
        self.assertEqual(len(f._selected_rows()), 3)
        self.assertTrue(f.select_all_var.get())
        f.select_all_chk._toggle()
        self.assertEqual(f._selected_rows(), [])
        self.select(1)
        f._delete_selected()
        f.select_all_chk._toggle()
        self.assertEqual(len(f._selected_rows()), 2)
        self.assertFalse(f.rows[1]["sel_var"].get())
        f._undo()
        self.assertFalse(f.select_all_var.get())
        self.assertEqual(len(f._visible_rows()), 3)


if __name__ == "__main__":
    unittest.main()
