import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from alibi.extract import Extractor, checked
from alibi.facts import profile_facts
from alibi.rules import adjudicate
from alibi.store import TABLES, open_dataset


class ExtractionTests(unittest.TestCase):
    sentence = "The subject's year of birth is recorded as 1970."

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cache = self.root / "cache"
        alerts = [{"alert_id": "A1", "hit_ids": ["H1", "H2"], "customer_name": "Jane Doe",
                   "email": "j@example.test", "entity_type": "Individual", "screened_at": "2026-01-01"}]
        hits = [{"hit_id": key, "alert_id": "A1", "name": "Jane Doe", "entity_type": "Individual",
                 "profile_notes": self.sentence + " Another irrelevant sentence."} for key in ("H1", "H2")]
        hits[1]["deceased_date"] = "2000"
        tables = {"alerts": alerts, "hits": hits, "customers": [{"record_id": "C1", "email": "j@example.test",
                  "entity_type": "Individual", "full_name": "Jane Doe", "dob": "1970-01-01"}]}
        for table in TABLES:
            rows = tables.get(table, [])
            (self.root / (table + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in rows))

    def run_batch(self, key="fake-test-key", seed=1):
        with open_dataset(self.root) as dataset:
            return list(adjudicate(dataset, Extractor(key, cache=self.cache, seed=seed)))

    def response(self, request, timeout):
        body = json.loads(request.data)
        self.assertEqual(body["messages"][1], {"role": "user", "content": self.sentence})
        self.assertEqual(body["temperature"], 0)
        self.assertIn("seed", body)
        fact = {"dob": "1970", "dob_quote": "year of birth is recorded as 1970"}
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(fact)}}]}).encode())

    def test_keyed_verified_fact_and_seeded_cache(self):
        with patch("alibi.extract.urlopen", side_effect=self.response) as send:
            result, independent = self.run_batch()
            self.assertEqual(result["code"], "NAME_DOB_MATCH")
            fact = result["candidates"][0]["trace"]["model_reads"][0]
            self.assertIn(fact["facts"]["dob"]["quote"], self.sentence)
            self.assertEqual(fact["source"], "H1")
            self.assertEqual(independent["code"], "DECEASED")
            self.assertEqual(self.run_batch()[0], result)
            self.assertEqual(send.call_count, 1)
            self.run_batch(seed=2)
            self.assertEqual(send.call_count, 2)
            self.assertEqual(self.run_batch(key="")[0]["decision"], "REVIEW")
            self.assertEqual(send.call_count, 2)

    def test_unavailable_service_and_bad_json(self):
        for failure in (TimeoutError(), None):
            with patch("alibi.extract.urlopen", side_effect=failure, return_value=io.BytesIO(b"invalid")):
                result, independent = self.run_batch()
            self.assertEqual(result["decision"], "REVIEW")
            self.assertEqual(independent["code"], "DECEASED")
            self.assertEqual(list(self.cache.rglob("*.json")), [])

    def test_safe_diagnostics(self):
        error = HTTPError("https://example.invalid", 404, "redacted", {},
                          io.BytesIO(b'{"error":{"message":"No endpoints matching data policy SECRET"}}'))
        with patch("alibi.extract.urlopen", side_effect=error):
            result, _ = self.run_batch()
        read = result["candidates"][0]["trace"]["model_reads"][0]
        self.assertEqual(read["status"], "http_404_privacy_policy")
        for content, expected in [("bad", "invalid_model_json"),
                                  ('{"dob":"1970","dob_quote":"invented"}', "validation_unverified_span")]:
            body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
            with patch("alibi.extract.urlopen", return_value=io.BytesIO(body)):
                result, _ = self.run_batch()
            self.assertEqual(result["candidates"][0]["trace"]["model_reads"][0]["status"], expected)
            self.assertEqual(result["decision"], "REVIEW")

    def test_truncation_retries_once_with_larger_budget(self):
        budgets = []
        def truncated(request, timeout):
            budgets.append(json.loads(request.data)["max_tokens"])
            return io.BytesIO(b'{"choices":[{"finish_reason":"length"}]}')
        with patch("alibi.extract.urlopen", side_effect=truncated):
            row, _ = self.run_batch()
        self.assertEqual(budgets, [2048, 4096])
        self.assertEqual(row["decision"], "REVIEW")
        self.assertEqual(row["candidates"][0]["trace"]["model_reads"][0]["status"], "completion_truncated")
        with patch("alibi.extract.urlopen", side_effect=lambda req, timeout:
                   truncated(req, timeout) if len(budgets) == 2 else self.response(req, timeout)):
            row, _ = self.run_batch()
        self.assertEqual(row["code"], "NAME_DOB_MATCH")

    def test_quote_must_ground_value(self):
        for facts in ({"dob": "1971", "dob_quote": self.sentence},
                      {"dob": "1970", "dob_quote": "born in 1970"},
                      {"dob": "1970", "dob_quote": "1970"}):
            with self.assertRaises(ValueError):
                checked(self.sentence, facts)

    def test_wrong_listing_country_rejected_before_extraction(self):
        hit = {"hit_id": "H", "entity_type": "Individual", "countries": ["France"], "categories": ["PEP"]}
        row = {"result_id": "S", "snippet": "A politically exposed person in Italy visited France, birth year unknown."}
        facts = profile_facts(hit, [row], model_reads=True)
        self.assertEqual(facts["pending"], [])
        self.assertEqual(facts["dates"], [])
