"""Send one synthetic extraction through the real client, bypassing prior cache."""
import os
from tempfile import TemporaryDirectory
from .extract import DEFAULT_MODEL, Extractor

SENTENCE = "The applicant's date of birth is 11 January 1977."


def main():
    # A free route even if the shell that set ALIBI_MODEL was closed.
    model = os.getenv("ALIBI_MODEL") or DEFAULT_MODEL
    with TemporaryDirectory() as cache:
        client = Extractor(os.getenv("OPENROUTER_API_KEY", ""), model=model, cache=cache)
        facts, status = client.batch([{"sentence": SENTENCE}])[SENTENCE]
    dob = facts.get("dob", {})
    if status == "verified" and dob.get("value") != "11 January 1977":
        status = "expected_dob_missing"
    print("Model:", model)
    print("Status:", status)
    if status == "verified":
        print("Verified DOB:", dob["value"])
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
