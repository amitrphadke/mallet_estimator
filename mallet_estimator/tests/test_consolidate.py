import unittest

from mallet_estimator import consolidate


def _inputs(**skus):
    return skus


class TestConsolidate(unittest.TestCase):
    def test_combined_never_more_than_standalone(self):
        # two SKUs, each with half-sheet parts of the SAME ply: alone they'd
        # each round up to a full sheet; together they share sheets
        half = [(2400, 590)]
        r = consolidate.consolidate(_inputs(
            WAR={"ply": {"SG_PLY_V1_a_b@18": half}, "lam": {}, "edges": {}},
            BED={"ply": {"SG_PLY_V1_a_b@18": half}, "lam": {}, "edges": {}},
        ))
        m = r["materials"]["SG_PLY_V1_a_b@18"]
        self.assertEqual(m["standalone"], 2)
        self.assertEqual(m["combined"], 1)
        # equal parts -> equal halves of the one combined sheet
        self.assertAlmostEqual(m["alloc"]["WAR"], 0.5, places=3)
        self.assertAlmostEqual(m["alloc"]["BED"], 0.5, places=3)

    def test_allocation_is_part_area_share(self):
        big = [(2000, 600)]           # 1.2 m² of parts
        small = [(1000, 500)]         # 0.5 m² of parts
        r = consolidate.consolidate(_inputs(
            A={"ply": {"P@18": big}, "lam": {}, "edges": {}},
            B={"ply": {"P@18": small}, "lam": {}, "edges": {}},
        ))
        m = r["materials"]["P@18"]
        self.assertEqual(m["combined"], 1)
        share_a = m["alloc"]["A"] / (m["alloc"]["A"] + m["alloc"]["B"])
        self.assertAlmostEqual(share_a, 1.2 / 1.7, places=2)

    def test_edge_rolls_combine(self):
        # 30 m + 30 m: alone 1 roll each (2 total), together 2 rolls of 50 m?
        # no — 60 m -> 2 rolls; but 20 m + 20 m -> alone 2 rolls, together 1
        r = consolidate.consolidate(_inputs(
            A={"ply": {"P@18": [(100, 100)]}, "lam": {}, "edges": {"EB_X": 20.0}},
            B={"ply": {"P@18": [(100, 100)]}, "lam": {}, "edges": {"EB_X": 20.0}},
        ))
        m = r["materials"]["EB_X"]
        self.assertEqual(m["standalone"], 2)
        self.assertEqual(m["combined"], 1)
        self.assertAlmostEqual(m["alloc"]["A"], 0.5, places=3)

    def test_sheet_ratio_drives_ops(self):
        half = [(2400, 590)]
        r = consolidate.consolidate(_inputs(
            WAR={"ply": {"P@18": half}, "lam": {}, "edges": {}},
            BED={"ply": {"P@18": half}, "lam": {}, "edges": {}},
        ))
        # 1 standalone sheet each -> 0.5 allocated each -> ratio 0.5
        self.assertAlmostEqual(r["sheet_ratio"]["WAR"], 0.5, places=3)

    def test_single_sku_is_neutral(self):
        r = consolidate.consolidate(_inputs(
            WAR={"ply": {"P@18": [(2000, 1000)]}, "lam": {}, "edges": {"EB_X": 10}},
        ))
        m = r["materials"]["P@18"]
        self.assertEqual(m["combined"], m["standalone"])
        self.assertAlmostEqual(r["sheet_ratio"]["WAR"], 1.0, places=3)

    def test_batch_factor(self):
        tiers = [(0, 1.0), (10, 0.85), (30, 0.7)]
        self.assertEqual(consolidate.batch_factor(tiers, 5), 1.0)
        self.assertEqual(consolidate.batch_factor(tiers, 10), 0.85)
        self.assertEqual(consolidate.batch_factor(tiers, 50), 0.7)
        self.assertEqual(consolidate.batch_factor([], 50), 1.0)
        self.assertEqual(consolidate.batch_factor(None, 5), 1.0)



class TestModeGuard(unittest.TestCase):
    def test_split_and_mixed(self):
        modes = {"A": "CSV-Nest", "B": "OCL PDF (standard)", "C": "CSV-Nest"}
        csv_nest, pdf = consolidate.split_by_mode(modes)
        self.assertEqual(csv_nest, ["A", "C"])
        self.assertEqual(pdf, ["B"])
        self.assertTrue(consolidate.is_mixed(modes))

    def test_homogeneous_is_not_mixed(self):
        self.assertFalse(consolidate.is_mixed({"A": "CSV-Nest", "B": "CSV-Nest"}))
        self.assertFalse(consolidate.is_mixed({"A": "OCL PDF (standard)"}))
        self.assertFalse(consolidate.is_mixed({}))

    def test_blank_mode_counts_as_pdf(self):
        # legacy SKUs predate the field: they are PDF-mode by definition
        self.assertFalse(consolidate.is_mixed({"A": None, "B": ""}))
        self.assertTrue(consolidate.is_mixed({"A": None, "B": "CSV-Nest"}))


class TestIntakeRowMode(unittest.TestCase):
    def test_files_decide_the_mode(self):
        self.assertEqual(consolidate.intake_row_mode(True, False), "CSV-Nest")
        self.assertEqual(consolidate.intake_row_mode(False, True), "OCL PDF (standard)")
        self.assertIsNone(consolidate.intake_row_mode(False, False))

    def test_both_files_is_ambiguous(self):
        with self.assertRaises(ValueError):
            consolidate.intake_row_mode(True, True)

if __name__ == "__main__":
    unittest.main()
