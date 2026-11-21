# Milestone 2 evidence checklist

## Review scope

Bennett University’s public SCSE4041 syllabus covers prompt chaining, task decomposition, and systematic evaluation and iteration. The official Milestone 2 handout or grading rubric was unavailable in the project and could not be verified publicly. The checklist maps the nine supplied requirements to evidence in this repository. [Bennett University syllabus](https://www.bennett.edu.in/wp-content/uploads/2025/03/New-Syllabus-Booklet_BTech-CSE_Global_2025-29.pdf)

| Requirement | Status | Evidence |
| --- | --- | --- |
| Versioned prompt templates | Complete | [V1](../prompts/v1/generate.txt), [V2](../prompts/v2/generate.txt), [V3](../prompts/v3/generate.txt), and [V3 repair](../prompts/v3/repair.txt). |
| Final workflow prompts | Complete | [Generation](../prompts/final/generate.txt) and [validation-guided repair](../prompts/final/repair.txt). |
| Prompt iteration log | Partial | [Changes, rationale, and measured controlled outcomes](prompt-iteration-log.md). Live prompt quality and latency remain unmeasured. |
| At least 10 inputs, including 3 edge cases | Complete for controlled integration | [36 executed case/version records](evidence/controlled-case-results.csv) cover 12 unique questions and three edge cases. Each record includes expected output, actual output, and Pass/Fail. The [34-case live benchmark](../data/evaluation_questions.json) includes eight edge cases. |
| Actual tests and screenshots | Partial | [Pytest output](evidence/pytest-2026-11-21.txt), [provider attempt log](evidence/live-evaluation-attempts-2026-11-21.txt), and [four local browser screenshots](milestone-2.md#screenshots-checklist). Screenshots with live model output and live evaluation charts are pending. |
| Dated changes | Complete | [Changelog](../CHANGELOG.md). |
| Workflow, decomposition, input-output flow, and architecture | Complete | [Task decomposition](task-decomposition.md) and [architecture](architecture.md). |
| Module status | Complete | [Module status table](module-status.md). |
| Evidence checklist | Complete | This page. The official rubric remains unavailable for reconciliation. |

The three controlled edge cases cover ambiguity (C8-27), a write request (C8-30), and a request for data outside the available schema (C8-34). Unsafe cases pass if the provider explicitly refuses or the validator blocks an unsafe statement. Refusal rates still distinguish explicit refusals from validator enforcement.
