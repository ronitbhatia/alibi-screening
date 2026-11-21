"""Policy comparisons for identifying the screened customer."""
import json
import re
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from functools import lru_cache
from pathlib import Path

VARIANTS = json.loads(Path(__file__).with_name("name_variants.json").read_text())
PLACEHOLDERS = {"na", "test", "unknown", "xxx", "none", "null"}


def name_policy(data):
    equivalents = {}
    for group in data["groups"]:
        for part in group:
            equivalents.setdefault(part, set()).update(group)
    return set(data["company_legal_forms"]), equivalents


NAME_POLICY = ContextVar("name_policy", default=name_policy(VARIANTS))


@contextmanager
def dataset_names(path):
    if not path.is_file():
        yield
        return
    token = NAME_POLICY.set(name_policy(json.loads(path.read_text(encoding="utf-8"))))
    try:
        yield
    finally:
        NAME_POLICY.reset(token)


def fold(value):
    text = unicodedata.normalize("NFKD", str(value or "").strip().casefold())
    return "".join(c for c in text if not unicodedata.combining(c))


# Country names, codes and demonyms in the browse data.
COUNTRIES = {}
for line in """
AR Argentina
AT Austria Austrian
AU Australia Australian
BD Bangladesh Bangladeshi
BR Brazil Brazilian
CA Canada Canadian
CI Cote_d'Ivoire
DE Germany German
DK Denmark Danish
ES Spain Spanish
FI Finland Finnish
FR France French
GB United_Kingdom British UK
GH Ghana Ghanaian
ID Indonesia Indonesian
IE Ireland Irish
IN India Indian
IT Italy Italian
KE Kenya Kenyan
MX Mexico Mexican
NG Nigeria Nigerian
NL Netherlands Dutch
NO Norway Norwegian
PE Peru Peruvian
PK Pakistan Pakistani
PL Poland Polish
PT Portugal Portuguese
RO Romania Romanian
RS Serbia Serbian
SE Sweden Swedish
SG Singapore Singaporean
US United_States American USA
ZA South_Africa South_African
CH Switzerland Swiss
TH Thailand Thai
VN Vietnam Vietnamese
EC Ecuador Ecuadorian
HT Haiti Haitian
PA Panama Panamanian
""".strip().splitlines():
    code, *forms = line.split()
    for form in [code, *forms]:
        COUNTRIES[fold(form.replace("_", " "))] = code


def country(value):
    key = fold(value)
    return COUNTRIES.get(key, key)


@lru_cache(maxsize=100000)
def date_parts(value):
    text = str(value or "").strip()
    if not text:
        return ()
    for fmt, length in (("%Y-%m-%d", 3), ("%d/%m/%Y", 3), ("%B %d, %Y", 3),
                        ("%d %B %Y", 3), ("%Y", 1), ("%Y-%m", 2), ("%B %Y", 2)):
        try:
            date = datetime.strptime(text, fmt)
            return (date.year, date.month, date.day)[:length]
        except ValueError:
            continue
    return ()


def parts(value, company):
    text = re.sub(r"\([^()]*\)|\[[^\]]*\]|\{[^}]*\}", " ", fold(value))
    words = re.findall(r"[^\W\d_]+", text.replace("'", "").replace("’", ""))
    return tuple(dict.fromkeys(w for w in words if len(w) > 1 and w not in PLACEHOLDERS
                               and not (company and w in NAME_POLICY.get()[0])))


def part_matches(a, b):
    if a == b or b in NAME_POLICY.get()[1].get(a, ()):
        return True
    return edit_matches(a, b)


@lru_cache(maxsize=60000)
def edit_matches(a, b):
    limit = 0 if min(len(a), len(b)) < 4 else (1 if min(len(a), len(b)) <= 6 else 2)
    if not limit or abs(len(a) - len(b)) > limit:
        return False
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        row = [i]
        for j, right in enumerate(b, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j-1] + (left != right)))
        if min(row) > limit:
            return False
        previous = row
    return previous[-1] <= limit


def names_agree(first, second, company=False):
    a, b = parts(first, company), parts(second, company)
    if not a or not b:
        return False
    if len(a) > len(b):
        a, b = b, a
    # Masks ensure a word cannot be counted twice, including joined/split spellings.
    edges = []
    for i, left in enumerate(a):
        for j, right in enumerate(b):
            if part_matches(left, right):
                edges.append((1 << i, 1 << j))
            if j + 1 < len(b) and left == right + b[j+1]:
                edges.append((1 << i, 3 << j))
            if i + 1 < len(a) and left + a[i+1] == right:
                edges.append((3 << i, 1 << j))
    full = (1 << len(a)) - 1
    return any(x == full or any(not (x & u or y & v) for u, v in edges)
               for x, y in edges)
