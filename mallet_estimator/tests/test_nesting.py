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

    def test_envelope_check(self):
        ply = {("SG_PLY_V1_a_b", 18.0): [(2100, 580), (2400, 600)]}
        # wardrobe 2133 x 580 x 2133: the 2400 part cannot build it
        bad = nesting.envelope_check((2133, 580, 2133), ply)
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0][1], 2400)
        # within tolerance passes; missing dims -> no check
        self.assertEqual(nesting.envelope_check((2133, 580, 2133),
                                                {("X", 18.0): [(2150, 580)]}), [])
        self.assertEqual(nesting.envelope_check((0, 0, 0), ply), [])
