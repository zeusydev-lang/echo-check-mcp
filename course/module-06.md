# Module 6: Prove it works (evals)

## Goal

By the end of this module, one command generates fresh test files, scores the
engine against their answer keys, and checks that the LLM summary only uses
real numbers.

```powershell
uv run python evals/run_evals.py
```

## The concept

**An eval is a test you can score.** A unit test says pass or fail. An eval
says *how well*: 226 of 226 pairs found, 0 false alarms. Run it after every
change, and you'll see a drop before your users do.

**The generator knows the truth.** It plants duplicates on purpose and writes
an answer key. Different seeds give different files, so we don't tune the
engine to one sample.

**Count pairs, not groups.** A group of 3 lines is 3 pairs: (a, b), (a, c),
(b, c). Pairs are easy to compare with the answer key.

**Four outcomes per rule:**

| | Meaning |
| --- | --- |
| TP | A reported pair the key expects for this rule. |
| FP | A reported pair the key doesn't list at all: a false alarm. |
| FN | An expected pair the rule missed. |
| cross | A pair the key lists for *another* rule. |

`precision = TP / (TP + FP)` asks: when it reports something, is it right?
`recall = TP / (TP + FN)` asks: did it find everything?

**Cross-rule hits aren't mistakes.** An exact copy of a line also has the same
trace number and the same payment, so three rules report it. That's correct.
We count those pairs but don't score them.

**The bar.** Exact rules (`exact_line`, `trace_number`, `same_payment`) must
score 100%. `near_amount` is a tolerance rule. Real files can have honest
near-matches, so we watch it rather than demand perfection.

**Checking an LLM with code.** You can't unit-test prose, but you can test
facts. Every number in the summary must appear in the data Claude was given.
If the summary says "12 groups" and no 12 exists, that's an invented number.
A code check like this is cheap, exact and runs every time.

**LLM-as-judge, optional.** Some things code can't check, such as "is this
clear?". A second model grades the summary with a short rubric: clarity 1-5,
actionable yes/no, names the resent batch yes/no. It costs API calls and is
itself an opinion, so it's behind `--judge`.

## Code walkthrough

1. **`pairs_from_groups`** (`src/echo_check/evaluate.py`) turns groups into pairs.
2. **`expected_pairs`** reads the answer key. `EXPECTED_RULE` maps each planted
   type to its rule, and a resent batch maps to `same_payment`.
3. **`score_rules`** uses set operations: `reported & wanted` is TP,
   `reported - all_expected` is FP, `wanted - reported` is FN.
4. **`check_summary_numbers`** pulls every number out of the summary and checks it
   against every number in the digest Claude saw. Leading zeros are ignored, so
   a trace number written `0091…` still matches `91…`.
5. **`judge_summary`** is one `messages.parse` call with a `JudgeVerdict` model.
6. **`evals/run_evals.py`** ties it together and exits with 1 when the bar isn't
   met, so CI (Module 7) can use it.

Try it:

```powershell
uv run python evals/run_evals.py --seeds 1 2 3 4 5 6 7 8 9 10
uv run --env-file .env python evals/run_evals.py --summary   # 1 API call per seed
uv run --env-file .env python evals/run_evals.py --judge     # 2 API calls per seed
```

## Exercise

1. In `configs/nacha.yaml`, change `near_amount`'s `max_diff` to 100 and run the
   evals. Which score drops, and why?
2. Remove `amount` from `same_payment`'s keys. Precision should drop. Look at
   which pairs became false positives.
3. Before this module, `individual_id` wasn't masked, and a real summary quoted
   `EMP225772`. Run `pytest tests/test_evaluate.py -k unmasked` and read the test.
   Why would that summary fail the number check today?

## What can go wrong

- **Scoring groups instead of pairs.** One big wrong group would count as one
  mistake. Pairs make every wrong link count.
- **Calling cross-rule hits false positives.** You'd "fix" a correct engine.
- **Testing one file.** Use several seeds and sizes. Bugs hide in shapes you
  didn't try.
- **Trusting the judge too much.** A judge is a model with opinions. Use code
  checks for facts and the judge for style.
- **Surprise bills.** The script prints how many API calls it will make. Summary
  evals are opt-in.
