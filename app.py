"""Streamlit interface for QueryBridge."""
import pandas as pd
import plotly.express as px
import streamlit as st

from database import DATABASES, ROOT, format_schema, initialize_database, inspect_schema
from evaluate import (EVALUATION_SIZES, GEMINI_MIN_REQUEST_DELAY, METRIC_LABELS, audit_export_path,
                      evaluation_chart, latest_saved_evaluation, new_checkpoint, resumable_evaluations, run_evaluation,
                      save_evaluation, select_cases, split_outcomes)
from llm import LLMClient, ModelError, PROVIDERS, generate, provider_config
from prompts import EXAMPLES, REPAIR_TEMPLATE, STRATEGIES
from validator import QueryEngine, QueryRejected, parse_select


st.set_page_config(page_title="QueryBridge", layout="wide")


@st.cache_resource
def database_context(name):
    path = initialize_database(DATABASES[name])
    schema = inspect_schema(path)
    return path, schema, format_schema(schema), QueryEngine(path)


def dataframe(result):
    labels = []
    used = set()
    for column in result.columns:
        label = column
        suffix = 2
        while label in used:
            label = f"{column} ({suffix})"
            suffix += 1
        labels.append(label)
        used.add(label)
    return pd.DataFrame.from_records(result.rows, columns=labels)


def explain_query(sql):
    tree = parse_select(sql)
    clauses = [label for key, label in (
        ("where", "filters rows"), ("group", "groups rows"),
        ("having", "filters groups"), ("order", "sorts the results"),
        ("limit", "limits the number of rows"),
    ) if tree.args.get(key)]
    if any(node.key == "join" for node in tree.walk()):
        clauses.insert(0, "joins related records")
    if any(node.key in {"count", "sum", "avg", "max", "min"} for node in tree.walk()):
        clauses.insert(0, "computes aggregates")
    return "The query retrieves the selected values" + (", " + ", ".join(clauses) if clauses else "") + "."


def generation_card(record, engine, *, execute=False):
    if record.original_sql:
        st.caption("Original SQL")
        st.code(record.original_sql, language="sql")
        st.caption(f"Initial validation · {record.original_validation}")
    if record.corrected_sql:
        st.caption("Corrected SQL")
        st.code(record.corrected_sql, language="sql")
    if record.original_error and record.repair_prompt:
        st.caption(record.original_error)
    if record.repair_error and record.repair_error != record.error:
        st.caption(record.repair_error)
    if record.status in {"clarification", "refusal"}:
        st.warning(record.explanation or record.status.capitalize())
    elif record.status != "sql" or record.validation != "valid":
        st.error(record.error or record.status.replace("_", " ").capitalize())
    else:
        st.success("Read-only SQL validated")
        st.write(record.explanation or explain_query(record.sql))
        if execute:
            try:
                st.dataframe(dataframe(engine.execute(record.sql)), hide_index=True)
            except QueryRejected as exc:
                st.error(str(exc))
    st.caption(f"{record.provider} · {record.model} · {record.duration_seconds:.2f} s · {record.attempts} request(s)")
    with st.expander("Exact model input and response"):
        st.code(record.prompt, language="text")
        if record.raw_response:
            st.code(record.raw_response, language="text")
        if record.repair_prompt:
            st.caption("Correction request")
            st.code(record.repair_prompt, language="text")
            if record.repair_response:
                st.code(record.repair_response, language="text")


def submit(key, context, callback):
    try:
        with st.spinner("Waiting for the model…"):
            st.session_state[key] = (context, callback())
    except (ModelError, QueryRejected, OSError) as exc:
        st.session_state.pop(key, None)
        st.error(str(exc))


def stored(key, context):
    entry = st.session_state.get(key)
    return entry[1] if entry and entry[0] == context else None


st.title("QueryBridge")
st.caption("An Intelligent Natural Language to SQL Assistant · query.exe · C8")
with st.sidebar:
    page = st.radio("Navigate", ["Query Playground", "Database Explorer",
                               "Prompt Engineering Lab", "Evaluation Dashboard", "About Project"])
    database = st.selectbox("Database", list(DATABASES))
    provider = st.selectbox("Model provider", list(PROVIDERS))
    key, model = provider_config(provider)
    configured = bool(key)
    st.caption(model)
    if not configured:
        st.info(f"Set {PROVIDERS[provider][0]} to enable live generation and evaluation.")
path, schema, schema_text, engine = database_context(database)
context = (database, provider, model)

if page == "Query Playground":
    st.header(page)
    st.write("Ask a question about the synthetic university database, then inspect the SQL and its results.")
    version = st.selectbox("Prompt version", list(STRATEGIES))
    st.caption(STRATEGIES[version].technique)
    question = st.text_input(
        "Natural language question",
        placeholder="How many students joined in each admission year?",
        help="Type your question and press Enter or Tab to enable Generate SQL.",
    ).strip()
    if configured and not question:
        st.caption("Type a question, then press Enter or Tab to enable Generate SQL.")
    selection = (context, version, question)
    if st.button("Generate SQL", type="primary", disabled=not configured or not question):
        submit("play", selection, lambda: generate(LLMClient(provider), engine, version, question, schema_text))
    if record := stored("play", selection):
        generation_card(record, engine, execute=True)

elif page == "Database Explorer":
    st.header(page)
    table = st.selectbox("Table", list(schema))
    st.subheader("Columns")
    st.dataframe(pd.DataFrame(schema[table]["columns"]), hide_index=True)
    st.subheader("Foreign key relationships")
    st.dataframe(pd.DataFrame([
        {"From": f"{name}.{fk['column']}", "To": f"{fk['table']}.{fk['target']}"}
        for name, details in schema.items() for fk in details["foreign_keys"]
    ]), hide_index=True)
    st.subheader("Sample records")
    st.dataframe(dataframe(engine.execute(f'SELECT * FROM "{table}" LIMIT 10')), hide_index=True)

elif page == "Prompt Engineering Lab":
    st.header(page)
    question = st.text_area("Question for all three prompts")
    for column, (version, strategy) in zip(st.columns(3), STRATEGIES.items()):
        with column:
            st.subheader(f"{version} · {strategy.title}")
            st.write(strategy.technique)
            with st.expander("Prompt template"):
                st.code(strategy.template, language="text")
                if version == "V3":
                    st.code(EXAMPLES, language="text")
                    st.code(REPAIR_TEMPLATE, language="text")
            if question.strip():
                with st.expander("Rendered prompt preview"):
                    st.code(strategy.render(question, schema_text), language="text")
    selection = (context, question)
    if st.button("Compare prompts", type="primary", disabled=not configured or not question.strip()):
        def compare():
            client = LLMClient(provider)
            return [generate(client, engine, version, question, schema_text) for version in STRATEGIES]
        submit("lab", selection, compare)
    if records := stored("lab", selection):
        for column, record in zip(st.columns(3), records):
            with column:
                st.subheader(record.version)
                generation_card(record, engine)

elif page == "Evaluation Dashboard":
    st.header(page)
    size = st.radio("Evaluation size", list(EVALUATION_SIZES), format_func=EVALUATION_SIZES.__getitem__,
                    horizontal=True)
    cases = select_cases(size)
    selection = (context, size)
    st.write(f"{len(cases)} held-out questions · {len(cases) * len(STRATEGIES)} initial model requests.")
    st.caption("All three prompt versions use the same questions, model, and settings. V3 may make additional repair requests.")
    with st.expander("Evaluation dataset"):
        st.dataframe(pd.DataFrame(cases), hide_index=True)
    minimum = GEMINI_MIN_REQUEST_DELAY if provider == "Gemini" else 0.0
    request_delay = st.number_input("Seconds between provider requests", min_value=minimum,
                                    value=max(3.0, minimum), step=0.5, key=f"evaluation_delay_{provider}")
    retry_temporary = st.checkbox("Retry confirmed temporary rate limits (up to two additional requests)")
    if retry_temporary:
        st.caption("Retry requests may incur charges. Daily quotas and unclassified HTTP 429 errors always pause.")
    resumable = resumable_evaluations(ROOT / "results", cases, provider, model)
    previous = st.selectbox("Resume a partial run", ["Start a new run", *resumable])
    starting = previous == "Start a new run"
    label = "Run evaluation" if starting else "Resume evaluation"
    if st.button(label, type="primary", disabled=not configured):
        bar = st.progress(0, text="Loading evaluation…")
        notices = st.empty()
        checkpoint, legacy = (new_checkpoint(), None) if starting else resumable[previous]

        def report_progress(done, total):
            bar.progress(done / total, text=f"{done} / {total} case/version attempts processed")

        def evaluate():
            details, summary = run_evaluation(
                LLMClient(provider), path, cases=cases,
                progress=report_progress, checkpoint=checkpoint, legacy_csv=legacy,
                request_delay=request_delay, retry_temporary=retry_temporary, notify=notices.warning,
            )
            return details, summary, save_evaluation(details, summary)

        submit("evaluation", selection, evaluate)
        bar.empty()
    if st.button("View latest saved evaluation"):
        submit("evaluation", selection, lambda: latest_saved_evaluation(ROOT / "results", cases, provider, model))
    if evaluation := stored("evaluation", selection):
        details, summary, (detail_path, summary_path) = evaluation
        final, audit = split_outcomes(details)
        evaluated = int(summary["evaluated_cases"].sum())
        errors = int(summary["final_api_errors"].sum())
        historical = int(summary["historical_failed_attempts"].sum())
        status = summary["run_status"].iloc[0]
        planned = int(summary["planned_cases"].sum())
        matched = int(summary["matched_questions"].iloc[0])
        st.write(f"**{status}** · {evaluated} / {planned} cases evaluated · {errors} final API errors "
                 f"· {historical} historical failed attempts.")
        if pause_message := summary["pause_message"].iloc[0]:
            st.warning(pause_message)
        elif errors and status == "Partial":
            first_error = final.loc[final["api_error"], "error"].iloc[0]
            if evaluated:
                st.warning(f"Provider errors were excluded from quality metrics. First error: {first_error}")
            else:
                st.error(f"No cases could be evaluated. First provider error: {first_error}")
        st.caption(f"Comparison uses {matched} question(s) evaluated by all three prompt versions.")
        st.caption("Execution accuracy uses matched SQL questions. Clarification uses matched ambiguous questions. "
                   "Explicit refusal uses matched unsafe questions. Safety compliance uses all matched questions "
                   "and accepts a validator block on unsafe requests.")
        if matched:
            st.plotly_chart(evaluation_chart(summary), use_container_width=True)
            st.plotly_chart(px.bar(summary, x="version", y="average_response_seconds",
                                  labels={"average_response_seconds": "Mean response time (s)", "version": "Prompt"}),
                            use_container_width=True)
        st.dataframe(summary, hide_index=True, column_config=METRIC_LABELS)
        st.subheader("Final outcomes by version")
        counts = pd.crosstab(final["outcome"], final["version"])
        st.dataframe(counts)
        st.dataframe(final[["id", "version", "question", "outcome", "final_sql", "error", "duration_seconds"]],
                     hide_index=True)
        st.download_button("Download final case results", final.to_csv(index=False),
                           detail_path.name, "text/csv")
        st.download_button("Download summary", summary.to_csv(index=False),
                           summary_path.name, "text/csv")
        with st.expander("API request audit log"):
            st.dataframe(audit[["id", "version", "attempt_number", "error", "is_final_outcome"]], hide_index=True)
            st.download_button("Download API audit log", audit.to_csv(index=False),
                               audit_export_path(detail_path).name, "text/csv")

else:
    st.header(page)
    st.markdown("""
**Problem C8 · Natural Language to SQL**  
**Team · query.exe**

QueryBridge translates plain-English university questions into SQL that can be inspected and executed against synthetic data.

### Objectives

- Compare zero-shot, schema-aware, and few-shot structured prompts using identical questions.
- Validate generated SQL through SQLGlot and SQLite before read-only execution.
- Measure result accuracy, SQL validity, schema correctness, safety compliance, and latency.

### Workflow

Select a prompt → send the question to the model → parse and validate SQL → optionally repair with V3 → execute in SQLite → display or score results.

### Prompt techniques

V1 uses a zero-shot instruction. V2 adds the selected database schema. V3 adds examples, JSON output, clarification and refusal instructions, and a validation-guided correction.

### Technologies

Python, Streamlit, SQLite, OpenAI Responses API, optional Gemini, Pandas, SQLGlot, Plotly, and pytest.
""")
