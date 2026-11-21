# Module status

| Module | Status | Verification |
| --- | --- | --- |
| Synthetic database and explorer | Working | Seeded SQLite data, schema inspection, foreign keys, and reference queries exercised in pytest. |
| V1, V2, and V3 templates | Working | Saved text checked against runtime prompts, including the expanded examples and repair prompt. |
| Response decoding, read-only validation, and V3 repair | Working | Automated tests and controlled case results cover valid rows, rejection, clarification, and repair. |
| Query Playground and Prompt Engineering Lab | Partly working | Streamlit flows exercised with controlled responses. Live provider output awaits credentials. |
| OpenAI adapter | Partly working | Actual SDK request path tested with mocked HTTP. The live attempt stopped at the key check. |
| Gemini adapter | Partly working | Configuration and missing-key handling are present. Live requests remain unverified. |
| Evaluation CLI and dashboard | Partly working | Case scoring, CSV, and chart generation tested with controlled responses. Live benchmark results remain pending. |
| Official rubric reconciliation | Not started | The official Milestone 2 handout was unavailable. The evidence checklist maps to the nine requested deliverables. |
