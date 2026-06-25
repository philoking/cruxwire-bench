"""Score a clustering against the operator's marks, and diff two clusterings.

Because marks are relations over article ids, the score is computed identically
at any span, parameter set, or embedding model (spec → Re-Run → Score against
the marks). The unit of truth is "are these ids in the same cluster or not",
read off a ``cluster_id_of`` map ({article_id: cluster_id}).

A mark is only scored if enough of its ids are present in the current span; a
mark whose ids aren't present is "not applicable" here, not a failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .marks import Mark


@dataclass
class MarkScore:
    same_total: int = 0          # applicable `same` marks
    same_satisfied: int = 0      # ...now in one cluster
    not_same_total: int = 0      # applicable `not_same` marks
    not_same_honored: int = 0    # ...odd story kept apart from its wrong group
    confirm_total: int = 0
    confirm_held: int = 0
    not_applicable: int = 0      # marks whose ids aren't present enough to judge
    unsatisfied: list[str] = field(default_factory=list)  # mark_ids not met

    @property
    def satisfied(self) -> int:
        return self.same_satisfied + self.not_same_honored + self.confirm_held

    @property
    def applicable(self) -> int:
        return self.same_total + self.not_same_total + self.confirm_total


def _present(ids: list[str], cluster_id_of: dict[str, str]) -> list[str]:
    return [i for i in ids if i in cluster_id_of]


def score_marks(marks: list[Mark], cluster_id_of: dict[str, str]) -> MarkScore:
    """Score a clustering (``cluster_id_of``) against ``marks``."""
    s = MarkScore()
    for m in marks:
        present = _present(m.article_ids, cluster_id_of)
        if m.type == "same":
            if len(present) < 2:
                s.not_applicable += 1
                continue
            s.same_total += 1
            clusters = {cluster_id_of[i] for i in present}
            if len(clusters) == 1:
                s.same_satisfied += 1
            else:
                s.unsatisfied.append(m.mark_id)
        elif m.type == "not_same":
            # ids[0] is the odd story; it must share NO cluster with ids[1:].
            odd = m.article_ids[0]
            others = _present(m.article_ids[1:], cluster_id_of)
            if odd not in cluster_id_of or not others:
                s.not_applicable += 1
                continue
            s.not_same_total += 1
            odd_c = cluster_id_of[odd]
            if all(cluster_id_of[o] != odd_c for o in others):
                s.not_same_honored += 1
            else:
                s.unsatisfied.append(m.mark_id)
        elif m.type == "confirm":
            if len(present) < 2:
                s.not_applicable += 1
                continue
            s.confirm_total += 1
            if len({cluster_id_of[i] for i in present}) == 1:
                s.confirm_held += 1
            else:
                s.unsatisfied.append(m.mark_id)
    return s


@dataclass
class Diff:
    """Membership change between a baseline and a new clustering, over the ids
    present in both. Reported as id-pairs so it is span- and run-stable."""
    newly_merged: list[tuple[str, str]] = field(default_factory=list)   # apart -> together
    newly_split: list[tuple[str, str]] = field(default_factory=list)    # together -> apart
    moved_ids: list[str] = field(default_factory=list)                  # changed cluster-mates

    @property
    def total_changes(self) -> int:
        return len(self.newly_merged) + len(self.newly_split)


def diff_clusterings(baseline: dict[str, str], new: dict[str, str]) -> Diff:
    """Compare two ``cluster_id_of`` maps over their common ids.

    A pair (a,b) is *together* if they share a cluster id. We report pairs that
    flipped together<->apart. O(n^2) over common ids — fine at window scale
    (production never exceeds ~150 stories; see SPEC_REVIEW.md).
    """
    common = sorted(set(baseline) & set(new))
    d = Diff()
    moved = set()
    for x in range(len(common)):
        for y in range(x + 1, len(common)):
            a, b = common[x], common[y]
            was = baseline[a] == baseline[b]
            now = new[a] == new[b]
            if was == now:
                continue
            if now:
                d.newly_merged.append((a, b))
            else:
                d.newly_split.append((a, b))
            moved.add(a)
            moved.add(b)
    d.moved_ids = sorted(moved)
    return d


def collateral(diff: Diff, marks: list[Mark]) -> int:
    """How many changed ids are NOT mentioned by any mark — the unintended
    fallout of a parameter/model change (spec → Re-Run → unmarked collateral)."""
    marked_ids = {i for m in marks for i in m.article_ids}
    return len([i for i in diff.moved_ids if i not in marked_ids])
