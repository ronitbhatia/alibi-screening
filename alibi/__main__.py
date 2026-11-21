import argparse
import json
from pathlib import Path
from zipfile import BadZipFile

from .extract import Extractor
from .rules import adjudicate, score
from .resolve import resolve_dataset
from .store import DataError, inspect, open_dataset


def main():
    parser = argparse.ArgumentParser(description="Inspect screening inputs and validate hit-to-alert links.")
    parser.add_argument("source", nargs="?", type=Path, help="JSONL directory or browse workbook")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--adjudicate", action="store_true", help="emit verdicts as JSON Lines")
    mode.add_argument("--score", action="store_true", help="score verdicts against practice labels")
    mode.add_argument("--resolve", action="store_true", help="emit one customer resolution per alert as JSON Lines")
    parser.add_argument("--rules-only", action="store_true", help="use the Phase 3b baseline")
    args = parser.parse_args()
    extractor = None if args.rules_only else Extractor.from_env()
    source = args.source
    if source is None:
        source = Path("dev") if Path("dev").is_dir() else Path("Screening Dev Set - browse.xlsx")
    try:
        with open_dataset(source) as dataset:
            if args.adjudicate:
                for prediction in adjudicate(dataset, extractor):
                    print(json.dumps(prediction, ensure_ascii=False))
            elif args.score:
                print(json.dumps(score(adjudicate(dataset, extractor), dataset.rows("practice_labels")), indent=2))
            elif args.resolve:
                for result in resolve_dataset(dataset):
                    print(json.dumps(result.as_dict()))
            else:
                print(json.dumps(inspect(dataset), indent=2))
    except (DataError, OSError, BadZipFile) as exc:
        parser.exit(1, f"Input error: {exc}\n")


if __name__ == "__main__":
    main()
