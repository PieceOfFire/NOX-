"""Domain regressions runnable without opening the application."""

import json
from pathlib import Path
import sys
import unittest
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from formula_document import FormulaDocument
from formula_engine import FormulaEngine
from formula_presets import PresetLibrary


class FormulaTests(unittest.TestCase):
    def fraction(self):
        library = PresetLibrary(Path(__file__).resolve().parent)
        return FormulaDocument.from_preset(library.entries["builtin:fraction"].preset)

    def test_each_spectrum_has_independent_values(self):
        doc = self.fraction()
        values = [{1: 80, 2: 20}, {1: 40, 2: 60}]
        outputs = [
            FormulaEngine(doc.blocks, lambda b, v=v: v.get(b["area_number"])).outputs()[0][2]
            for v in values
        ]
        self.assertEqual([r.value for r in outputs], [20, 60])
        self.assertTrue(all(r.status == "ready" for r in outputs))

    def test_missing_area_and_zero_division_reach_output_as_warnings(self):
        doc = self.fraction()
        result = FormulaEngine(doc.blocks, lambda b: None).outputs()[0][2]
        self.assertEqual(result.value, 0)
        self.assertEqual(result.status, "warning")
        self.assertIn("0", result.message)

    def test_cycle_is_diagnosed(self):
        doc = self.fraction()
        doc.blocks[3]["inputs"][0] = 6
        result = FormulaEngine(doc.blocks, lambda b: 10).outputs()[0][2]
        self.assertIsNone(result.value)
        self.assertEqual(result.status, "error")
        self.assertIn("Цикл", result.message)

    def test_shared_integral_is_evaluated_once(self):
        doc = self.fraction()
        calls = []

        def area(block):
            calls.append(block["area_number"])
            return 20

        engine = FormulaEngine(doc.blocks, area)
        engine.outputs()
        engine.outputs()
        self.assertEqual(sorted(calls), [1, 2])

    def test_named_missing_integral_does_not_rebind_to_another_number(self):
        preset = {
            "format": "nmr-formula-preset",
            "blocks": [{"id": 8, "kind": "area", "area_number": 7, "area_index": 0}],
        }
        doc = FormulaDocument.from_preset(preset, [{"id": 42, "number": 1}])
        self.assertEqual(doc.blocks[8]["area_number"], 7)
        self.assertIsNone(doc.blocks[8]["integral_id"])

    def test_legacy_index_and_frame_ports_survive_json_roundtrip(self):
        preset = {
            "format": "nmr-formula-preset",
            "blocks": [
                {"id": 10, "kind": "area", "area_index": 0, "x": 130, "y": 80},
                {"id": 20, "kind": "operation", "operation": "+", "inputs": [10, 10]},
                {"id": 30, "kind": "output", "input": 20},
            ],
            "frames": [
                {
                    "id": 3,
                    "name": "Группа",
                    "members": [20],
                    "collapsed": True,
                    "inputs": [{"name": "A", "target_id": 20, "index": 0}],
                    "outputs": [{"name": "Сумма", "source_id": 20}],
                }
            ],
            "comments": [{"x": 80, "y": 200, "text": "Пример"}],
            "wire_routes": [{"target_id": 20, "index": 0, "x": 300, "y": 150}],
            "view": {"zoom": 0.4},
        }
        doc = FormulaDocument.from_preset(preset, [{"id": 45, "number": 3}])
        encoded = json.loads(json.dumps(doc.to_preset()))
        restored = FormulaDocument.from_preset(encoded)
        self.assertEqual(restored.blocks[10]["area_number"], 3)
        self.assertEqual(restored.blocks[20]["inputs"], [10, 10])
        self.assertEqual(restored.frames[1]["outputs"][0]["source_id"], 20)
        self.assertEqual(restored.wire_routes, {"20_0": (300, 150)})
        self.assertEqual(restored.zoom, 0.4)
        self.assertEqual(restored.blocks[10]["x"], 130)

    def test_snapshot_is_not_mutated_by_later_edits(self):
        doc = self.fraction()
        snapshot = doc.snapshot()
        doc.blocks[3]["inputs"][0] = None
        doc.restore(snapshot)
        self.assertEqual(doc.blocks[3]["inputs"], [1, 2])

    def test_nonfinite_constants_are_rejected(self):
        with self.assertRaises(ValueError):
            FormulaDocument.from_preset(
                {
                    "format": "nmr-formula-preset",
                    "blocks": [{"id": 1, "kind": "constant", "value": float("inf")}],
                }
            )

    def test_import_is_idempotent_and_survives_library_refresh(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            preset = self.fraction().to_preset()
            source = root / "sample.json"
            source.write_text(json.dumps(preset, separators=(",", ":")), encoding="utf-8")
            library = PresetLibrary(root)
            first = library.register(source, import_copy=True)
            second = library.register(source, import_copy=True)
            self.assertEqual(first.key, second.key)
            self.assertEqual(len(list((root / "presets").glob("*.json"))), 1)
            library.refresh()
            user_entries = [v for v in library.entries.values() if v.path is not None]
            self.assertEqual(len(user_entries), 1)


if __name__ == "__main__":
    unittest.main()
