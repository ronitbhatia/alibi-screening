import json
from pathlib import Path
import tempfile
import unittest

from alibi.store import DataError, TABLES, inspect, linked_hits, open_dataset


class WorkbookBaseline(unittest.TestCase):
    def test_supplied_workbook(self):
        path = Path(__file__).resolve().parents[1] / "Screening Dev Set - browse.xlsx"
        if not path.exists():
            self.skipTest("Participant workbook unavailable")
        with open_dataset(path) as dataset:
            report = inspect(dataset)
        self.assertEqual(report["rows"], {
            "alerts": 440, "hits": 819, "customers": 72265, "accounts": 86659,
            "onboarding": 72265, "kyc_checks": 54488, "notes": 31895,
            "search_results": 16882, "practice_labels": 50,
        })
        self.assertEqual(report["linked_hits"], 819)
        self.assertEqual(report["practice_decisions"],
                         {"DIFFERENT_ENTITY": 28, "SAME_ENTITY": 12, "REVIEW": 10})


class JsonInputs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for table in TABLES:
            if table != "practice_labels":
                self.write(table, [])
        self.write("alerts", [{"alert_id": "A", "hit_ids": ["H"]}])
        self.write("hits", [{"hit_id": "H", "alert_id": "A"}])

    def write(self, table, rows):
        (self.root / f"{table}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def test_unlabeled_jsonl(self):
        with open_dataset(self.root) as dataset:
            _, pairs = linked_hits(dataset)
            self.assertEqual(pairs[0][1]["alert_id"], "A")
            self.assertNotIn("practice_labels", inspect(dataset)["rows"])

    def test_nested_screened_customer_and_folder_name_policy(self):
        from alibi.identity import names_agree
        self.write("alerts", [{"alert_id": "A", "hit_ids": ["H"], "customer": {
            "name": "Orin North", "entity_type": "Individual", "dob": "1990",
            "country": "GB", "city": "London"}}])
        (self.root / "name_variants.json").write_text(json.dumps({
            "groups": [["orin", "kester"]], "company_legal_forms": ["plc"]}))
        with open_dataset(self.root) as dataset:
            row = next(dataset.rows("alerts"))
            self.assertEqual((row["customer_name"], row["dob"], row["country"], row["city"]),
                             ("Orin North", "1990", "GB", "London"))
            self.assertTrue(names_agree("Orin North", "Kester North"))
        self.assertFalse(names_agree("Orin North", "Kester North"))

    def test_wrong_parent(self):
        self.write("hits", [{"hit_id": "H", "alert_id": "B"}])
        with open_dataset(self.root) as dataset, self.assertRaisesRegex(DataError, "parent"):
            inspect(dataset)

    def test_dangling_hit_reference(self):
        self.write("hits", [])
        with open_dataset(self.root) as dataset, self.assertRaisesRegex(DataError, "IDs"):
            inspect(dataset)

    def test_duplicate_hit(self):
        self.write("hits", [{"hit_id": "H", "alert_id": "A"}] * 2)
        with open_dataset(self.root) as dataset, self.assertRaisesRegex(DataError, "Duplicate"):
            inspect(dataset)

    def test_policy_codes_missing_from_pdf(self):
        for code in ("DOB_MISMATCH", "TEMPORAL_IMPOSSIBILITY"):
            self.write("practice_labels", [{"hit_id": "H", "alert_id": "A",
                       "decision": "DIFFERENT_ENTITY", "reason_codes": [code]}])
            with open_dataset(self.root) as dataset:
                self.assertEqual(inspect(dataset)["practice_decisions"], {"DIFFERENT_ENTITY": 1})
