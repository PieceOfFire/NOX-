import copy
import json
import numpy as np
import pytest
from spin_backend import pack_spin, run_selected_region, unpack_spin
from spin_groups import component_matches, group_area, remap_groups, validate_groups
from test_spin_bridge import record, recipe


def state():
    r = record()
    s = run_selected_region(r, recipe(), 3.4, 3.7)
    s["groups"] = [
        dict(id="a", name="Группа A", components=[0], missing=0),
        dict(id="b", name="Группа B", components=[1], missing=0),
    ]
    return r, s


def test_same_domain_multiple_independent_integrals_and_roundtrip():
    r, s = state()
    assert group_area(r, s, "a") == pytest.approx(np.pi * 72, rel=0.02)
    assert group_area(r, s, "b") == pytest.approx(np.pi * 58.5, rel=0.02)
    restored = unpack_spin(json.loads(json.dumps(pack_spin(s))))
    assert restored["groups"] == s["groups"]
    assert group_area(r, restored, "b") == group_area(r, s, "b")
    r["phase0"] = 10
    assert np.isnan(group_area(r, restored, "a"))


def test_signed_groups_module_missing_and_alignment():
    r, s = state()
    s["groups"] = [dict(id="mix", name="Смесь", components=[0, 1], missing=0)]
    s["models"][0]["amplitudes"][1] *= -1
    signed = group_area(r, s, "mix")
    assert group_area(r, s, "mix", absolute=True) > signed
    r["ppm"] += 0.03
    r["ppm_shift"] += 0.03
    assert group_area(r, s, "mix") == signed
    assert np.isnan(group_area(r, s, "unknown"))
    s["groups"][0]["missing"] = 1
    assert np.isnan(group_area(r, s, "mix"))


def test_remap_is_one_to_one_and_never_swaps_missing_line_for_neighbour():
    r, old = state()
    new = copy.deepcopy(old)
    new["models"][0]["centers"] = np.array([3.5801, 3.7])
    remap_groups(old, new)
    assert new["groups"][0]["components"] == []
    assert new["groups"][0]["missing"] == 1
    assert new["groups"][1]["components"] == [0]
    assert component_matches([0, 1], [1, 100], 0.1) == {1: 0}
    assert np.isnan(group_area(r, new, "a"))


def test_automatic_whole_region_tracks_all_new_lines_without_phantom_losses():
    r, old = state()
    old["groups"] = [
        dict(id="all", name="Вся область", components=[0, 1], missing=2, automatic=True)
    ]
    new = copy.deepcopy(old)
    new["models"][0].update(
        centers=np.array([3.49, 3.55, 3.65]),
        amplitudes=np.array([1000.0, 2000.0, 3000.0]),
        widths=np.full(3, 0.006),
        mixing=np.ones(3),
        picked_centers=np.array([3.49, 3.55, 3.65]),
    )
    remap_groups(old, new)
    assert new["groups"][0]["components"] == [0, 1, 2]
    assert new["groups"][0]["missing"] == 0
    validate_groups(new)
    assert group_area(r, new, "all") == pytest.approx(np.pi * 0.006 * 6000)
    restored = unpack_spin(json.loads(json.dumps(pack_spin(new))))
    assert restored["groups"][0]["automatic"] is True
    assert restored["groups"] == new["groups"]


def test_automatic_remainder_does_not_steal_manually_assigned_components():
    _, old = state()
    old["groups"] = [
        dict(id="all", name="Вся область", components=[1], missing=0, automatic=True),
        dict(id="manual", name="A", components=[0], missing=0),
    ]
    new = copy.deepcopy(old)
    new["models"][0]["centers"] = np.array([3.5001, 3.64])
    remap_groups(old, new)
    assert new["groups"][0]["components"] == [1]
    assert new["groups"][0]["missing"] == 0
    assert new["groups"][1]["components"] == [0]
    validate_groups(new)


@pytest.mark.parametrize("mutation", ["overlap", "outside", "bad_id", "negative_missing"])
def test_saved_group_membership_is_validated(mutation):
    _, s = state()
    if mutation == "overlap":
        s["groups"][1]["components"] = [0]
    elif mutation == "outside":
        s["groups"][0]["components"] = [20]
    elif mutation == "bad_id":
        s["groups"][1]["id"] = "a"
    else:
        s["groups"][0]["missing"] = -1
    with pytest.raises(ValueError):
        validate_groups(s)
    with pytest.raises(ValueError):
        unpack_spin(pack_spin(s))
