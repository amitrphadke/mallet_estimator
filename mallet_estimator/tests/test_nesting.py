import unittest

from mallet_estimator import nesting


class TestNesting(unittest.TestCase):
    def test_single_part_single_sheet(self):
        r = nesting.pack_sheets([(2000, 1000)])
        self.assertEqual(r["sheets"], 1)

    def test_full_sheets_do_not_merge(self):
        # four half-sheets fit two per sheet
        r = nesting.pack_sheets([(2400, 590)] * 4, kerf=4, trim=10)
        self.assertEqual(r["sheets"], 2)

    def test_grain_lock_blocks_rotation(self):
        # part fits only rotated: allowed when rotation on, rejected when locked
        ok = nesting.pack_sheets([(1000, 2000)], allow_rotate=True)
        self.assertEqual(ok["sheets"], 1)
        locked = nesting.pack_sheets([(1000, 2000)], allow_rotate=False)
        self.assertEqual(locked["sheets"], 0)
        self.assertEqual(len(locked["too_big"]), 1)

    def test_monotonic_more_parts_never_fewer_sheets(self):
        base = [(800, 600)] * 6
        more = base + [(800, 600)] * 6
        self.assertLessEqual(nesting.pack_sheets(base)["sheets"],
                             nesting.pack_sheets(more)["sheets"])

    def test_edge_rolls(self):
        self.assertEqual(nesting.edge_rolls(0), 0)
        self.assertEqual(nesting.edge_rolls(49.9), 1)
        self.assertEqual(nesting.edge_rolls(50.1), 2)
