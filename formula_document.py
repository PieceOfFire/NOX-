"""Persistent graph/document state. No UI widgets or numerical spectrum arrays."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from formula_engine import OPERATIONS, UNARY_OPERATIONS


@dataclass
class FormulaDocument:
    blocks: dict[int, dict] = field(default_factory=dict)
    frames: dict[int, dict] = field(default_factory=dict)
    comments: dict[int, dict] = field(default_factory=dict)
    wire_routes: dict[str, tuple[float, float]] = field(default_factory=dict)
    next_block_id: int = 1
    next_frame_id: int = 1
    next_comment_id: int = 1
    name: str = "Новый расчёт"
    zoom: float = 1.0
    auto_route: bool = False

    def snapshot(self) -> dict:
        return copy.deepcopy(vars(self))

    def restore(self, state: dict) -> None:
        for name in self.__dataclass_fields__:
            if name in state:
                setattr(self, name, copy.deepcopy(state[name]))

    def clear(self) -> None:
        self.restore(FormulaDocument().snapshot())

    def area_numbers(self) -> list[int]:
        return sorted(
            {
                int(b["area_number"])
                for b in self.blocks.values()
                if b.get("kind") == "area" and isinstance(b.get("area_number"), int)
            }
        )

    def to_preset(self, integrals: list[dict] = ()) -> dict:
        positions = {int(item["id"]): i for i, item in enumerate(integrals)}
        blocks = []
        for bid, block in self.blocks.items():
            saved = copy.deepcopy(block)
            saved["id"] = bid
            if saved.get("kind") == "area":
                saved["area_index"] = positions.get(saved.get("integral_id"))
                saved["comparison_id"] = None
            saved.pop("integral_id", None)
            blocks.append(saved)
        routes = []
        for key, (x, y) in self.wire_routes.items():
            target, index = key.rsplit("_", 1)
            routes.append({"target_id": int(target), "index": int(index), "x": x, "y": y})
        return {
            "format": "nmr-formula-preset",
            "version": 1,
            "name": self.name,
            "blocks": blocks,
            "frames": [{"id": fid, **copy.deepcopy(f)} for fid, f in self.frames.items()],
            "comments": list(copy.deepcopy(self.comments).values()),
            "wire_routes": routes,
            "view": {"zoom": self.zoom, "auto_route": self.auto_route},
        }

    @classmethod
    def from_preset(
        cls, preset: object, integrals: list[dict] = (), comparison_id: int | None = None
    ) -> FormulaDocument:
        if not isinstance(preset, dict) or preset.get("format") != "nmr-formula-preset":
            raise ValueError("это не пресет формул NOX")
        if not isinstance(preset.get("blocks"), list):
            raise ValueError("в пресете нет списка блоков")
        doc = cls(name=str(preset.get("name") or "Расчёт"))

        def number(value, default=40.0):
            result = float(default if value is None else value)
            if not math.isfinite(result):
                raise ValueError("координаты и константы должны быть конечными числами")
            return result

        for raw in preset["blocks"]:
            if not isinstance(raw, dict) or raw.get("kind") not in {
                "area",
                "constant",
                "operation",
                "output",
            }:
                raise ValueError("пресет содержит неизвестный тип блока")
            bid = int(raw["id"])
            if bid in doc.blocks:
                raise ValueError("в пресете повторяются номера блоков")
            kind, op = raw["kind"], raw.get("operation")
            if kind == "operation" and op not in OPERATIONS:
                raise ValueError(f"неизвестная операция: {op}")
            count = 1 if op in UNARY_OPERATIONS else 2 if kind == "operation" else 0
            area_number = raw.get("area_number")
            if kind == "area" and not isinstance(area_number, int):
                index = raw.get("area_index")
                if isinstance(index, int) and index >= 0:
                    area_number = (
                        int(integrals[index].get("number", index + 1))
                        if index < len(integrals)
                        else index + 1
                    )
                else:
                    raise ValueError("для источника площади не указан номер интеграла")
            item = next((i for i in integrals if i.get("number") == area_number), None)
            inputs = raw.get("inputs") or []
            doc.blocks[bid] = {
                "kind": kind,
                "x": number(raw.get("x")),
                "y": number(raw.get("y")),
                "operation": op if kind == "operation" else None,
                "value": number(raw.get("value"), 0) if kind == "constant" else None,
                "inputs": list(inputs[:count]) + [None] * max(0, count - len(inputs)),
                "input": raw.get("input") if kind == "output" else None,
                "name": str(raw.get("name") or ""),
                "auto_name": bool(raw.get("auto_name", True)),
                "area_number": area_number if kind == "area" else None,
                "integral_id": item["id"] if kind == "area" and item else None,
                "comparison_id": comparison_id if kind == "area" else None,
            }
        # Empty workspaces are valid in saved analysis projects.
        for block in doc.blocks.values():
            block["inputs"] = [
                i if isinstance(i, int) and i in doc.blocks else None for i in block["inputs"]
            ]
            if block.get("input") not in doc.blocks:
                block["input"] = None
        for index, block in enumerate(
            (b for b in doc.blocks.values() if b["kind"] == "output"), start=1
        ):
            if block.get("auto_name", True):
                block["name"] = "Вывод" if index == 1 else f"Вывод {index}"
        for raw in preset.get("comments", []):
            if isinstance(raw, dict) and str(raw.get("text", "")).strip():
                doc.comments[doc.next_comment_id] = {
                    "x": number(raw.get("x")),
                    "y": number(raw.get("y")),
                    "text": str(raw["text"]),
                }
                doc.next_comment_id += 1
        for raw in preset.get("wire_routes", []):
            if not isinstance(raw, dict):
                continue
            bid, index = int(raw["target_id"]), int(raw["index"])
            block = doc.blocks.get(bid)
            count = (
                len(block["inputs"])
                if block and block["kind"] == "operation"
                else 1
                if block and block["kind"] == "output"
                else 0
            )
            if 0 <= index < count:
                doc.wire_routes[f"{bid}_{index}"] = (number(raw.get("x")), number(raw.get("y")))
        for raw in preset.get("frames", []):
            if not isinstance(raw, dict):
                continue
            members = [i for i in raw.get("members", []) if isinstance(i, int) and i in doc.blocks]
            if not members:
                continue
            inputs, outputs = [], []
            for port in raw.get("inputs", []):
                if not isinstance(port, dict):
                    continue
                bid, index = port.get("target_id"), port.get("index")
                block = doc.blocks.get(bid)
                count = (
                    len(block["inputs"])
                    if block and block["kind"] == "operation"
                    else 1
                    if block and block["kind"] == "output"
                    else 0
                )
                if isinstance(index, int) and 0 <= index < count:
                    inputs.append(
                        {
                            "name": str(port.get("name") or f"Вход {len(inputs) + 1}"),
                            "target_id": bid,
                            "index": index,
                        }
                    )
            for port in raw.get("outputs", []):
                if not isinstance(port, dict):
                    continue
                bid = port.get("source_id")
                if bid in doc.blocks and doc.blocks[bid]["kind"] != "output":
                    outputs.append(
                        {
                            "name": str(port.get("name") or f"Выход {len(outputs) + 1}"),
                            "source_id": bid,
                        }
                    )
            fid = doc.next_frame_id
            doc.frames[fid] = {
                "name": str(raw.get("name") or "Фрейм"),
                "members": members,
                "collapsed": bool(raw.get("collapsed")),
                "x": number(raw.get("x")),
                "y": number(raw.get("y")),
                "inputs": inputs,
                "outputs": outputs,
            }
            doc.next_frame_id += 1
        doc.next_block_id = max(doc.blocks, default=0) + 1
        view = preset.get("view", {})
        if isinstance(view, dict):
            doc.zoom = max(0.2, min(2.5, number(view.get("zoom"), 1)))
            doc.auto_route = bool(view.get("auto_route", False))
        return doc
