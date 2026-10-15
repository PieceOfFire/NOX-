import numpy as np
from spin_history import WorkspaceHistory, snapshot


def test_checkpoints_isolate_membership_and_share_immutable_fit_arrays():
    model = {"centers": np.array([1.0, 2.0])}
    states = {"s": {"models": [model], "groups": [{"id": "a", "components": [0]}]}}
    saved = snapshot(states, {"fit": {"threshold": 5}}, [{"id": "a", "name": "A"}], None)
    states["s"]["groups"][0]["components"].append(1)
    assert saved["states"]["s"]["groups"][0]["components"] == [0]
    restored = snapshot(saved["states"], saved["settings"], saved["specs"], None)
    restored["states"]["s"]["groups"][0]["missing"] = 1
    assert "missing" not in saved["states"]["s"]["groups"][0]
    assert restored["states"]["s"]["models"][0] is model


def test_history_branch_and_limit():
    history = WorkspaceHistory(limit=3)
    for i in range(5):
        history.push(i)
    assert history.items == [2, 3, 4]
    assert history.undo() == 3
    assert history.undo() == 2
    assert not history.can_undo
    assert history.redo() == 3
    history.push(9)
    assert history.items == [2, 3, 9]
    assert not history.can_redo
