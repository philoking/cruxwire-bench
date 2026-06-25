"""Scoring tests: marks are relational truth, scored off a cluster_id_of map."""

from __future__ import annotations

from bench import scoring
from bench.marks import Mark


def _mark(mid, typ, ids):
    return Mark(mark_id=mid, day="2026-06-24", created_at="t", type=typ, article_ids=ids)


def test_same_satisfied_and_unsatisfied():
    # a,b together; c apart.
    cids = {"a": "C1", "b": "C1", "c": "C2"}
    ok = _mark("m1", "same", ["a", "b"])
    bad = _mark("m2", "same", ["a", "c"])
    s = scoring.score_marks([ok, bad], cids)
    assert s.same_total == 2
    assert s.same_satisfied == 1
    assert s.unsatisfied == ["m2"]


def test_not_same_honored_when_apart():
    cids = {"odd": "C2", "x": "C1", "y": "C1"}
    honored = _mark("m1", "not_same", ["odd", "x", "y"])  # odd kept apart
    s = scoring.score_marks([honored], cids)
    assert s.not_same_total == 1 and s.not_same_honored == 1 and not s.unsatisfied


def test_not_same_violated_when_together():
    cids = {"odd": "C1", "x": "C1"}  # odd wrongly with x
    violated = _mark("m1", "not_same", ["odd", "x"])
    s = scoring.score_marks([violated], cids)
    assert s.not_same_total == 1 and s.not_same_honored == 0 and s.unsatisfied == ["m1"]


def test_not_applicable_when_ids_absent():
    cids = {"a": "C1"}  # b,c not in span
    s = scoring.score_marks([_mark("m1", "same", ["b", "c"])], cids)
    assert s.not_applicable == 1 and s.applicable == 0


def test_partial_presence_same_uses_present_ids():
    # three-way same, only two present -> still judged on the two present.
    cids = {"a": "C1", "b": "C1"}  # c absent
    s = scoring.score_marks([_mark("m1", "same", ["a", "b", "c"])], cids)
    assert s.same_total == 1 and s.same_satisfied == 1


def test_diff_detects_merge_and_split():
    base = {"a": "C1", "b": "C2", "c": "C2"}   # b,c together; a apart
    new = {"a": "C1", "b": "C1", "c": "C9"}    # a,b now together; b,c now apart
    d = scoring.diff_clusterings(base, new)
    assert ("a", "b") in d.newly_merged
    assert ("b", "c") in d.newly_split
    assert d.total_changes == 2
    assert set(d.moved_ids) == {"a", "b", "c"}


def test_collateral_excludes_marked_ids():
    base = {"a": "C1", "b": "C2"}
    new = {"a": "C1", "b": "C1"}   # a,b merged -> both moved
    d = scoring.diff_clusterings(base, new)
    # mark mentions only 'a' -> 'b' is collateral
    marks = [_mark("m1", "same", ["a", "z"])]
    assert scoring.collateral(d, marks) == 1


def test_diff_only_common_ids():
    base = {"a": "C1", "b": "C1"}
    new = {"a": "C1"}  # b dropped from span
    d = scoring.diff_clusterings(base, new)
    assert d.total_changes == 0 and d.moved_ids == []
