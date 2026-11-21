# ALIBI

ALIBI helps a payments analyst decide whether a customer and a watchlist entry describe the same person or company. It follows the customer's records across bank systems, checks the listed entity's identity evidence, and gives each hit a decision with a short explanation and the IDs that support it. An alert closes only when every hit is cleared.

[Watch the ALIBI demo on YouTube](https://youtu.be/mr6N0Uj8eio) · [Read the technical design](system/README.md) · [Inspect the development predictions](output/dev.jsonl)

## Try the analyst demo

You need Python 3.9 or later and a POSIX shell. The synthetic example runs offline using the standard library.

```sh
git clone https://github.com/ronitbhatia/alibi-screening.git
cd alibi-screening
OPENROUTER_API_KEY= python3 -S -m alibi.reproduce system/example output/example.jsonl
python3 -S -m alibi.casefile output/example.jsonl
```

Open **http://127.0.0.1:8765/**. The queue starts with the unresolved hit. Use **J/K** to move through hits, **E** to inspect rule outcomes and decisive records, and **T** to compare five independent runs. Stop the server with **Ctrl+C**.

The demo follows three hits for Mira Arden, whose approved identity check gives a birth date of 10 June 1990.

| Hit | What the analyst sees | Decision |
| --- | --- | --- |
| H-01 | The listed birth year is 1960. The approved check establishes a 30-year discrepancy. | DIFFERENT_ENTITY |
| H-02 | The listed name, birth year 1990 and UK country all agree. | SAME_ENTITY |
| H-03 | The name agrees and the listing gives no usable birth date. | REVIEW |

One hit clears. The other two keep the alert open.

## Run on a screening dataset

From the repository root, pass the data-room JSONL folder and the destination file to the judge entry point.

```sh
system/run screening/dev output/dev.jsonl
python3 -S screening/validate.py --data screening/dev --output output/dev.jsonl --score screening/dev/practice_labels.jsonl
```

The event supplies the `screening/` folder and validator to participants. The runner writes one row per hit with its alert ID, hit ID, resolved customer ID, decision, reason code, explanation and decisive evidence IDs. The adjacent `dev.trace.jsonl` contains the candidate comparisons, rule outcomes and model-read evidence used by the analyst view.

## Where AI enters the decision

1. **Identify the customer.** Follow account, login and email links. Merge records supported by an approved document number or by a shared phone and full DOB. Prefer the latest approved identity check.
2. **Resolve the listing.** Compare aliases, dates and countries. Search results must match the listing category and a country tied to the hit.
3. **Read unresolved text.** With `OPENROUTER_API_KEY` set, ALIBI sends an individual unresolved sentence to OpenRouter. It requires a source quote for each extracted fact and checks that quote locally. The default route is `openrouter/free`. Set `ALIBI_MODEL` to select a specific endpoint.
4. **Apply the identity policy.** A verified contradiction can clear a hit. Sufficient agreement can confirm it. Missing, conflicting or unverified evidence sends dependent decisions to review.

To test your configured key with a single synthetic sentence, run `python3 -S -m alibi.smoke`. To compare five full runs on your data, run `python3 -S -m alibi.reproduce screening/dev output/live.jsonl`.

## Verification

The published development output covers **819 hits**. The organizers' validator reported zero problems. On the **50 labeled practice hits**, all decisions, reason codes and customer records were correct, with zero unsafe clearances and **108 of 108 cost points saved** against sending every hit to review. The other 769 hits have no published practice labels.

| Five-run check on development data | Result |
| --- | --- |
| Empty key | All 819 decisions and codes agreed. Verdict files were byte-identical. Each run took about 2.5–2.8 seconds. |
| Live key | 808 of 819 hits agreed on both decision and code. The harness routed all 11 disagreements to review. Runs took about 4–81 seconds. |

Run the regression suite with `python3 -S -m unittest discover -s tests`. Workbook and policy checks run when their participant files are available. The system README describes the model prompt, evidence format, failure handling and three worked cases in detail. [System README](system/README.md)
