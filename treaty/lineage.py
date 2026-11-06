"""Consumer impact: declared lineage cross-checked against the consumers' SQL."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .contract import ContractError
from .diff import BREAKING, SAFE, WARNING, DiffResult

_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_STRING = re.compile(r"'(?:[^']|'')*'")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class Consumer:
    id: str
    contract: str
    team: str
    owner: str
    room: str
    asset: str
    sql: str | None
    uses: list[str]


@dataclass
class Impact:
    consumer: Consumer
    status: str  # breaking | warning | safe
    columns: list[str]
    reasons: list[str]
    undeclared: list[str] = field(default_factory=list)
    literal_hits: list[str] = field(default_factory=list)


def load_consumers(path: str | Path) -> list[Consumer]:
    p = Path(path)
    if not p.is_file():
        raise ContractError(f"lineage file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = []
    for row in raw.get("consumers", []):
        out.append(Consumer(
            id=str(row["id"]), contract=str(row["contract"]), team=str(row.get("team", "")),
            owner=str(row.get("owner", "")), room=str(row.get("room", "")),
            asset=str(row.get("asset", row["id"])), sql=row.get("sql"),
            uses=[str(c) for c in row.get("uses", [])],
        ))
    return out


def scan_sql(sql: str, known_columns: set[str]) -> tuple[set[str], set[str]]:
    """Return (columns referenced, string literals) from a SQL text.
    Deliberately simple: identifiers are matched against known column names."""
    no_comments = _COMMENT.sub(" ", sql)
    literals = {m.group(0)[1:-1].replace("''", "'") for m in _STRING.finditer(no_comments)}
    code = _STRING.sub(" ", no_comments)
    idents = {m.group(0).lower() for m in _IDENT.finditer(code)}
    return {c for c in known_columns if c.lower() in idents}, literals


def assess(result: DiffResult, consumers: list[Consumer], root: str | Path = ".") -> list[Impact]:
    root = Path(root)
    known = set(result.base.columns)
    touched = result.touched_columns(WARNING)
    contract_level = [c for c in result.changes if c.column is None and c.severity == BREAKING]
    impacts: list[Impact] = []

    for cons in (c for c in consumers if c.contract == result.base.name):
        used = set(cons.uses)
        literals: set[str] = set()
        undeclared: list[str] = []
        if cons.sql and (root / cons.sql).is_file():
            scanned, literals = scan_sql((root / cons.sql).read_text(encoding="utf-8"), known)
            undeclared = sorted(scanned - used)
            used |= scanned

        hit_cols = sorted(c for c in used if c in touched)
        reasons = [ch.message for col in hit_cols for ch in touched[col]]
        reasons += [ch.message for ch in contract_level]
        severities = [ch.severity for col in hit_cols for ch in touched[col]]
        severities += [BREAKING] * len(contract_level)

        literal_hits = []
        for col in hit_cols:
            for ch in touched[col]:
                if ch.kind == "enum_value_removed":
                    literal_hits += [v for v in ch.detail.get("values", []) if v in literals]

        status = BREAKING if BREAKING in severities else WARNING if severities else SAFE
        impacts.append(Impact(cons, status, hit_cols, reasons, undeclared, sorted(set(literal_hits))))

    order = {BREAKING: 0, WARNING: 1, SAFE: 2}
    impacts.sort(key=lambda i: (order[i.status], i.consumer.id))
    return impacts
