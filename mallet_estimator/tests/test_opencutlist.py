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

    def test_station_flags(self):
        # Part 1 is edge-banded (not laminated); part 2 is laminated (not edged);
        # every sheet part is cut. These drive the Job Card part list per station.
        parts = OCL.parts_list(OCL.parse_opencutlist_csv(CSV))
        self.assertEqual((parts[0]["cut"], parts[0]["edge_banded"], parts[0]["laminated"]), (1, 1, 0))
        self.assertEqual((parts[1]["cut"], parts[1]["edge_banded"], parts[1]["laminated"]), (1, 0, 1))


# Hardware rows: Material name is the coarse category; Designation is the real
# SKU; OpenCutList suffixes duplicate instances with #N. Two hinges (one #1) roll
# up to one SKU qty 2; two handle designations under one category stay distinct.
HW_CSV = """No.;Material name;Material type;Designation;Length;Width;Thickness
1;HWD_Hinge;Hardware;HWD_AH_SC_0;80 mm;65 mm;42 mm
2;HWD_Hinge;Hardware;HWD_AH_SC_0#1;80 mm;65 mm;42 mm
3;HWD_Handle;Hardware;HWD_Handle_150mm;86 mm;32 mm;22 mm
4;HWD_Handle;Hardware;HWD_HandleDrawer_150mm;152 mm;32 mm;22 mm
5;SG_PLY_V0_a_a;Sheet Goods;panel;600;400;16
"""


class TestHardwareList(unittest.TestCase):
    def test_canonical_strips_instance_suffix(self):
        self.assertEqual(OCL.canonical_hw_code("HWD_AH_SC_0#3"), "HWD_AH_SC_0")
        self.assertEqual(OCL.canonical_hw_code("HWD_AH_SC_0"), "HWD_AH_SC_0")

    def test_hardware_by_designation_with_dims(self):
        hw = OCL.hardware_list(OCL.parse_opencutlist_csv(HW_CSV))
        by = {h["code"]: h for h in hw}
        self.assertEqual(set(by), {"HWD_AH_SC_0", "HWD_Handle_150mm", "HWD_HandleDrawer_150mm"})
        self.assertEqual(by["HWD_AH_SC_0"]["qty"], 2)          # #1 instance rolled up
        self.assertEqual(by["HWD_AH_SC_0"]["category"], "HWD_Hinge")
        self.assertEqual(
            (by["HWD_AH_SC_0"]["length"], by["HWD_AH_SC_0"]["width"], by["HWD_AH_SC_0"]["thickness"]),
            (80.0, 65.0, 42.0),
        )
        # one Material name (HWD_Handle) resolves to two distinct SKUs
        self.assertEqual(by["HWD_Handle_150mm"]["qty"], 1)
        self.assertEqual(by["HWD_HandleDrawer_150mm"]["qty"], 1)
        # sheet-goods rows are ignored here
        self.assertNotIn("panel", by)


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



class TestHardwareDesignationColumns(unittest.TestCase):
    """Which column carries a part's own name depends on how the OpenCutList
    export is configured. Reading only 'Designation' made every hardware line
    fall back to its CATEGORY (HWD_Hinge instead of HWD_AH_SC_0), which hides
    several SKUs at different rates behind one code."""

    def _rows(self, column):
        return [
            {"Material type": "Hardware", "Material name": "HWD_Hinge", column: "HWD_AH_SC_0#1"},
            {"Material type": "Hardware", "Material name": "HWD_Hinge", column: "HWD_AH_SC_0#2"},
        ]

    def test_every_designation_column_is_tried(self):
        for column in OCL.DESIGNATION_COLUMNS:
            hw = OCL.hardware_list(self._rows(column))
            self.assertEqual([h["code"] for h in hw], ["HWD_AH_SC_0"], column)
            self.assertEqual(hw[0]["qty"], 2, column)
            self.assertEqual(hw[0]["category"], "HWD_Hinge", column)
            self.assertTrue(hw[0]["named"], column)

    def test_a_csv_with_no_designation_is_flagged_not_faked(self):
        hw = OCL.hardware_list([{"Material type": "Hardware", "Material name": "HWD_Handle"}])
        self.assertEqual(hw[0]["code"], "HWD_Handle")
        self.assertFalse(hw[0]["named"])   # caller warns instead of pretending

    def test_a_designation_echoing_the_category_is_not_a_name(self):
        hw = OCL.hardware_list([{"Material type": "Hardware", "Material name": "HWD_Hinge",
                                 "Designation": "HWD_Hinge#1"}])
        self.assertFalse(hw[0]["named"])


if __name__ == "__main__":
    unittest.main()


class TestCsvHardwareDesignations(unittest.TestCase):
    """CSV-Nest must aggregate hardware by DESIGNATION (HWD_AH_SC_0), never by
    the coarse Material name (HWD_Hinge) — several distinct SKUs at different
    rates can hide inside one category."""

    CSV = (
        "Material type;Material name;Designation;Length;Width;Thickness\n"
        "Hardware;HWD_Hinge;HWD_AH_SC_0;0;0;0\n"
        "Hardware;HWD_Hinge;HWD_AH_SC_0#2;0;0;0\n"
        "Hardware;HWD_Hinge;HWD_AH_SC_165;0;0;0\n"
        "Hardware;HWD_Handle;HWD_HDL_128;0;0;0\n"
    )

    def test_designations_not_categories(self):
        rows = OCL.parse_opencutlist_csv(self.CSV)
        hw = OCL.hardware_list(rows)
        by_code = {h["code"]: h for h in hw}
        self.assertIn("HWD_AH_SC_0", by_code)
        self.assertIn("HWD_AH_SC_165", by_code)
        self.assertNotIn("HWD_Hinge", by_code)      # the category is never a line
        self.assertEqual(by_code["HWD_AH_SC_0"]["qty"], 2)   # '#2' rolls up
        self.assertEqual(by_code["HWD_AH_SC_0"]["category"], "HWD_Hinge")

    def test_category_still_classifies_the_driver(self):
        # the operation driver must still see these as hinges/handles
        self.assertEqual(OCL.classify_hardware("HWD_AH_SC_0 · HWD_Hinge"), "hinges")
        self.assertEqual(OCL.classify_hardware("HWD_HDL_128 · HWD_Handle"), "handles")
        self.assertEqual(OCL.classify_hardware("HWD_AH_SC_0"), "other")
