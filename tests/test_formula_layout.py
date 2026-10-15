"""Clean changes geometry only, including frames, notes and orthogonal wires."""

import copy
import json
import random
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from formula_document import FormulaDocument
from formula_engine import FormulaEngine
from formula_layout import (
    block_size,
    clean_document,
    connections,
    route_orthogonal,
    segments,
    segment_hits_rect,
    wire_midpoint,
)
from formula_presets import PresetLibrary


def rect(block):
    w, h = block_size(block)
    return block["x"], block["y"], block["x"] + w, block["y"] + h


def overlap(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


class CleanTests(unittest.TestCase):
    def document(self):
        preset = PresetLibrary(Path(__file__).resolve().parent).entries["builtin:fraction"].preset
        doc = FormulaDocument.from_preset(preset)
        for block in doc.blocks.values():
            block.update(x=40, y=40)
        doc.comments[1] = {"x": 40, "y": 40, "text": "Исходные данные"}
        doc.comments[2] = {"x": 40, "y": 40, "text": "Расчёт"}
        return doc

    def test_removes_overlap_and_preserves_calculation_and_links(self):
        doc = self.document()
        before = copy.deepcopy(doc.blocks)
        expected = FormulaEngine(doc.blocks, lambda b: {1: 80, 2: 20}[b["area_number"]]).outputs()
        clean_document(doc)
        for bid in before:
            for field in before[bid].keys() - {"x", "y"}:
                self.assertEqual(before[bid][field], doc.blocks[bid][field], (bid, field))
        actual = FormulaEngine(doc.blocks, lambda b: {1: 80, 2: 20}[b["area_number"]]).outputs()
        self.assertEqual(expected, actual)
        boxes = [rect(b) for b in doc.blocks.values()]
        boxes += [(c["x"], c["y"], c["x"] + 170, c["y"] + 50) for c in doc.comments.values()]
        for i, a in enumerate(boxes):
            for b in boxes[i + 1 :]:
                self.assertFalse(overlap(a, b), (a, b))
        for source, target, _ in connections(doc.blocks):
            self.assertLess(rect(doc.blocks[source])[2], rect(doc.blocks[target])[0])

    def test_layout_is_deterministic_and_repeatable(self):
        doc = self.document()
        other = FormulaDocument()
        other.restore(doc.snapshot())
        clean_document(doc)
        clean_document(other)
        self.assertEqual(doc.snapshot(), other.snapshot())
        first = doc.snapshot()
        clean_document(doc)
        self.assertEqual(first, doc.snapshot())

    def test_disconnected_graph_and_cycle_are_kept(self):
        doc = self.document()
        doc.blocks[3]["inputs"][0] = 6
        doc.blocks[99] = {"kind": "constant", "value": 7, "x": 40, "y": 40}
        links = list(connections(doc.blocks))
        clean_document(doc)
        self.assertEqual(links, list(connections(doc.blocks)))
        boxes = [rect(b) for b in doc.blocks.values()]
        for i, a in enumerate(boxes):
            for b in boxes[i + 1 :]:
                self.assertFalse(overlap(a, b))

    def test_frame_ports_members_and_collapsed_state_are_preserved(self):
        for collapsed in (False, True):
            with self.subTest(collapsed=collapsed):
                doc = self.document()
                frame = {
                    "name": "Доля",
                    "members": [3, 4],
                    "x": 40,
                    "y": 40,
                    "collapsed": collapsed,
                    "inputs": [{"name": "A", "target_id": 3, "index": 0}],
                    "outputs": [{"name": "B", "source_id": 4}],
                }
                doc.frames[1] = copy.deepcopy(frame)
                clean_document(doc)
                for field in frame.keys() - {"x", "y"}:
                    self.assertEqual(frame[field], doc.frames[1][field])
                for bid in frame["members"]:
                    self.assertGreater(doc.blocks[bid]["x"], doc.frames[1]["x"])
                    self.assertGreater(doc.blocks[bid]["y"], doc.frames[1]["y"])
                first = doc.snapshot()
                clean_document(doc)
                self.assertEqual(first, doc.snapshot())

    def test_clean_routes_persist_and_undo_restores_manual_waypoints(self):
        doc = self.document()
        doc.wire_routes["3_0"] = (700, 600)
        previous = doc.snapshot()
        clean_document(doc)
        self.assertTrue(doc.auto_route)
        self.assertFalse(doc.wire_routes)
        restored = FormulaDocument.from_preset(json.loads(json.dumps(doc.to_preset())))
        self.assertTrue(restored.auto_route)
        self.assertEqual(restored.blocks, doc.blocks)
        doc.restore(previous)
        self.assertEqual(doc.snapshot(), previous)

    def test_empty_and_comment_only_document(self):
        doc = FormulaDocument()
        clean_document(doc)
        doc.comments = {i: {"x": 40, "y": 40, "text": str(i)} for i in range(4)}
        clean_document(doc)
        boxes = [(c["x"], c["y"], c["x"] + 170, c["y"] + 50) for c in doc.comments.values()]
        for i, a in enumerate(boxes):
            for b in boxes[i + 1 :]:
                self.assertFalse(overlap(a, b))

    def test_varied_branching_graphs_keep_nodes_separate(self):
        for seed in range(20):
            rng = random.Random(seed)
            doc = FormulaDocument()
            for bid in range(1, 26):
                doc.blocks[bid] = {
                    "kind": "constant" if bid < 6 else "operation",
                    "value": bid,
                    "operation": "+",
                    "x": rng.randrange(500),
                    "y": rng.randrange(400),
                    "inputs": [] if bid < 6 else [rng.randrange(1, bid), rng.randrange(1, bid)],
                }
            clean_document(doc)
            boxes = [rect(b) for b in doc.blocks.values()]
            for i, a in enumerate(boxes):
                for b in boxes[i + 1 :]:
                    self.assertFalse(overlap(a, b), seed)
            for a, b, _ in connections(doc.blocks):
                self.assertLess(rect(doc.blocks[a])[2], rect(doc.blocks[b])[0], seed)


class RoutingTests(unittest.TestCase):
    def check_path(self, points, start, end, obstacles):
        self.assertEqual(tuple(points[:2]), start)
        self.assertEqual(tuple(points[-2:]), end)
        for x1, y1, x2, y2 in segments(points):
            self.assertTrue(x1 == x2 or y1 == y2)
            for obstacle in obstacles:
                self.assertFalse(
                    segment_hits_rect((x1, y1), (x2, y2), obstacle), (points, obstacle)
                )

    def test_route_avoids_obstacle_and_handle_is_on_wire(self):
        start, end = (50, 100), (350, 180)
        obstacles = [(130, 70, 230, 210)]
        path = route_orthogonal(start, end, obstacles)
        self.check_path(path, start, end, obstacles)
        x, y = wire_midpoint(path)
        self.assertTrue(
            any(
                min(a, c) <= x <= max(a, c) and min(b, d) <= y <= max(b, d)
                for a, b, c, d in segments(path)
            )
        )

    def test_multibend_visibility_route_and_backwards_connection(self):
        start, end = (20, 70), (380, 70)
        # No simple three/five-segment route exists through this staggered maze.
        obstacles = [(80, -100, 120, 140), (200, 20, 240, 300), (300, -100, 340, 140)]
        self.check_path(route_orthogonal(start, end, obstacles), start, end, obstacles)
        start, end = (380, 70), (20, 170)
        self.check_path(route_orthogonal(start, end, obstacles), start, end, obstacles)

    def test_dense_ports_and_endpoint_bodies(self):
        doc = CleanTests().document()
        clean_document(doc)
        boxes = {bid: rect(b) for bid, b in doc.blocks.items()}
        occupied = []
        for source, target, index in connections(doc.blocks):
            a, b = boxes[source], boxes[target]
            start = (a[2], (a[1] + a[3]) / 2)
            end = (b[0], b[1] + (22 if doc.blocks[target]["kind"] == "output" else 20 + 24 * index))
            path = route_orthogonal(start, end, list(boxes.values()), occupied)
            self.check_path(
                path, start, end, [box for bid, box in boxes.items() if bid not in (source, target)]
            )
            occupied.extend(segments(path))

    def test_shared_wires_use_different_channels(self):
        start, end = (30, 40), (300, 160)
        first = route_orthogonal(start, end, [])
        second = route_orthogonal(start, end, [], list(segments(first)))
        self.assertNotEqual(first, second)
        self.check_path(second, start, end, [])


if __name__ == "__main__":
    unittest.main()
