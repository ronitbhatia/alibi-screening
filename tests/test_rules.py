import unittest
import re
from types import SimpleNamespace
from pathlib import Path

from alibi.facts import Evidence, note_check, profile_facts
from alibi.rules import adjudicate, decide, score, error_cost
from alibi.store import REASONS, open_dataset


class DecisionTests(unittest.TestCase):
    def decision(self, *, name="William Smith", dob=(1970, 1, 1), country="GB",
                 verified=True, kind="Individual", **fields):
        customer = {"verified": verified, "names": [(name, "N")], "dates": [(dob, "B")],
                    "countries": [(country, "L")] if country else [], "types": [(kind, "C")]}
        hit = {"hit_id": "H", "name": "Bill Smith", "entity_type": "Individual", "dobs": ["1970"],
               "countries": ["United Kingdom"], **fields}
        return decide(customer, hit, profile_facts(hit, []), "2020-01-01")["code"]

    def test_policy_reason_codes(self):
        source = Path(__file__).resolve().parents[1] / "POLICY.md"
        if not source.exists():
            self.skipTest("Participant policy unavailable")
        policy = source.read_text()
        documented = set(re.findall(r"^\| `(\w+)` \| `(\w+)` \|", policy, re.M))
        self.assertEqual(documented, {(cls, code) for cls, codes in REASONS.items() for code in codes})

    def test_cost_matrix(self):
        self.assertEqual(error_cost("REVIEW", "DIFFERENT_ENTITY"), 60)
        self.assertEqual(error_cost("SAME_ENTITY", "DIFFERENT_ENTITY"), 60)
        self.assertEqual(error_cost("DIFFERENT_ENTITY", "REVIEW"), 3)
        self.assertEqual(error_cost("SAME_ENTITY", "REVIEW"), 2)
        self.assertEqual(error_cost("REVIEW", "SAME_ENTITY"), 2)
        self.assertEqual(error_cost("DIFFERENT_ENTITY", "SAME_ENTITY"), 3)
        wrong = {"hit_id": "H", "decision": "DIFFERENT_ENTITY", "code": "DECEASED", "candidates": []}
        truth = {"hit_id": "H", "decision": "SAME_ENTITY", "reason_codes": ["NAME_DOB_MATCH"]}
        self.assertEqual(score([wrong], [truth])["decision_score"], 0)

    def test_unverified_date_conflict_abstains(self):
        self.assertEqual(self.decision(verified=False, dobs=["1995"]), "INSUFFICIENT_DATA")

    def test_confirmations_and_location_exception(self):
        self.assertEqual(self.decision(), "NAME_DOB_LOCATION_MATCH")
        self.assertEqual(self.decision(countries=[]), "NAME_DOB_MATCH")
        self.assertEqual(self.decision(countries=["France"]), "INSUFFICIENT_DATA")
        self.assertEqual(self.decision(countries=["France"], dobs=[]), "LOCATION_MISMATCH")
        self.assertEqual(self.decision(kind="Company", entity_type="Company"), "NAME_LOCATION_MATCH")

    def test_dates_and_time(self):
        self.assertEqual(self.decision(dobs=["1971"]), "INSUFFICIENT_DATA")
        self.assertEqual(self.decision(dobs=["1980", "1970-02"]), "DOB_MISMATCH")
        self.assertEqual(self.decision(dobs=["1980", "1970-01"]), "NAME_DOB_LOCATION_MATCH")
        self.assertEqual(self.decision(deceased_date="2019"), "DECEASED")
        self.assertEqual(self.decision(deceased_date="2020"), "NAME_DOB_LOCATION_MATCH")
        report = "According to Example; December 31, 1985: Report."
        self.assertEqual(self.decision(profile_notes=report), "TEMPORAL_IMPOSSIBILITY")
        self.assertEqual(self.decision(kind="Company", entity_type="Company", dob=(1986,),
                                       profile_notes=report), "TEMPORAL_IMPOSSIBILITY")

    def test_identity_contradictions_require_evidence(self):
        self.assertEqual(self.decision(kind="Company"), "ENTITY_TYPE_MISMATCH")
        self.assertEqual(self.decision(name="Alice Jones"), "NAME_MISMATCH_VERIFIED")
        self.assertEqual(self.decision(name="Alice Jones", verified=False), "INSUFFICIENT_DATA")
        self.assertEqual(self.decision(name="Smith TEST"), "INSUFFICIENT_DATA")
        self.assertEqual(self.decision(aliases=["William"]), "NAME_DOB_LOCATION_MATCH")

    def test_web_requires_category_and_country_and_is_fallback(self):
        hit = dict(hit_id="H", entity_type="Individual", countries=["France"], categories=["PEP"])
        def row(text):
            return dict(result_id="S", snippet=text)
        rows = [row("In March 2000, aged 25, a politically exposed person in Italy."),
                row("In March 1990, aged 25, a football coach in France.")]
        self.assertEqual(profile_facts(hit, rows)["dates"], [])
        rows.append(row("In March 2000, aged 25, a politically exposed person in France."))
        self.assertEqual(profile_facts(hit, rows)["dates"], [((1974,), "S"), ((1975,), "S")])
        self.assertEqual(profile_facts(dict(hit, dobs=["1980"]), rows)["dates"], [((1980,), "H")])

    def test_birth_date_wording_and_successful_check(self):
        for text in (
            "The provider validated JANE SMITH on 2024-05-01, referencing a birth date of January 11, 1977.",
            "The provider performed a successful check on JANE SMITH, born 11 January 1977.",
        ):
            check = note_check(dict(note_id="N", channel="kyc", created_at="2024-05-01", text=text))
            self.assertEqual(check["dob"], (1977, 1, 11))
        hit = dict(hit_id="H", entity_type="Individual",
                   profile_notes="Investigators questioned Jane Smith in January 2023, she was 46.")
        self.assertEqual(profile_facts(hit, [])["dates"], [((1976,), "H"), ((1977,), "H")])

    def test_unreadable_latest_verified_dob_stays_unknown(self):
        dataset = SimpleNamespace(rows=lambda table: ())
        resolver = SimpleNamespace(records={"C": {"record_id": "C", "dob": "1965-01-01"}},
                                   approved_checks={"C": [{"check_id": "K", "checked_at": "2025-01-01",
                                                           "verified_dob": "unknown"}]})
        facts = Evidence(dataset, resolver).customer(("C",), {"entity_type": "Individual"})
        self.assertEqual(facts["dates"], [((), "K")])

    def test_rejected_kyc_note(self):
        row = dict(note_id="N", channel="kyc", created_at="2020-01-01",
                   text="Passport verification failed for JOE SMITH, born 1 May 1970.")
        self.assertIsNone(note_check(row))


class WorkbookVerdicts(unittest.TestCase):
    def test_batch_and_practice_safety(self):
        path = Path(__file__).resolve().parents[1] / "Screening Dev Set - browse.xlsx"
        if not path.exists():
            self.skipTest("Participant workbook unavailable")
        with open_dataset(path) as dataset:
            predictions = list(adjudicate(dataset))
            report = score(predictions, dataset.rows("practice_labels"))
        self.assertEqual(report["hits"], 819)
        self.assertEqual(report["practice_hits"], 50)
        self.assertEqual(report["class_and_code_correct"], 50)
        self.assertEqual(report["unsafe_clearances"], 0)
        self.assertEqual(report["all_review_cost"], 108)
        self.assertEqual(report["system_cost"], 0)
        self.assertEqual(report["decision_score"], 1.0)
        for p in predictions:
            self.assertIn(p["code"], REASONS[p["decision"]])
            if all(c["code"] != "DECEASED" for c in p["candidates"]):
                self.assertNotIn(p["alert_id"], p["evidence"])
            self.assertTrue(p["reason"].endswith("."))
            self.assertIn(p["hit_id"], p["evidence"])
            divergent = len({c["decision"] for c in p["candidates"]}) > 1
            self.assertEqual(p["code"] == "AMBIGUOUS_CUSTOMER_RECORD", divergent)
