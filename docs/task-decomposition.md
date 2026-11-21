# Prompt workflow and task decomposition

| Step | Ownership | Input | Output and next step |
| --- | --- | --- | --- |
| 1. Build context | Database and UI | Question, selected prompt version, provider, database choice | Question and inspected table, column, and relationship metadata. |
| 2. Generate | Provider via the selected prompt | V1 receives the question. V2 receives the question and schema. V3 also receives examples and explicit response rules. | SQL text, refusal, clarification, or a response that cannot be decoded. |
| 3. Decode and validate | Python, SQLGlot, and SQLite | Raw response and read-only SQLite connection | Parsed SQL or a terminal outcome. A preparable SELECT proceeds. Eligible V3 validation errors enter step 4. |
| 4. Repair once | Provider, then the same validator | Question, actual schema, original SQL, validation error | Corrected SQL or a terminal outcome. Record both attempts. A repair is never requested for an unsafe query. |
| 5. Execute and explain | SQLite and UI | Validated SQL | Bounded result rows and an explanation. The UI can describe the SQL clauses locally when the model supplies no explanation. |
| 6. Score and export | Evaluation runner | Holdout question, reference SQL or expected behavior, recorded response and rows | Expected and actual outputs, Pass/Fail, error category, aggregate metrics, CSV and charts. |

The final workflow has two distinct model prompts, [generation](../prompts/final/generate.txt) and [repair](../prompts/final/repair.txt). V1 and V2 are comparison strategies. Schema inspection, SQL validation, SQL execution, and result scoring run locally. See the [system architecture and input-output diagrams](architecture.md).
