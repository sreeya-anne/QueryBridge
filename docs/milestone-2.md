# Milestone 2 implementation summary

See the [Milestone 2 evidence checklist](milestone-2-checklist.md), [prompt iteration log](prompt-iteration-log.md), [module status](module-status.md), and [task decomposition](task-decomposition.md).

- Implemented a reproducible university SQLite fixture and a 34-case held-out benchmark.
- Implemented V1 zero-shot prompting, V2 schema grounding, and V3 few-shot structured output with one validation-guided repair.
- Built a Streamlit playground, database explorer, prompt lab, evaluation dashboard, and project overview.
- Added a shared read-only SQL engine, an evaluation CLI, detailed CSV exports, and Plotly charts.

## Screenshots checklist

- [x] Playground with provider, model, and prompt selection before credentials are configured. [Query Playground](evidence/screenshots/query-playground.png)
- [x] Explorer showing one table, its columns and foreign keys, and sample records. [Database Explorer](evidence/screenshots/database-explorer.png)
- [x] Lab showing the three prompt strategies and template controls. [Prompt Engineering Lab](evidence/screenshots/prompt-lab-preview.png)
- [x] About page showing team query.exe and problem C8. [About Project](evidence/screenshots/about-project.png)
- [ ] Playground showing the provider, model, selected prompt, SQL, validation, and result table.
- [ ] Lab showing the same question across V1, V2, and V3, including exact prompts.
- [ ] A V3 repair trace if one occurs in a live run.
- [ ] Evaluation dashboard showing rates, latency, and outcome counts after a successful live run.

The four completed captures are from a local browser session on 2026-11-21 with live generation disabled. Capture live experimental screenshots and retain their CSV evidence after configuring a provider.
