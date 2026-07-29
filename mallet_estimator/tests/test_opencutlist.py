# Pure unit tests for the OpenCutList CSV parser + aggregator — no database.
#   python -m unittest mallet_estimator.tests.test_opencutlist
import unittest

from mallet_estimator import opencutlist as OCL

# A tiny semicolon-delimited OpenCutList "parts" export: 2 sheet parts (one edged
# on all four sides), plus a hardware row — enough to exercise every aggregator path.
CSV = """No.;Material name;Material type;Length;Width;Thickness;Area - final;Edge Length 1;Edge Length 2;Edge Width 1;Edge Width 2;Frontside;Backside;Tag
1;SG_PLY_V0_a_a;Sheet Goods;600;400;16;0.24;EB_PVC_IN_a (1 mm x 22 mm);;EB_PVC_IN_a (1 mm x 22 mm);;;;shelf
2;SG_PLY_V0_a_a;Sheet Goods;800;500;16;0.40;;;;;SG_LAM_V0_a_a;;door
3;HWD_Hinge;Hardware;;;;;;;;;;;
"""


class TestParse(unittest.TestCase):
    def test_rows_parsed(self):
        rows = OCL.parse_opencutlist_csv(CSV)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["Material name"], "SG_PLY_V0_a_a")

    def test_material_from_strips_spec(self):
        self.assertEqual(OCL._material_from("EB_PVC_IN_a (1 mm x 22 mm)"), "EB_PVC_IN_a")
        self.assertIsNone(OCL._material_from(""))


class TestPartsList(unittest.TestCase):
    def test_only_sheet_goods_with_part_no(self):
        parts = OCL.parts_list(OCL.parse_opencutlist_csv(CSV))
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0]["part_no"], "1")
        self.assertEqual(parts[0]["thickness"], 16)


class TestAggregate(unittest.TestCase):
    def setUp(self):
        self.agg = OCL.aggregate(OCL.parse_opencutlist_csv(CSV))

    def test_sheet_line_present(self):
        sheets = [l for l in self.agg["lines"] if l["kind"] == "sheet"]
        self.assertEqual(len(sheets), 1)
        self.assertEqual(sheets[0]["material"], "SG_PLY_V0_a_a")

    def test_edge_measured_in_metres(self):
        edge = [l for l in self.agg["lines"] if l["kind"] == "edge"]
        self.assertTrue(edge, "expected an edge-banding line")
        self.assertEqual(edge[0]["uom"], "Meter")
        self.assertGreater(edge[0]["qty"], 0)

    def test_hardware_counted(self):
        self.assertEqual(self.agg["drivers"]["hinges"], 1)

    def test_panels_and_edge_parts(self):
        self.assertEqual(self.agg["drivers"]["panels"], 2)
        self.assertEqual(self.agg["drivers"]["edge_parts"], 1)  # only part 1 is edged


class TestClassifyHardware(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(OCL.classify_hardware("HWD_Hinge"), "hinges")
        self.assertEqual(OCL.classify_hardware("HWD_MiniFix"), "minifix")
        self.assertEqual(OCL.classify_hardware("HWD_Screw"), "screws")


if __name__ == "__main__":
    unittest.main()
