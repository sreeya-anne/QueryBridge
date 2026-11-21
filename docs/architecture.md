# System architecture

```mermaid
flowchart LR
    UI[Streamlit] --> Pipeline[Generation pipeline]
    CLI[Evaluation CLI] --> Pipeline
    DB[(Synthetic SQLite)] --> Schema[Schema inspection]
    Schema --> Pipeline
    Pipeline --> LLM[OpenAI or Gemini]
    Pipeline --> Validator[SQLGlot and SQLite authorizer]
    Validator --> DB
    CLI --> Score[Result comparison]
    UI --> Score
    Cases[Held-out JSON benchmark] --> Score
    Score --> CSV[CSV and Plotly charts]
```

## Prompt engineering workflow

```mermaid
flowchart TD
    Q[Question and selected strategy] --> P[Render prompt]
    P --> R[Send to model]
    R --> D[Decode response]
    D --> S{SQL response?}
    S -->|No| M[Show refusal, clarification, or format error]
    S -->|Yes| V[Validate SQL]
    V -->|Eligible V3 failure| F[Send one correction prompt]
    F --> W[Decode and validate correction]
    V -->|Valid| X[Execute read-only query]
    W -->|Valid SQL| X
    W -->|Other outcome| M
    V -->|Rejected| M
    X --> O[Display or compare rows]
```

## Input and output flow

```mermaid
flowchart LR
    A[Question, version, and provider] --> B[Rendered prompt]
    B --> C[Model response]
    C --> D[Decoded intent and SQL validation]
    D -->|Accepted SQL| E[Read-only SQLite rows]
    D -->|Refusal, clarification, or error| F[Display outcome]
    E --> F
    G[Benchmark question and expected result] --> H[Case comparison]
    D --> H
    E --> H
    H --> I[Expected output, actual output, and Pass/Fail]
```

The browser accepts a question, database, provider, and strategy. The pipeline loads the selected schema and renders the prompt. V1 uses only the question. V2 and V3 add schema context, and V3 embeds worked examples. A valid SQL response produces column labels and rows in the playground. The evaluator also loads a reference query and expected behavior for each benchmark case, compares results where applicable, and exports a trace and aggregate metrics.
