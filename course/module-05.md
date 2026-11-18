# Module 5: Let Claude explain the results

## Goal

By the end of this module, `dupcheck --summary` (and the MCP tool
`summarize_results`) gives a short written summary: what happened, the likely
cause, which batches, and what to do next. Claude never sees full account
numbers or names.

## The concept

**Counts, not data.** The engine already did the hard, exact work. Claude gets
a small JSON digest: counts per rule, a per-batch breakdown and 3 masked
sample groups per rule. That's a few kilobytes even for a million-line file.

**Batches tell the story.** A single duplicate is a mistake. Eight duplicates
that all link batch 3 to batch 4 is a pattern: the batch was sent twice. So
the engine now reports `groups_by_batches`:

```json
"same_payment": {"1": 1, "2": 1, "3-4": 8}
```

"3-4" means a group with one line in batch 3 and one in batch 4. The config
says where batch headers are, so this still works for other file formats:

```yaml
batch: {header_starts_with: "5", id_start: 88, id_length: 7}
```

**Structured output.** We don't parse free text. The SDK's
`client.messages.parse(..., output_format=Summary)` makes Claude's reply
match a Pydantic model:

```python
class Summary(BaseModel):
    headline: str
    likely_cause: str
    affected_batches: list[str]
    suggested_action: str
```

**Don't trust, check.** The prompt says "use only numbers from the results".
The code also checks what it can: any batch the summary names that isn't in
the data is removed, with a warning. Module 6 adds a full number check.

## Code walkthrough

1. **`BatchSpec`** (`config.py`) is the optional `batch` section, validated like
   every other field.
2. **`BatchTracker`** (`engine.py`) stores only the line number of each batch
   header. `bisect` finds the batch for any line, so memory stays small.
3. **`build_summary_input`** (`summary.py`) builds the digest. It refuses an
   unmasked result, so a mistake can't leak data.
4. **`build_request`** puts everything sent to the API in one dict:
   model (`ECHO_CHECK_MODEL`, default `claude-opus-5-5`), system prompt, the
   digest inside `<results>` tags, and `output_format=Summary`.
5. **`summarize`** calls the API, handles a refusal, and runs `check_summary`.
6. **`make_client`** is a tiny function that tests replace with a fake. No test
   calls the real API.
7. **The no-leak test** reads every account number and name from the sample file
   and checks none of them appear in the request.

Try it (needs an API key):

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
uv run dupcheck data/sample.ach --config configs/nacha.yaml --summary --out data/results.json
```

## Exercise

1. Run the summary on `data/sample.ach`. Does it find the resent batch? Check its
   numbers against `data/results.json`.
2. Remove the `batch` line from a copy of the config and run it again. What does
   the summary say about the cause now?
3. In `test_summary.py`, make the fake client return `affected_batches: ["99"]`.
   What happens?

## What can go wrong

- **Leaking data through samples.** Samples come from the masked result only.
  The `_line` key is masked too. If you add a new sensitive field, mark it
  `sensitive: true` or it will be sent.
- **Invented numbers.** LLMs like round, confident numbers. The prompt forbids
  them, and Module 6 checks every number against the stats.
- **Refusals.** Claude can decline a request (`stop_reason: "refusal"`). The code
  raises a clear `SummaryError` and the check results still print. In
  production you can add the API's server-side `fallbacks` option, which
  retries the request on another model.
- **Cost and keys.** Each summary is one API call. Keep the key in an
  environment variable, never in the config or the repo.
