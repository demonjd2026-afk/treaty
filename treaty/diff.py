"""Deterministic comparison of two versions of a contract.

Every rule here is written from the consumer's point of view: a change is
breaking when a query that worked against the old contract can fail, or
silently return different results, against the new one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .contract import Column, ColumnType, Contract

BREAKING, WARNING, SAFE = "breaking", "warning", "safe"
_RANK = {SAFE: 0, WARNING: 1, BREAKING: 2}
_INT_ORDER = ["smallint", "int", "bigint"]


@dataclass
class Change:
    kind: str
    severity: str
    column: str | None
    message: str
    detail: dict = field(default_factory=dict)


@dataclass
class DiffResult:
    base: Contract
    head: Contract
    changes: list[Change]
    renames: dict[str, str]  # old column -> likely new column

    @property
    def verdict(self) -> str:
        if not self.changes:
            return SAFE
        return max((c.severity for c in self.changes), key=_RANK.__getitem__)

    @property
    def required_bump(self) -> str:
        if self.verdict == BREAKING:
            return "major"
        if self.changes:
            return "minor"
        return "patch"

    @property
    def declared_bump(self) -> str:
        b, h = self.base.version, self.head.version
        if h[0] > b[0]:
            return "major"
        if h[0] == b[0] and h[1] > b[1]:
            return "minor"
        if h[:2] == b[:2] and h[2] > b[2]:
            return "patch"
        return "none"

    @property
    def version_ok(self) -> bool:
        order = ["none", "patch", "minor", "major"]
        declared, required = self.declared_bump, self.required_bump
        if not self.changes:
            return self.head.version >= self.base.version
        if self.head.version <= self.base.version:
            return False
        return order.index(declared) >= order.index(required)

    def touched_columns(self, min_severity: str = WARNING) -> dict[str, list[Change]]:
        out: dict[str, list[Change]] = {}
        for c in self.changes:
            if c.column and _RANK[c.severity] >= _RANK[min_severity]:
                out.setdefault(c.column, []).append(c)
        return out


def is_widening(old: ColumnType, new: ColumnType) -> bool:
    """True when every value of `old` is representable in `new` unchanged."""
    if old == new:
        return True
    if old.base in _INT_ORDER and new.base in _INT_ORDER:
        return _INT_ORDER.index(new.base) >= _INT_ORDER.index(old.base)
    if old.base in _INT_ORDER and new.base == "decimal" and new.p1 is not None:
        digits = {"smallint": 5, "int": 10, "bigint": 19}[old.base]
        return new.p1 - (new.p2 or 0) >= digits
    if old.base == "float" and new.base == "double":
        return True
    if old.base == "decimal" and new.base == "decimal":
        op, os_ = old.p1 or 10, old.p2 or 0
        np_, ns = new.p1 or 10, new.p2 or 0
        return ns >= os_ and (np_ - ns) >= (op - os_)
    if old.base in {"varchar", "char"} and new.base == "string":
        return True
    if old.base == "varchar" and new.base == "varchar":
        return (new.p1 or 0) >= (old.p1 or 0)
    return False


def _meaning_changed(old: Column, new: Column) -> bool:
    norm = lambda s: " ".join(s.lower().split()).rstrip(".")  # noqa: E731
    return bool(old.description and new.description) and norm(old.description) != norm(new.description)


def _rename_candidates(removed: list[Column], added: list[Column]) -> dict[str, str]:
    """Pair a removed column with an added one when exactly one added column
    shares its type family. Ambiguous cases are left unpaired on purpose."""
    pairs: dict[str, str] = {}
    used: set[str] = set()
    for old in removed:
        options = [a for a in added if a.name not in used and a.type.family() == old.type.family()]
        if len(options) == 1:
            pairs[old.name] = options[0].name
            used.add(options[0].name)
    return pairs


def diff_contracts(base: Contract, head: Contract) -> DiffResult:
    changes: list[Change] = []

    if base.name != head.name:
        changes.append(Change("contract_renamed", BREAKING, None,
                              f"contract renamed from `{base.name}` to `{head.name}`"))
    if base.dataset != head.dataset:
        changes.append(Change("dataset_moved", BREAKING, None,
                              f"dataset moved from `{base.dataset}` to `{head.dataset}`; every query reads the old name"))

    removed = [c for n, c in base.columns.items() if n not in head.columns]
    added = [c for n, c in head.columns.items() if n not in base.columns]
    renames = _rename_candidates(removed, added)

    for col in removed:
        if col.name in renames:
            new = head.columns[renames[col.name]]
            msg = f"`{col.name}` removed; looks like a rename to `{new.name}`"
            if new.type != col.type:
                msg += f" with type `{col.type}` → `{new.type}`"
        else:
            msg = f"`{col.name}` removed with no replacement"
        changes.append(Change("column_removed", BREAKING, col.name, msg,
                              {"renamed_to": renames.get(col.name)}))
        if col.name in renames:
            new = head.columns[renames[col.name]]
            if _meaning_changed(col, new):
                changes.append(Change("meaning_changed", WARNING, col.name,
                                      f"`{new.name}` is described differently from `{col.name}` "
                                      f"(\"{col.description}\" → \"{new.description}\"); "
                                      f"a shim would keep the name but not the meaning",
                                      {"renamed_to": new.name}))

    for col in added:
        if col.name in renames.values():
            continue  # reported with its removal
        note = "" if col.nullable else " (non-null: producer must backfill history)"
        changes.append(Change("column_added", SAFE, col.name, f"`{col.name}` added as `{col.type}`{note}"))

    for name, old in base.columns.items():
        new = head.columns.get(name)
        if new is None:
            continue
        if _meaning_changed(old, new):
            changes.append(Change("meaning_changed", WARNING, name,
                                  f"`{name}` description changed (\"{old.description}\" → "
                                  f"\"{new.description}\"); check the values still mean the same"))
        if old.type != new.type:
            if is_widening(old.type, new.type):
                changes.append(Change("type_widened", SAFE, name,
                                      f"`{name}` widened `{old.type}` → `{new.type}`"))
            else:
                changes.append(Change("type_changed", BREAKING, name,
                                      f"`{name}` type `{old.type}` → `{new.type}` can truncate or fail casts"))
        if not old.nullable and new.nullable:
            changes.append(Change("nullability_relaxed", BREAKING, name,
                                  f"`{name}` can now be null; sums, joins and filters may change silently"))
        elif old.nullable and not new.nullable:
            changes.append(Change("nullability_tightened", SAFE, name, f"`{name}` is now guaranteed non-null"))
        if old.primary_key != new.primary_key:
            changes.append(Change("primary_key_changed", BREAKING, name,
                                  f"`{name}` {'is no longer' if old.primary_key else 'is now'} part of the primary key; row grain changes"))
        if old.enum and new.enum:
            gone = [v for v in old.enum if v not in new.enum]
            extra = [v for v in new.enum if v not in old.enum]
            if gone:
                changes.append(Change("enum_value_removed", BREAKING, name,
                                      f"`{name}` drops value(s) {', '.join(repr(v) for v in gone)}",
                                      {"values": gone}))
            if extra:
                changes.append(Change("enum_value_added", WARNING, name,
                                      f"`{name}` gains value(s) {', '.join(repr(v) for v in extra)}; exhaustive CASE logic may miss them",
                                      {"values": extra}))
        elif old.enum and not new.enum:
            changes.append(Change("enum_dropped", WARNING, name,
                                  f"`{name}` is no longer limited to {len(old.enum)} known values"))

    if base.freshness_hours is not None:
        if head.freshness_hours is None:
            changes.append(Change("sla_removed", BREAKING, None, "freshness SLA removed"))
        elif head.freshness_hours > base.freshness_hours:
            changes.append(Change("sla_loosened", BREAKING, None,
                                  f"freshness SLA loosened {base.freshness_hours:g}h → {head.freshness_hours:g}h"))
        elif head.freshness_hours < base.freshness_hours:
            changes.append(Change("sla_tightened", SAFE, None,
                                  f"freshness SLA tightened {base.freshness_hours:g}h → {head.freshness_hours:g}h"))

    changes.sort(key=lambda c: -_RANK[c.severity])
    return DiffResult(base=base, head=head, changes=changes, renames=renames)
