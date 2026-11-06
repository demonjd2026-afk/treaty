import datetime as dt
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from treaty.cli import main
from treaty.contract import ColumnType, ContractError, load_contract, parse_contract
from treaty.diff import BREAKING, SAFE, WARNING, diff_contracts, is_widening
from treaty.lineage import assess, load_consumers, scan_sql
from treaty.render import shim_sql

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "contracts" / "orders.yaml"
RENAME = ROOT / "tests" / "fixtures" / "orders.amount_usd.yaml"


def contract(version="1.0.0", columns=None, sla=None, dataset="d.t"):
    cols = columns or [{"name": "id", "type": "string", "nullable": False, "primary_key": True}]
    doc = {"contract": "c", "version": version, "dataset": dataset, "columns": cols}
    if sla:
        doc["sla"] = sla
    return parse_contract(json.dumps(doc))


def kinds(result):
    return {c.kind for c in result.changes}


class ContractParsing(unittest.TestCase):
    def test_loads_demo_contract(self):
        c = load_contract(BASE)
        self.assertEqual(c.name, "orders")
        self.assertIn("amount", c.columns)

    def test_rejects_bad_version(self):
        with self.assertRaises(ContractError):
            contract(version="v1")

    def test_rejects_duplicate_columns(self):
        with self.assertRaises(ContractError):
            contract(columns=[{"name": "a", "type": "int"}, {"name": "a", "type": "int"}])

    def test_type_parsing(self):
        self.assertEqual(str(ColumnType.parse("DECIMAL(12, 2)")), "decimal(12,2)")
        self.assertEqual(ColumnType.parse("integer").base, "int")


class Widening(unittest.TestCase):
    def test_safe_widenings(self):
        for a, b in [("int", "bigint"), ("decimal(12,2)", "decimal(18,2)"),
                     ("varchar(10)", "string"), ("float", "double"), ("int", "decimal(12,0)")]:
            self.assertTrue(is_widening(ColumnType.parse(a), ColumnType.parse(b)), (a, b))

    def test_narrowings(self):
        for a, b in [("bigint", "int"), ("decimal(12,2)", "decimal(12,0)"),
                     ("string", "int"), ("timestamp", "date"), ("varchar(20)", "varchar(10)")]:
            self.assertFalse(is_widening(ColumnType.parse(a), ColumnType.parse(b)), (a, b))


class Rules(unittest.TestCase):
    base_cols = [
        {"name": "id", "type": "string", "nullable": False, "primary_key": True},
        {"name": "qty", "type": "int", "nullable": False},
        {"name": "state", "type": "string", "nullable": False, "enum": ["a", "b", "c"]},
    ]

    def head(self, mutate, version="2.0.0", **kw):
        cols = [dict(c) for c in self.base_cols]
        mutate(cols)
        return contract(version=version, columns=cols, **kw)

    def run_diff(self, mutate, version="2.0.0", **kw):
        return diff_contracts(contract(columns=self.base_cols, sla={"freshness_hours": 6}),
                              self.head(mutate, version, sla=kw.get("sla", {"freshness_hours": 6})))

    def test_no_change_is_safe(self):
        r = diff_contracts(contract(columns=self.base_cols), contract(columns=self.base_cols))
        self.assertEqual(r.verdict, SAFE)
        self.assertTrue(r.version_ok)

    def test_add_nullable_column_is_safe_minor(self):
        r = self.run_diff(lambda c: c.append({"name": "note", "type": "string"}), version="1.1.0")
        self.assertEqual(r.verdict, SAFE)
        self.assertEqual(r.required_bump, "minor")
        self.assertTrue(r.version_ok)

    def test_remove_column_is_breaking(self):
        r = self.run_diff(lambda c: c.pop(1))
        self.assertEqual(r.verdict, BREAKING)
        self.assertIn("column_removed", kinds(r))

    def test_rename_is_detected(self):
        r = self.run_diff(lambda c: c[1].update(name="quantity"))
        self.assertEqual(r.renames, {"qty": "quantity"})

    def test_ambiguous_rename_is_not_guessed(self):
        def m(c):
            c.pop(1)
            c += [{"name": "q1", "type": "int"}, {"name": "q2", "type": "int"}]
        self.assertEqual(self.run_diff(m).renames, {})

    def test_narrowing_type_is_breaking(self):
        r = self.run_diff(lambda c: c[1].update(type="smallint"))
        self.assertIn("type_changed", kinds(r))

    def test_widening_type_is_safe(self):
        r = self.run_diff(lambda c: c[1].update(type="bigint"), version="1.1.0")
        self.assertEqual(r.verdict, SAFE)

    def test_relaxed_nullability_is_breaking(self):
        r = self.run_diff(lambda c: c[1].update(nullable=True))
        self.assertIn("nullability_relaxed", kinds(r))

    def test_enum_removal_breaks_addition_warns(self):
        self.assertEqual(self.run_diff(lambda c: c[2].update(enum=["a", "b"])).verdict, BREAKING)
        self.assertEqual(self.run_diff(lambda c: c[2].update(enum=["a", "b", "c", "d"]),
                                       version="1.1.0").verdict, WARNING)

    def test_primary_key_change_is_breaking(self):
        r = self.run_diff(lambda c: c[1].update(primary_key=True))
        self.assertIn("primary_key_changed", kinds(r))

    def test_loosened_sla_is_breaking(self):
        r = self.run_diff(lambda c: None, sla={"freshness_hours": 24})
        self.assertIn("sla_loosened", kinds(r))

    def test_breaking_change_needs_major_bump(self):
        r = self.run_diff(lambda c: c.pop(1), version="1.1.0")
        self.assertFalse(r.version_ok)
        self.assertEqual(r.required_bump, "major")


class DemoScenario(unittest.TestCase):
    def setUp(self):
        self.result = diff_contracts(load_contract(BASE), load_contract(RENAME))
        self.impacts = assess(self.result, load_consumers(ROOT / "consumers.yaml"), ROOT)

    def test_rename_breaks_finance_and_marketing_only(self):
        status = {i.consumer.id: i.status for i in self.impacts}
        self.assertEqual(status["finance-revenue-daily"], BREAKING)
        self.assertEqual(status["marketing-ltv"], BREAKING)
        self.assertEqual(status["ops-fulfilment"], SAFE)

    def test_meaning_change_is_flagged(self):
        self.assertIn("meaning_changed", kinds(self.result))

    def test_lineage_drift_found_from_sql(self):
        drift = {i.consumer.id: i.undeclared for i in self.impacts}
        self.assertEqual(drift["marketing-ltv"], ["channel"])

    def test_shim_maps_old_name(self):
        sql = shim_sql(self.result, dt.date(2026, 12, 7))
        self.assertIn("cast(amount_usd as decimal(12,2)) as amount", sql)
        self.assertIn("2026-12-07", sql)
        self.assertIn("WARNING", sql)


class SqlScan(unittest.TestCase):
    def test_ignores_comments_and_literals(self):
        cols, lits = scan_sql("-- amount here\nselect status from t where status = 'amount'",
                              {"amount", "status"})
        self.assertEqual(cols, {"status"})
        self.assertEqual(lits, {"amount"})


class Cli(unittest.TestCase):
    def test_exit_codes(self):
        self.assertEqual(main(["check", str(RENAME), "--base-file", str(BASE),
                               "--lineage", str(ROOT / "consumers.yaml")]), 1)
        self.assertEqual(main(["check", str(BASE), "--base-file", str(BASE), "--lineage", "none.yaml"]), 0)
        self.assertEqual(main(["validate", "missing.yaml"]), 2)

    def test_git_ref_base(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "orders.yaml"
            git = lambda *a: subprocess.run(["git", "-C", d, *a], check=True, capture_output=True)  # noqa: E731
            git("init", "-q", "-b", "main")
            git("config", "user.email", "t@example.com")
            git("config", "user.name", "t")
            p.write_text(BASE.read_text())
            git("add", ".")
            git("commit", "-qm", "base")
            p.write_text(RENAME.read_text())
            self.assertEqual(main(["check", str(p), "--base", "main", "--lineage", "none.yaml"]), 1)


if __name__ == "__main__":
    unittest.main()
