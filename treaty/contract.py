"""Loading and validating data contracts."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
TYPE_RE = re.compile(r"^\s*([a-z_]+)\s*(?:\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\))?\s*$", re.I)


class ContractError(ValueError):
    """The contract file is missing, malformed or fails validation."""


@dataclass(frozen=True)
class ColumnType:
    base: str
    p1: int | None = None
    p2: int | None = None

    @classmethod
    def parse(cls, text: str) -> "ColumnType":
        m = TYPE_RE.match(str(text))
        if not m:
            raise ContractError(f"unrecognised column type: {text!r}")
        base, p1, p2 = m.group(1).lower(), m.group(2), m.group(3)
        base = {"integer": "int", "long": "bigint", "text": "string", "bool": "boolean",
                "numeric": "decimal", "real": "float"}.get(base, base)
        return cls(base, int(p1) if p1 else None, int(p2) if p2 else None)

    def __str__(self) -> str:
        if self.p1 is None:
            return self.base
        if self.p2 is None:
            return f"{self.base}({self.p1})"
        return f"{self.base}({self.p1},{self.p2})"

    def family(self) -> str:
        if self.base in {"smallint", "int", "bigint", "float", "double", "decimal"}:
            return "number"
        if self.base in {"string", "varchar", "char"}:
            return "text"
        return self.base


@dataclass
class Column:
    name: str
    type: ColumnType
    nullable: bool = True
    primary_key: bool = False
    enum: tuple[str, ...] | None = None
    description: str = ""


@dataclass
class Contract:
    name: str
    version: tuple[int, int, int]
    dataset: str
    columns: dict[str, Column]
    freshness_hours: float | None = None
    owner: dict = field(default_factory=dict)
    source: str = ""

    @property
    def version_str(self) -> str:
        return ".".join(map(str, self.version))


def parse_contract(text: str, source: str = "<string>") -> Contract:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ContractError(f"{source}: not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ContractError(f"{source}: expected a mapping at the top level")

    for key in ("contract", "version", "dataset", "columns"):
        if key not in raw:
            raise ContractError(f"{source}: missing required key '{key}'")

    m = SEMVER.match(str(raw["version"]))
    if not m:
        raise ContractError(f"{source}: version must be MAJOR.MINOR.PATCH, got {raw['version']!r}")

    if not isinstance(raw["columns"], list) or not raw["columns"]:
        raise ContractError(f"{source}: columns must be a non-empty list")

    columns: dict[str, Column] = {}
    for i, col in enumerate(raw["columns"]):
        if not isinstance(col, dict) or "name" not in col or "type" not in col:
            raise ContractError(f"{source}: column #{i + 1} needs a name and a type")
        name = str(col["name"])
        if name in columns:
            raise ContractError(f"{source}: duplicate column '{name}'")
        enum = col.get("enum")
        columns[name] = Column(
            name=name,
            type=ColumnType.parse(col["type"]),
            nullable=bool(col.get("nullable", True)),
            primary_key=bool(col.get("primary_key", False)),
            enum=tuple(str(v) for v in enum) if enum else None,
            description=str(col.get("description", "")),
        )

    sla = raw.get("sla") or {}
    return Contract(
        name=str(raw["contract"]),
        version=tuple(int(g) for g in m.groups()),  # type: ignore[arg-type]
        dataset=str(raw["dataset"]),
        columns=columns,
        freshness_hours=float(sla["freshness_hours"]) if "freshness_hours" in sla else None,
        owner=raw.get("owner") or {},
        source=source,
    )


def load_contract(path: str | Path) -> Contract:
    p = Path(path)
    if not p.is_file():
        raise ContractError(f"contract not found: {p}")
    return parse_contract(p.read_text(encoding="utf-8"), source=str(p))


def load_contract_at_ref(path: str | Path, ref: str) -> Contract:
    """Load the contract as it exists at a git ref (e.g. `main`)."""
    p = Path(path).resolve()
    try:
        out = subprocess.run(
            ["git", "-C", str(p.parent), "show", f"{ref}:./{p.name}"],
            check=True, capture_output=True, text=True,
        )
    except FileNotFoundError as exc:
        raise ContractError("git is not installed") from exc
    except subprocess.CalledProcessError as exc:
        raise ContractError(f"could not read {p.name} at {ref}: {exc.stderr.strip()}") from exc
    return parse_contract(out.stdout, source=f"{ref}:{p.name}")
