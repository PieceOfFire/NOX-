"""Reusable calculation library: built-ins plus the user's local JSON presets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from formula_document import FormulaDocument


@dataclass(frozen=True)
class PresetEntry:
    key: str
    name: str
    description: str
    preset: dict
    path: Path | None = None


def _example(name: str, operation: str, percent=False) -> dict:
    blocks = [
        {"id": 1, "kind": "area", "area_number": 1, "x": 80, "y": 100},
        {"id": 2, "kind": "area", "area_number": 2, "x": 80, "y": 220},
        {
            "id": 3,
            "kind": "operation",
            "operation": operation,
            "inputs": [1, 2],
            "x": 250,
            "y": 150,
        },
    ]
    if percent:
        blocks.extend(
            [
                {
                    "id": 4,
                    "kind": "operation",
                    "operation": "÷",
                    "inputs": [2, 3],
                    "x": 400,
                    "y": 150,
                },
                {"id": 5, "kind": "constant", "value": 100, "x": 400, "y": 260},
                {
                    "id": 6,
                    "kind": "operation",
                    "operation": "×",
                    "inputs": [4, 5],
                    "x": 560,
                    "y": 150,
                },
            ]
        )
    blocks.append(
        {
            "id": 7,
            "kind": "output",
            "input": 6 if percent else 3,
            "name": name,
            "auto_name": False,
            "x": 720 if percent else 440,
            "y": 165,
        }
    )
    return {"format": "nmr-formula-preset", "version": 1, "name": name, "blocks": blocks}


class PresetLibrary:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.user_directory = self.directory / "presets"
        self.entries: dict[str, PresetEntry] = {}
        self.refresh()

    def refresh(self) -> None:
        self.entries = {
            "builtin:ratio": PresetEntry(
                "builtin:ratio",
                "Отношение I1 / I2",
                "Отношение физических площадей двух интегралов.",
                _example("I1 / I2", "÷"),
            ),
            "builtin:sum": PresetEntry(
                "builtin:sum",
                "Сумма I1 + I2",
                "Суммарная площадь двух областей.",
                _example("I1 + I2", "+"),
            ),
            "builtin:fraction": PresetEntry(
                "builtin:fraction",
                "Доля I2, %",
                "100 × I2 / (I1 + I2). Интерпретация зависит от назначения интегралов.",
                _example("Доля I2, %", "+", True),
            ),
        }
        paths = sorted(set(self.directory.glob("*.json")) | set(self.user_directory.glob("*.json")))
        for path in paths:
            if path.name.endswith(".nmrproj.json"):
                continue
            try:
                entry = self.register(path)
                duplicates = [
                    k
                    for k, v in self.entries.items()
                    if k != entry.key and v.name == entry.name and v.preset == entry.preset
                ]
                if duplicates:
                    # Prefer the persistent library copy over a loose source.
                    if entry.path.parent != self.user_directory.resolve():
                        self.entries.pop(entry.key)
                    else:
                        for key in duplicates:
                            self.entries.pop(key)
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                # Spectrum projects and unrelated local JSON files are not presets.
                continue

    def register(self, path: str | Path, *, import_copy=False) -> PresetEntry:
        path = Path(path).resolve()
        preset = json.loads(path.read_text(encoding="utf-8-sig"))
        document = FormulaDocument.from_preset(preset)
        if not document.blocks:
            raise ValueError("в формуле нет блоков")
        if import_copy and path.parent != self.user_directory.resolve():
            self.user_directory.mkdir(parents=True, exist_ok=True)
            destination = self.user_directory / path.name
            suffix = 2

            def same_preset(candidate):
                try:
                    return json.loads(candidate.read_text(encoding="utf-8-sig")) == preset
                except (OSError, ValueError):
                    return False

            while destination.exists() and not same_preset(destination):
                destination = self.user_directory / f"{path.stem} ({suffix}).json"
                suffix += 1
            if not destination.exists():
                destination.write_text(
                    json.dumps(preset, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            path = destination.resolve()
        key = str(path)
        name = str(preset.get("name") or path.stem)
        required = ", ".join(f"I{n}" for n in document.area_numbers())
        entry = PresetEntry(
            key, name, f"Пользовательская формула · {required or 'без интегралов'}", preset, path
        )
        if import_copy:
            for old_key, old in list(self.entries.items()):
                if old.name == name and old.preset == preset:
                    self.entries.pop(old_key)
        self.entries[key] = entry
        return entry
