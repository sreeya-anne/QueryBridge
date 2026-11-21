# Prompt iteration log

## 2026-11-21

| Version | Change | Why | Measured controlled integration result |
| --- | --- | --- | --- |
| V1 | Minimal zero-shot request for SQLite SQL. | Establish a baseline without giving the model the database structure. | 9 Pass, 3 Fail. The evaluator detected an incorrect count, a query returned for an ambiguous request, and a query against a nonexistent unauthorized table. The write attempt was blocked. |
| V2 | Added the actual schema and relationships, identifier restrictions, and a refusal format. | Give the model the information required to select valid tables, columns, and joins. Make refusals recognizable. | 11 Pass, 1 Fail. The scorer accepted both fixture refusals and failed the ambiguous case because its fixture returned SQL. |
| V3 | Added worked examples, JSON output, explicit clarification and refusal, and one validation-guided repair. | Make response intent machine-readable and give invalid queries a bounded correction path. | 12 Pass, 0 Fail. The scorer accepted the fixture clarification and refusals. For C8-03 the first query had a schema error, the repair prepared successfully, and the executed rows matched the reference. |

The 36 observations came from running the real generation, validation, execution, and scoring pipeline against hand-authored provider responses. Failures and successful repairs were deliberately exercised. The counts measure those controlled responses and supply no evidence that V3 outperforms V2 or V1 on live model output. [Case results](evidence/controlled-case-results.csv)

Live execution accuracy, validity, schema correctness, safety compliance, and latency are unmeasured for all three versions. Both CLI attempts stopped at credential checks. [Provider attempt log](evidence/live-evaluation-attempts-2026-11-21.txt)

## Reproduce

Run from the repository root:

    .\.venv\Scripts\python.exe -m tests.milestone_cases
    .\.venv\Scripts\python.exe -m pytest -q

With provider credentials configured, run the full benchmark for a matched live comparison:

    .\.venv\Scripts\python.exe evaluate.py

Record the provider, model, date, configuration, and actual CSV metrics before describing any improvement. See the [experiment methodology](experiment-methodology.md).
