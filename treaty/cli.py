"""treaty: decide whether a data-contract change is breaking, and who must agree."""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from . import __version__
from .contract import ContractError, load_contract, load_contract_at_ref
from .diff import BREAKING, diff_contracts
from .lineage import assess, load_consumers
from .render import as_json, markdown, shim_sql

EXIT_OK, EXIT_BREAKING, EXIT_INVALID = 0, 1, 2


def _load_pair(args):
    head = load_contract(args.contract)
    if args.base_file:
        base = load_contract(args.base_file)
    else:
        base = load_contract_at_ref(args.contract, args.base)
    return base, head


def cmd_check(args) -> int:
    base, head = _load_pair(args)
    result = diff_contracts(base, head)
    impacts = []
    lineage = Path(args.lineage)
    if lineage.is_file():
        impacts = assess(result, load_consumers(lineage), root=args.root or lineage.parent)
    print(as_json(result, impacts) if args.format == "json" else markdown(result, impacts, args.contract))
    if not result.version_ok:
        return EXIT_INVALID
    return EXIT_BREAKING if result.verdict == BREAKING else EXIT_OK


def cmd_shim(args) -> int:
    base, head = _load_pair(args)
    until = dt.date.fromisoformat(args.until) if args.until else dt.date.today() + dt.timedelta(days=args.days)
    print(shim_sql(diff_contracts(base, head), until), end="")
    return EXIT_OK


def cmd_validate(args) -> int:
    c = load_contract(args.contract)
    print(f"✅ {c.name} {c.version_str}: {len(c.columns)} columns, dataset {c.dataset}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="treaty", description=__doc__)
    p.add_argument("--version", action="version", version=f"treaty {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def pair(sp):
        sp.add_argument("contract", help="path to the proposed contract YAML")
        g = sp.add_mutually_exclusive_group()
        g.add_argument("--base", default="main", help="git ref holding the agreed contract (default: main)")
        g.add_argument("--base-file", help="compare against this file instead of a git ref")

    c = sub.add_parser("check", help="classify a contract change and list affected consumers")
    pair(c)
    c.add_argument("--lineage", default="consumers.yaml", help="consumer lineage file (default: consumers.yaml)")
    c.add_argument("--root", help="directory consumer SQL paths are relative to (default: lineage file's dir)")
    c.add_argument("--format", choices=["md", "json"], default="md")
    c.set_defaults(func=cmd_check)

    s = sub.add_parser("shim", help="emit a compatibility view that keeps old column names working")
    pair(s)
    s.add_argument("--days", type=int, default=30, help="deprecation window in days (default: 30)")
    s.add_argument("--until", help="explicit drop date, YYYY-MM-DD (overrides --days)")
    s.set_defaults(func=cmd_shim)

    v = sub.add_parser("validate", help="check that a contract file is well formed")
    v.add_argument("contract")
    v.set_defaults(func=cmd_validate)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ContractError as exc:
        print(f"treaty: {exc}", file=sys.stderr)
        return EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
