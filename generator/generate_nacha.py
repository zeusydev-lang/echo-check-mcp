#!/usr/bin/env python3
"""Generate synthetic NACHA (ACH) files with planted duplicates.

All data is fake. For each run it writes two files:
  <out>.ach           the fixed-width file (every record is 94 characters)
  <out>.answers.json  the answer key: every planted duplicate and its line numbers

Usage:
  uv run python generator/generate_nacha.py --out data/sample --seed 42
"""

import argparse
import copy
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

RECORD_LENGTH = 94
BLOCKING_FACTOR = 10

FIRST_NAMES = [
    "AVA", "LIAM", "NOAH", "EMMA", "OLIVIA", "MASON", "MIA", "LUCAS", "ARJUN",
    "PRIYA", "SOFIA", "ETHAN", "CHLOE", "DIEGO", "HANA", "OMAR", "LEILA", "KAI",
    "NINA", "RAVI", "ZOE", "LEO", "MAYA", "IVAN", "ANIKA", "SAM", "ELLA", "JADE",
]
LAST_NAMES = [
    "SMITH", "GARCIA", "NGUYEN", "PATEL", "KIM", "JOHNSON", "LOPEZ", "MENON",
    "BROWN", "DAVIS", "MARTIN", "WILSON", "CHEN", "TAYLOR", "SINGH", "WRIGHT",
    "NAIR", "KHAN", "ROSSI", "MOORE", "CLARK", "HALL", "YOUNG", "REYES",
]


# ---------- field helpers ----------

def alpha(value, length):
    """Left-justify text and pad with spaces (alphanumeric NACHA fields)."""
    return str(value)[:length].ljust(length)


def num(value, length):
    """Right-justify a number and pad with zeros (numeric NACHA fields)."""
    text = str(value)
    if len(text) > length:
        raise ValueError(f"{value} does not fit in {length} digits")
    return text.rjust(length, "0")


def routing_check_digit(first8):
    """ABA routing check digit: weights 3,7,1 repeated, then mod 10."""
    weights = [3, 7, 1, 3, 7, 1, 3, 7]
    total = sum(int(d) * w for d, w in zip(first8, weights))
    return str((10 - total % 10) % 10)


def checked(line):
    if len(line) != RECORD_LENGTH:
        raise ValueError(f"record is {len(line)} chars, expected 94: {line!r}")
    return line


# ---------- record builders ----------

def file_header(dest, origin, created):
    return checked(
        "1"                          # 1      record type
        + "01"                       # 2-3    priority code
        + " " + dest                 # 4-13   immediate destination
        + " " + origin               # 14-23  immediate origin
        + created.strftime("%y%m%d") # 24-29  file creation date
        + "0900"                     # 30-33  file creation time
        + "A"                        # 34     file ID modifier
        + "094"                      # 35-37  record size
        + "10"                       # 38-39  blocking factor
        + "1"                        # 40     format code
        + alpha("ECHO TEST BANK", 23)    # 41-63 destination name
        + alpha("ECHO CHECK DEMO CO", 23)  # 64-86 origin name
        + alpha("", 8)               # 87-94  reference code
    )


def batch_header(batch_no, company_id, odfi, effective):
    return checked(
        "5"                          # 1      record type
        + "220"                      # 2-4    service class (credits only)
        + alpha("ECHO DEMO CO", 16)  # 5-20   company name
        + alpha("", 20)              # 21-40  discretionary data
        + company_id                 # 41-50  company ID
        + "PPD"                      # 51-53  SEC code
        + alpha("PAYROLL", 10)       # 54-63  entry description
        + effective.strftime("%y%m%d")  # 64-69 descriptive date
        + effective.strftime("%y%m%d")  # 70-75 effective entry date
        + "   "                      # 76-78  settlement date (bank fills)
        + "1"                        # 79     originator status
        + odfi                       # 80-87  originating DFI
        + num(batch_no, 7)           # 88-94  batch number
    )


def entry_detail(e):
    return checked(
        "6"                          # 1      record type
        + "22"                       # 2-3    transaction code (checking credit)
        + e["rdfi"]                  # 4-11   receiving DFI routing (8)
        + routing_check_digit(e["rdfi"])  # 12 check digit
        + alpha(e["account"], 17)    # 13-29  account number
        + num(e["amount"], 10)       # 30-39  amount in cents
        + alpha(e["individual_id"], 15)  # 40-54 individual ID
        + alpha(e["name"], 22)       # 55-76  individual name
        + "  "                       # 77-78  discretionary data
        + "0"                        # 79     addenda indicator
        + e["trace"]                 # 80-94  trace number
    )


def batch_control(batch_no, entries, company_id, odfi):
    entry_hash = sum(int(e["rdfi"]) for e in entries) % 10**10
    credits = sum(e["amount"] for e in entries)
    return checked(
        "8"                          # 1      record type
        + "220"                      # 2-4    service class
        + num(len(entries), 6)       # 5-10   entry/addenda count
        + num(entry_hash, 10)        # 11-20  entry hash
        + num(0, 12)                 # 21-32  total debits
        + num(credits, 12)           # 33-44  total credits
        + company_id                 # 45-54  company ID
        + alpha("", 19)              # 55-73  message auth code
        + alpha("", 6)               # 74-79  reserved
        + odfi                       # 80-87  originating DFI
        + num(batch_no, 7)           # 88-94  batch number
    ), entry_hash, credits


def file_control(batch_count, block_count, entry_count, entry_hash, credits):
    return checked(
        "9"                          # 1      record type
        + num(batch_count, 6)        # 2-7    batch count
        + num(block_count, 6)        # 8-13   block count
        + num(entry_count, 8)        # 14-21  entry/addenda count
        + num(entry_hash % 10**10, 10)  # 22-31 entry hash
        + num(0, 12)                 # 32-43  total debits
        + num(credits, 12)           # 44-55  total credits
        + alpha("", 39)              # 56-94  reserved
    )


# ---------- synthetic data ----------

class Generator:
    def __init__(self, seed, odfi):
        self.rng = random.Random(seed)
        self.odfi = odfi
        self.trace_seq = 0
        self.uid = 0
        self.used_accounts = set()
        self.used_ids = set()

    def next_trace(self):
        self.trace_seq += 1
        return self.odfi + num(self.trace_seq, 7)

    def next_uid(self):
        self.uid += 1
        return self.uid

    def unique(self, used, make):
        while True:
            value = make()
            if value not in used:
                used.add(value)
                return value

    def new_entry(self):
        r = self.rng
        return {
            "uid": self.next_uid(),
            "rdfi": num(r.randint(1_000_000, 32_999_999), 8),
            "account": self.unique(self.used_accounts,
                                   lambda: str(r.randint(10**9, 10**12 - 1))),
            "amount": r.randint(50_00, 4_500_00),  # $50.00 to $4,500.00
            "individual_id": self.unique(self.used_ids,
                                         lambda: f"EMP{r.randint(0, 999_999):06d}"),
            "name": f"{r.choice(LAST_NAMES)} {r.choice(FIRST_NAMES)}",
            "trace": self.next_trace(),
        }

    def clone(self, entry, new_trace=True):
        dup = copy.deepcopy(entry)
        dup["uid"] = self.next_uid()
        if new_trace:
            dup["trace"] = self.next_trace()
        return dup


def insert_after(batch, original, dup, rng):
    """Insert dup somewhere after the original's position in the same batch."""
    start = batch.index(original) + 1
    batch.insert(rng.randint(start, len(batch)), dup)


def build(args):
    rng_dates = random.Random(args.seed)
    created = date(2026, 1, 5) + timedelta(days=rng_dates.randint(0, 300))
    effective = created + timedelta(days=1)
    odfi = "09100001"
    company_id = "1" + num(random.Random(args.seed).randint(0, 999_999_999), 9)

    g = Generator(args.seed, odfi)
    r = g.rng
    batches = [[g.new_entry() for _ in range(args.entries)] for _ in range(args.batches)]

    # The batch to resend later is kept clean: no planted duplicates in or from it.
    source_no = r.randint(1, len(batches)) if args.resend_batch else None
    others = [b for i, b in enumerate(batches, start=1) if i != source_no]

    # Pick distinct originals so planted groups never overlap.
    pool = [(b, e) for b in others for e in b]
    r.shuffle(pool)
    planted = []

    def take():
        return pool.pop()

    for _ in range(args.dupes):
        # 1. exact_line: the same record appears twice (same trace number)
        batch, orig = take()
        dup = g.clone(orig, new_trace=False)
        insert_after(batch, orig, dup, r)
        planted.append({"type": "exact_line", "original": orig["uid"], "duplicates": [dup["uid"]],
                        "rule": "identical line, same trace_number"})

        # 2. trace_collision: different payment reusing an existing trace number
        batch, orig = take()
        dup = g.new_entry()
        dup["trace"] = orig["trace"]
        insert_after(batch, orig, dup, r)
        planted.append({"type": "trace_collision", "original": orig["uid"], "duplicates": [dup["uid"]],
                        "rule": "same trace_number, different payment"})

        # 3. same_payment: same person, account and amount, new trace number
        _, orig = take()
        dup = g.clone(orig)
        target = r.choice(others)
        target.insert(r.randint(0, len(target)), dup)
        planted.append({"type": "same_payment", "original": orig["uid"], "duplicates": [dup["uid"]],
                        "rule": "same account + amount + individual_id"})

        # 4. near_amount: same person and account, amount off by a few dollars
        _, orig = take()
        dup = g.clone(orig)
        delta = r.choice([-1, 1]) * r.randint(1, 500)  # up to $5.00
        dup["amount"] = max(1, orig["amount"] + delta)
        target = r.choice(others)
        target.insert(r.randint(0, len(target)), dup)
        planted.append({"type": "near_amount", "original": orig["uid"], "duplicates": [dup["uid"]],
                        "rule": f"same account + individual_id, amount differs by {abs(delta)} cents"})

    # 5. resent_batch: one whole batch sent again with new trace numbers
    if args.resend_batch:
        source = batches[source_no - 1]
        resent = [g.clone(e) for e in source]
        batches.append(resent)
        planted.append({"type": "resent_batch", "source_batch": source_no, "resent_batch": len(batches),
                        "pairs": [[o["uid"], d["uid"]] for o, d in zip(source, resent)],
                        "rule": "every entry of the batch repeated in a new batch"})

    # ---------- assemble the file ----------
    lines = [file_header("091000019", "123456780", created)]
    line_of = {}
    total_hash = total_credits = total_entries = 0
    for batch_no, batch in enumerate(batches, start=1):
        lines.append(batch_header(batch_no, company_id, odfi, effective))
        for e in batch:
            lines.append(entry_detail(e))
            line_of[e["uid"]] = len(lines)  # 1-based line number
        control, h, c = batch_control(batch_no, batch, company_id, odfi)
        lines.append(control)
        total_hash += h
        total_credits += c
        total_entries += len(batch)

    block_count = math.ceil((len(lines) + 1) / BLOCKING_FACTOR)
    lines.append(file_control(len(batches), block_count, total_entries, total_hash, total_credits))
    while len(lines) % BLOCKING_FACTOR:
        lines.append("9" * RECORD_LENGTH)  # filler records to complete the block

    # ---------- answer key with line numbers ----------
    answers = []
    for p in planted:
        if p["type"] == "resent_batch":
            answers.append({
                "type": p["type"], "rule": p["rule"],
                "source_batch": p["source_batch"], "resent_batch": p["resent_batch"],
                "pairs": [[line_of[a], line_of[b]] for a, b in p["pairs"]],
            })
        else:
            answers.append({
                "type": p["type"], "rule": p["rule"],
                "original_line": line_of[p["original"]],
                "duplicate_lines": [line_of[u] for u in p["duplicates"]],
            })

    key = {
        "seed": args.seed,
        "record_length": RECORD_LENGTH,
        "total_lines": len(lines),
        "batches": len(batches),
        "entry_records": total_entries,
        "planted_count": sum(len(a.get("pairs", [None])) for a in answers),
        "planted": answers,
    }
    return lines, key


def main():
    parser = argparse.ArgumentParser(description="Generate a synthetic NACHA file with planted duplicates.")
    parser.add_argument("--out", default="data/sample", help="output path without extension")
    parser.add_argument("--batches", type=int, default=3, help="number of batches before duplicates")
    parser.add_argument("--entries", type=int, default=8, help="entries per batch before duplicates")
    parser.add_argument("--dupes", type=int, default=1, help="how many of each duplicate type to plant")
    parser.add_argument("--no-resend-batch", dest="resend_batch", action="store_false",
                        help="skip planting a resent batch")
    parser.add_argument("--seed", type=int, default=42, help="random seed (same seed = same file)")
    args = parser.parse_args()

    lines, key = build(args)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".ach").write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    out.with_suffix(".answers.json").write_text(json.dumps(key, indent=2), encoding="utf-8")
    print(f"Wrote {out.with_suffix('.ach')} ({key['total_lines']} lines, "
          f"{key['entry_records']} entries, {key['planted_count']} planted duplicates)")
    print(f"Wrote {out.with_suffix('.answers.json')}")


if __name__ == "__main__":
    main()
