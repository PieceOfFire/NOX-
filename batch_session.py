"""Manual-phase series state machine, independent of Tk and spectrum arrays.

The application owns spectrum data/undo. This object owns only queue progress,
the immutable processing recipe, and review diagnostics. Selecting an old item
never reapplies anything; advancing and accepting are deliberately separate.
"""

from __future__ import annotations

import copy
import json

from processing_templates import validate_template


FORMAT = "himera-manual-batch-session"
STATUSES = frozenset({
    "reference", "awaiting_phase", "processing", "review", "confirmed", "skipped", "error",
})
TERMINAL = frozenset({"confirmed", "skipped"})


def _metadata(value):
    """Reject arrays, NaN, custom objects and other nonportable queue state."""
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Метаданные очереди должны быть конечными JSON-значениями.") from error
    return copy.deepcopy(value)


class BatchSession:
    """Queue for operator phase -> background/areas -> explicit review.

    ``begin_processing`` returns a revision token. Pass it to completion/failure
    to reject late worker results after retry, a phase edit or template change.
    Input signatures are optional small JSON metadata, never raw spectrum arrays.
    """

    def __init__(
        self, spectrum_ids, reference_id=None, *, template=None, formula=None, options=None,
    ):
        ids = list(spectrum_ids)
        if not ids or any(type(sid) is not int or sid < 1 for sid in ids) or len(set(ids)) != len(ids):
            raise ValueError("Выберите неповторяющиеся спектры для серии.")
        self.reference_id = ids[0] if reference_id is None else reference_id
        if self.reference_id not in ids:
            raise ValueError("Спектр-образец отсутствует в серии.")
        # The sample is always the first workflow step; the remaining order stays intact.
        ids = [self.reference_id] + [sid for sid in ids if sid != self.reference_id]
        self.items = [dict(
            spectrum_id=sid, status="reference" if sid == self.reference_id else "awaiting_phase",
            phase_confirmed=False, phase_signature=None, revision=0,
            diagnostic={}, warnings=[], blocking_issues=[], error=None,
        ) for sid in ids]
        self.current_index = 0
        self.paused = False
        self.template_revision = 0
        self._template = None
        self._formula = _metadata(formula or {})
        self.options = _metadata({"align": True, **(options or {})})
        # A manual queue must never smuggle automatic/template phase in via options.
        if self.options.get("auto_phase") or self.options.get("phase_mode", "preserve") != "preserve":
            raise ValueError("В этой очереди фаза задаётся только оператором.")
        self.options.pop("auto_phase", None)
        self.options.pop("phase_mode", None)
        if template is not None:
            self.mark_reference_ready(template, formula=formula)

    @property
    def spectrum_ids(self):
        return [item["spectrum_id"] for item in self.items]

    @property
    def current_sid(self):
        return self.current_item["spectrum_id"]

    @property
    def current_item(self):
        return self.items[self.current_index]

    @property
    def template(self):
        return copy.deepcopy(self._template)

    @property
    def formula(self):
        return copy.deepcopy(self._formula)

    @property
    def finished(self):
        return all(item["status"] in TERMINAL for item in self.items)

    def item(self, spectrum_id=None):
        spectrum_id = self.current_sid if spectrum_id is None else spectrum_id
        for item in self.items:
            if item["spectrum_id"] == spectrum_id:
                return item
        raise ValueError("Спектр не входит в текущую очередь.")

    def select(self, spectrum_id):
        self.item(spectrum_id)
        self.current_index = self.spectrum_ids.index(spectrum_id)
        return self.current_item

    def previous(self):
        self.current_index = max(0, self.current_index - 1)
        return self.current_item

    def next(self):
        self.current_index = min(len(self.items) - 1, self.current_index + 1)
        return self.current_item

    def set_template(self, template, *, formula=None):
        """Explicitly update remaining work; already accepted results stay untouched."""
        validated = copy.deepcopy(validate_template(template))
        formula = _metadata(template["formula"] if formula is None else formula)
        self._template, self._formula = validated, formula
        self.template_revision += 1
        for item in self.items:
            if item["spectrum_id"] == self.reference_id or item["status"] in TERMINAL:
                continue
            item.update(status="awaiting_phase", revision=item["revision"] + 1,
                        diagnostic={}, warnings=[], blocking_issues=[], error=None)

    def mark_reference_ready(self, template, *, formula=None):
        self.set_template(template, formula=formula)
        item = self.item(self.reference_id)
        item.update(status="confirmed", phase_confirmed=True, revision=item["revision"] + 1,
                    error=None, warnings=[], blocking_issues=[])
        return item

    def mark_phase_done(self, spectrum_id=None, *, signature=None):
        item = self.item(spectrum_id)
        if item["spectrum_id"] == self.reference_id:
            raise ValueError("Для образца сначала сохраните шаблон обработки.")
        item.update(status="awaiting_phase", phase_confirmed=True,
                    phase_signature=_metadata(signature), revision=item["revision"] + 1,
                    diagnostic={}, warnings=[], blocking_issues=[], error=None)
        return item

    def phase_changed(self, spectrum_id=None):
        """An intentional phase edit invalidates pending work/review, not raw data."""
        item = self.item(spectrum_id)
        if item["spectrum_id"] != self.reference_id:
            item.update(status="awaiting_phase", phase_confirmed=False,
                        phase_signature=None, revision=item["revision"] + 1,
                        diagnostic={}, warnings=[], blocking_issues=[], error=None)
        return item

    def begin_processing(self, spectrum_id=None):
        item = self.item(spectrum_id)
        if self.paused:
            raise ValueError("Очередь приостановлена.")
        if self._template is None:
            raise ValueError("Сначала обработайте образец и сохраните шаблон.")
        if item["spectrum_id"] == self.reference_id:
            raise ValueError("Шаблон не применяется автоматически к образцу.")
        if not item["phase_confirmed"]:
            raise ValueError("Сначала оператор должен подтвердить фазу спектра.")
        if item["status"] in TERMINAL or item["status"] == "processing":
            raise ValueError("Для повторной обработки сначала выберите «Повторить».")
        item.update(status="processing", revision=item["revision"] + 1,
                    diagnostic={}, warnings=[], blocking_issues=[], error=None)
        return item["revision"]

    def _worker_current(self, item, revision):
        return item["status"] == "processing" and (
            revision is None or revision == item["revision"]
        )

    def complete_processing(self, spectrum_id, diagnostic=None, *, revision=None):
        item = self.item(spectrum_id)
        if not self._worker_current(item, revision):
            return False
        self.mark_review(spectrum_id, diagnostic)
        return True

    def mark_review(self, spectrum_id=None, diagnostic=None, *, blocking_issues=None):
        """Record operator-edited results without changing processing/template parameters."""
        item = self.item(spectrum_id)
        if not item["phase_confirmed"]:
            raise ValueError("Перед проверкой результата подтвердите ручную фазу.")
        diagnostic = _metadata(diagnostic or {})
        blockers = list(
            diagnostic.get("blocking_issues", []) if blocking_issues is None else blocking_issues
        )
        if diagnostic.get("incomplete_fits"):
            blockers.append("В Spintegrate потеряны линии; проверьте состав интегралов.")
        item.update(status="review", revision=item["revision"] + 1,
                    diagnostic=diagnostic, warnings=list(diagnostic.get("warnings", [])),
                    blocking_issues=_metadata(blockers), error=None)
        return item

    def fail_processing(self, spectrum_id, error, *, revision=None):
        item = self.item(spectrum_id)
        if not self._worker_current(item, revision):
            return False
        item.update(status="error", error=str(error), revision=item["revision"] + 1)
        return True

    def confirm(self, spectrum_id=None):
        item = self.item(spectrum_id)
        if item["status"] not in ("review", "confirmed"):
            raise ValueError("Подтвердить можно только обработанный и проверенный спектр.")
        if item["blocking_issues"]:
            raise ValueError("Исправьте проблемы перед подтверждением: " + "; ".join(item["blocking_issues"]))
        item.update(status="confirmed", revision=item["revision"] + 1, error=None)
        return item

    def skip(self, spectrum_id=None):
        item = self.item(spectrum_id)
        if item["spectrum_id"] == self.reference_id and self._template is None:
            raise ValueError("Нельзя пропустить образец до создания шаблона.")
        item.update(status="skipped", revision=item["revision"] + 1)
        return item

    def retry(self, spectrum_id=None):
        item = self.item(spectrum_id)
        if item["spectrum_id"] == self.reference_id:
            item.update(status="reference", revision=item["revision"] + 1)
        else:
            item.update(status="awaiting_phase", revision=item["revision"] + 1,
                        diagnostic={}, warnings=[], blocking_issues=[], error=None)
        return item

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def to_payload(self, id_map=None, *, include_template=True):
        """Store IDs through a project index map; never store spectrum snapshots.

        The optional template contains its existing compact alignment reference,
        not raw/processed spectrum arrays. Omit it only if stored separately.
        """
        mapping = {sid: sid for sid in self.spectrum_ids} if id_map is None else id_map
        if not isinstance(mapping, dict) or any(sid not in mapping for sid in self.spectrum_ids):
            raise ValueError("Не все спектры очереди входят в сохраняемый проект.")
        mapped = [mapping[sid] for sid in self.spectrum_ids]
        if any(type(sid) is not int or sid < 0 for sid in mapped) or len(set(mapped)) != len(mapped):
            raise ValueError("Некорректное соответствие спектров очереди проекту.")
        items = copy.deepcopy(self.items)
        for item in items:
            item["spectrum_id"] = mapping[item["spectrum_id"]]
        return _metadata(dict(
            format=FORMAT, version=1, reference_id=mapping[self.reference_id],
            current_id=mapping[self.current_sid], items=items, paused=self.paused,
            template=self.template if include_template else None,
            template_revision=self.template_revision, formula=self.formula, options=self.options,
        ))

    @classmethod
    def from_payload(cls, payload, id_map=None, *, template=None):
        payload = _metadata(payload)
        if (
            not isinstance(payload, dict) or payload.get("format") != FORMAT
            or type(payload.get("version")) is not int or payload["version"] != 1
        ):
            raise ValueError("Неизвестный формат очереди обработки.")
        try:
            raw_items = payload["items"]
            if not isinstance(raw_items, list) or not raw_items or any(
                not isinstance(item, dict) for item in raw_items
            ):
                raise ValueError("В очереди должен быть список спектров.")
            ids = [item["spectrum_id"] for item in raw_items]
            if any(type(sid) is not int or sid < 0 for sid in ids) or len(set(ids)) != len(ids):
                raise ValueError("Некорректные идентификаторы спектров очереди.")
            mapping = {sid: sid for sid in ids} if id_map is None else id_map
            if not isinstance(mapping, dict) or any(sid not in mapping for sid in ids):
                raise ValueError("Спектры очереди отсутствуют в проекте.")
            if any(type(payload[key]) is not int for key in ("reference_id", "current_id")):
                raise ValueError("Некорректный выбранный спектр очереди.")
            if payload["reference_id"] not in ids or payload["current_id"] not in ids:
                raise ValueError("Выбранный спектр отсутствует в очереди.")
            if not isinstance(payload.get("formula", {}), dict) or not isinstance(payload.get("options", {}), dict):
                raise ValueError("Некорректные настройки очереди.")
            if type(payload.get("paused", False)) is not bool:
                raise ValueError("Некорректный статус паузы очереди.")
            session = cls([mapping[sid] for sid in ids], mapping[payload["reference_id"]],
                          formula=payload.get("formula"), options=payload.get("options"))
            saved_template = payload.get("template") if template is None else template
            if saved_template is not None:
                session._template = copy.deepcopy(validate_template(saved_template))
            elif any(item.get("status") in ("review", "confirmed", "processing") for item in raw_items):
                raise ValueError("В обработанной очереди отсутствует шаблон обработки.")
            session.template_revision = payload.get("template_revision", 0)
            if type(session.template_revision) is not int or session.template_revision < 0:
                raise ValueError("Некорректная версия шаблона.")
            for raw in raw_items:
                item = session.item(mapping[raw["spectrum_id"]])
                if (
                    not isinstance(raw["status"], str) or raw["status"] not in STATUSES
                    or type(raw["revision"]) is not int or raw["revision"] < 0
                ):
                    raise ValueError("Некорректное состояние очереди.")
                if type(raw["phase_confirmed"]) is not bool:
                    raise ValueError("Некорректный статус ручной фазы.")
                if (
                    raw["status"] in ("review", "confirmed", "processing")
                    and not raw["phase_confirmed"]
                ):
                    raise ValueError("Обработанный спектр должен иметь подтверждённую ручную фазу.")
                if raw["status"] == "reference" and mapping[raw["spectrum_id"]] != session.reference_id:
                    raise ValueError("В очереди должен быть один образец.")
                if not isinstance(raw.get("diagnostic", {}), dict):
                    raise ValueError("Некорректная диагностика очереди.")
                if any(
                    not isinstance(raw.get(key, []), list)
                    or any(not isinstance(message, str) for message in raw.get(key, []))
                    for key in ("warnings", "blocking_issues")
                ):
                    raise ValueError("Некорректный список предупреждений очереди.")
                if raw.get("error") is not None and not isinstance(raw["error"], str):
                    raise ValueError("Некорректное описание ошибки очереди.")
                item.update(copy.deepcopy(raw), spectrum_id=mapping[raw["spectrum_id"]])
                if item["status"] == "processing":
                    # The worker does not survive reload. Never accept partial output.
                    item.update(status="awaiting_phase", phase_confirmed=False,
                                phase_signature=None, revision=item["revision"] + 1,
                                error=None, diagnostic={}, blocking_issues=[],
                                warnings=["Обработка была прервана; проверьте фазу и повторите применение."])
            session.select(mapping[payload["current_id"]])
            session.paused = payload.get("paused", False)
            return session
        except (KeyError, TypeError, OverflowError) as error:
            raise ValueError("Неполная или повреждённая очередь обработки.") from error
