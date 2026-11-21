# QueryBridge

**An Intelligent Natural Language to SQL Assistant**  
**University Prompt Engineering project · Team query.exe · Problem C8**

QueryBridge studies how prompt design affects the translation of English questions into SQLite queries. The playground exposes each model response and its validation result. The lab compares matched prompts, and the evaluation runner measures their behavior against a held-out benchmark.

## Setup

Python 3.11 or newer is required. From the project directory in Windows PowerShell, run:

    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    Copy-Item .env.example .env
    .\.venv\Scripts\python.exe database.py

On macOS or Linux use .venv/bin/python and cp in the corresponding commands. Database initialization also runs automatically on first app launch.

In .env, set OPENAI_API_KEY for the default provider and optionally set OPENAI_MODEL. To use Gemini, set GOOGLE_API_KEY and optionally GEMINI_MODEL. Use a model that supports text output in the selected API. The database explorer, prompt previews, and benchmark browser work without credentials. Live calls require network access, provider quota, and a valid key. The full evaluation makes 102 initial requests plus any V3 correction requests.

## Run the app

    .\.venv\Scripts\python.exe -m streamlit run app.py

Open the local URL printed by Streamlit. Use the sidebar to select the database, provider, and page.

- **Query Playground** generates, validates, explains, and executes an individual query.
- **Database Explorer** shows columns, foreign keys, and samples.
- **Prompt Engineering Lab** previews templates and compares live outputs and exact prompts.
- **Evaluation Dashboard** runs the benchmark, displays metrics and outcomes, and provides CSV downloads.
- **About Project** describes the problem and workflow.

## Run an experiment or the tests

    .\.venv\Scripts\python.exe evaluate.py
    .\.venv\Scripts\python.exe evaluate.py --limit 1 --out results/smoke
    .\.venv\Scripts\python.exe evaluate.py --provider Gemini
    .\.venv\Scripts\python.exe -m pytest -q

The first command runs the full OpenAI experiment. The second exercises all three versions on one question. The third runs the full experiment with Gemini. In the dashboard, choose Quick for 12 fixed questions (36 initial requests) or Full for 34 questions (102 initial requests). On the CLI, use `--size quick` to select the same quick set. V3 may make additional requests for repair.

Each attempted run writes `results/evaluation_results_<UTC timestamp>.csv`, `results/evaluation_summary_<UTC timestamp>.csv`, and two timestamped HTML charts by default. The detail CSV preserves API errors and timings. The summary reports evaluated cases and API failures separately. Use `--out` to select another directory.

### Resume a partial evaluation

In the Evaluation Dashboard, select the same database, provider, model and evaluation size. Choose the saved checkpoint under **Resume a partial run**, then click **Resume evaluation**. An interrupted run from before checkpoints were added can be selected by its results CSV filename. Successful evaluations, including scored failures, are reused. If a V3 repair request failed, the original response is reused and only the repair request is sent again.

Wait until the provider's retry time has passed. For a daily quota, verify that quota is available again before resuming. Gemini evaluation calls run sequentially with at least five seconds between the end of one request and the start of the next, including V3 repair requests. You can set a longer delay. Automatic retries are off by default. Opting in allows up to two additional requests for confirmed temporary rate limits, and each may incur charges. Daily and unclassified HTTP 429 errors always pause. A provider Retry-After is enforced on resume, even for an HTTP error other than 429.

For a checkpoint, use the same arguments that selected the original question set:

    .\.venv\Scripts\python.exe evaluate.py --provider Gemini --size quick --resume results/evaluation_checkpoint_<timestamp>.json

To continue the pre-checkpoint Quick run, use its detail CSV:

    .\.venv\Scripts\python.exe evaluate.py --provider Gemini --size quick --resume-csv results/evaluation_results_20261121T085055_919105Z.csv

The evaluator checkpoints responses before scoring and saves each case result as it finishes. The Partial/Complete CSV snapshots remain separate. Comparison metrics use only questions evaluated by every selected prompt version. Per-version API request and evaluation counts include the rest of the run history.

## Prompt strategies

The [Milestone 2 evidence checklist](docs/milestone-2-checklist.md) links the versioned prompt artifacts, controlled test evidence, and remaining live checks.

| Version | Intervention | Question under study |
| --- | --- | --- |
| V1 | Minimal zero-shot instruction | How well does the model infer a plausible query without schema context? |
| V2 | Actual table names, typed columns, foreign keys, and output restrictions | Does grounding improve valid SQL and schema use? |
| V3 | V2-style grounding, separate worked examples, JSON response, ambiguity handling, and one validation-guided correction for eligible errors | How does a structured generation and correction pipeline affect accuracy and latency? |

The V3 detail export includes original and corrected SQL, prompts, raw responses, and validation outcomes. The summary describes final pipeline behavior. Compare initial validation columns to isolate changes before repair.

## Data and validation

The deterministic SQLite fixture contains 120 synthetic students, 5 departments, 20 courses, 10 instructors, and 360 enrollments. Departments connect to students, instructors, and courses. Courses reference instructors, and enrollments link students to courses with term and score. The separate JSON benchmark contains 34 questions, including 26 executable reference queries, 3 clarification cases, and 5 unsafe requests.

SQLGlot screens for a single SELECT. SQLite prepares it under an authorizer that restricts reads to known tables, then executes it through a read-only connection. A progress handler limits execution to approximately two seconds. The query engine also caps each result at 5,000 rows and approximately 8 MB.

## Metrics and limitations

See [experiment methodology](docs/experiment-methodology.md) for denominators, comparison rules, and evidence fields. The project uses Streamlit, SQLite, OpenAI Responses API, optional Gemini, Pandas, SQLGlot, Plotly, python-dotenv, and pytest.

The included benchmark covers one small synthetic schema. Result equivalence on that fixture can accept different SQL that happens to return the same rows. V3 combines several interventions, so the experiment cannot attribute a gain to any one technique. Provider defaults may produce variations across repeated runs. The database selector lists the supplied synthetic database. Spider import is outside this implementation.
