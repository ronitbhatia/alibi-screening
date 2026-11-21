import unittest
from pathlib import Path

from alibi.identity import names_agree
from alibi.resolve import Resolver, resolve_dataset
from alibi.store import open_dataset


class IdentityTests(unittest.TestCase):
    def test_names(self):
        self.assertTrue(names_agree("Bill Abdulsalam", "Abdul Salam William"))
        self.assertTrue(names_agree("José Silva Ltd", "Jose Silva GmbH", company=True))
        self.assertFalse(names_agree("TEST 000", "UNKNOWN N/A"))
        self.assertFalse(names_agree("abcd", "bacd"))
        self.assertFalse(names_agree("John Jon White", "John Black Smith"))

    def test_transitive_identity_and_joint_holders(self):
        def person(key, name, phone, dob):
            return dict(record_id=key, full_name=name, phone=phone, dob=dob,
                        entity_type="Individual", email=key+"@example.test")

        rows = {
            "customers": [person("A", "Joe Smith", "1234", "1960-02-03"),
                          person("B", "Joseph Smith", "1234", "03/02/1960"),
                          person("C", "Joe Smith", "5678", "1960-02-03"),
                          person("D", "Jane Smith", "1234", "1965-04-07")],
            "accounts": [dict(account_id="joint", customer_id=key, login_handle=key)
                         for key in "ABCD"],
            "onboarding": [dict(account_id="joint", customer_id=key, kyc_applicant_id=key)
                           for key in "BCD"],
            "kyc_checks": [dict(applicant_id=key, document_number="doc42",
                                result="rejected" if key=="D" else "approved") for key in "BCD"],
        }
        class Data:
            def rows(self, table):
                return rows[table]

        resolver = Resolver(Data())
        alert = dict(alert_id="1", account_ref="joint", customer_name="Joe Smith",
                     entity_type="Individual", dob="1960")
        self.assertEqual(resolver.resolve(alert).candidates, (("A", "B", "C"),))
        self.assertEqual(resolver.resolve(dict(alert, dob="1970")).customer_record_id, None)
        rows["customers"][3].update(full_name="Joe Smith", dob="1960", phone=None)
        result = Resolver(Data()).resolve(alert)
        self.assertEqual(result.candidates, (("A", "B", "C"), ("D",)))
        self.assertEqual(result.customer_record_id, "AMBIGUOUS")


class WorkbookResolution(unittest.TestCase):
    def test_practice_customer_resolution(self):
        path = Path(__file__).resolve().parents[1] / "Screening Dev Set - browse.xlsx"
        if not path.exists():
            self.skipTest("Participant workbook unavailable")
        with open_dataset(path) as dataset:
            resolved = {r.alert_id: r for r in resolve_dataset(dataset)}
            self.assertEqual(len(resolved), 440)
            for label in dataset.rows("practice_labels"):
                result = resolved[label["alert_id"]]
                with self.subTest(hit=label["hit_id"]):
                    self.assertIn(result.customer_record_id, label["customer_record_ids"])
                    if result.customer_record_id != "AMBIGUOUS":
                        self.assertEqual(set(result.candidates[0]), set(label["customer_record_ids"]))
