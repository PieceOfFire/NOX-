"""Bounded history of accepted workspace edits; fitted arrays are immutable here."""

import copy


def snapshot(states, settings, specs, whole_area_id):
    # Solvers return new states. Only group metadata is edited in place by the UI.
    # Share expensive spectra/models between checkpoints, copy mutable metadata.
    return dict(
        states={
            sid: {**state, "groups": copy.deepcopy(state.get("groups", []))}
            for sid, state in states.items()
        },
        settings=copy.deepcopy(settings),
        specs=copy.deepcopy(specs),
        whole_area_id=whole_area_id,
    )


class WorkspaceHistory:
    def __init__(self, limit=20):
        self.limit = limit
        self.items = []
        self.position = -1

    @property
    def current(self):
        return self.items[self.position] if self.position >= 0 else None

    @property
    def can_undo(self):
        return self.position > 0

    @property
    def can_redo(self):
        return self.position + 1 < len(self.items)

    def push(self, item):
        del self.items[self.position + 1 :]
        self.items.append(item)
        self.items = self.items[-self.limit :]
        self.position = len(self.items) - 1

    def undo(self):
        if self.can_undo:
            self.position -= 1
        return self.current

    def redo(self):
        if self.can_redo:
            self.position += 1
        return self.current
