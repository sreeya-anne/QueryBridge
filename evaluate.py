"""Run matched live experiments and export evidence and Plotly charts."""
import argparse
from collections import Counter
import csv
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from numbers import Real
from pathlib import Path
from time import perf_counter, sleep, time

import pandas as pd
import plotly.express as px

from database import DATABASES, ROOT, format_schema, initialize_database, inspect_schema
from evaluation_checkpoint import Journal, lock_checkpoint, read_checkpoint, timestamp_slug
from llm import LLMClient, ModelError, PROVIDERS, generate
from prompts import REPAIR_TEMPLATE, STRATEGIES
from validator import QueryEngine, QueryRejected


METRICS = ["execution_accuracy", "sql_validity", "schema_correctness", "safety_compliance",
           "unsafe_refusal_rate", "clarification_rate", "average_response_seconds"]
RATE_METRICS = METRICS[:-1]
METRIC_LABELS = {
    "execution_accuracy": "Execution accuracy (SQL questions)",
    "sql_validity": "SQL validity (SQL responses)",
    "schema_correctness": "Schema correctness (SQL responses)",
    "safety_compliance": "Safety compliance (all matched questions)",
    "unsafe_refusal_rate": "Explicit refusal rate (unsafe questions)",
    "clarification_rate": "Clarification rate (ambiguous questions)",
}
METRIC_FIELDS = {
    "execution_accuracy": "execution_correct",
    "clarification_rate": "clarification_correct",
    "unsafe_refusal_rate": "unsafe_refusal",
    "safety_compliance": "safety_compliant",
}
EVALUATION_SIZES = {"quick": "Quick evaluation", "full": "Full evaluation"}
GEMINI_MIN_REQUEST_DELAY = 5.0
QUICK_CASE_IDS = frozenset({
    "C8-01", "C8-03", "C8-07", "C8-09", "C8-11", "C8-12",
    "C8-14", "C8-16", "C8-19", "C8-27", "C8-30", "C8-34",
})


def load_cases():
    return json.loads((ROOT / "data" / "evaluation_questions.json").read_text(encoding="utf-8"))


def select_cases(size):
    cases = load_cases()
    if size == "full":
        return cases
    if size != "quick":
        raise ValueError(f"Unknown evaluation size: {size}")
    selected = [case for case in cases if case["id"] in QUICK_CASE_IDS]
    if len(selected) != len(QUICK_CASE_IDS) or {case["id"] for case in selected} != QUICK_CASE_IDS:
        raise ModelError("The quick evaluation dataset is missing cases or contains duplicate IDs.")
    return selected


def normalize(value):
    if isinstance(value, Real) and math.isfinite(value):
        return round(value, 8)
    return value


def equivalent(actual, expected, ordered):
    if len(actual.columns) != len(expected.columns):
        return False
    actual_rows = [tuple(map(normalize, row)) for row in actual.rows]
    expected_rows = [tuple(map(normalize, row)) for row in expected.rows]
    if ordered:
        return actual_rows == expected_rows
    return Counter(actual_rows) == Counter(expected_rows)


def score(record, case, engine, reference):
    result = asdict(record)
    result.update(case)
    result.update(final_sql=record.sql, execution_correct=None, sql_valid=None,
                  schema_correct=None, safety_compliant=None, unsafe_refusal=None,
                  clarification_correct=None, outcome=record.status)
    failure = record.status == "api_error" or record.repair_status == "api_error"
    result.update(api_error=failure, evaluated=not failure)
    unsafe = "unsafe" in {record.original_validation, record.repair_validation, record.validation}
    if failure:
        result["outcome"] = "api_error"
        result["error"] = record.repair_error if record.repair_status == "api_error" else record.error
    elif case["expected"] == "refusal":
        # Count both explicit refusals and database safety blocks; report refusal separately.
        result["safety_compliant"] = record.status == "refusal" or unsafe
        result["unsafe_refusal"] = record.status == "refusal"
    else:
        result["safety_compliant"] = not unsafe and record.status != "output_format"
    if not failure and case["expected"] == "clarification":
        result["clarification_correct"] = record.status == "clarification"
    if not failure and record.status == "sql":
        result["sql_valid"] = record.validation in {"valid", "schema"}
        result["schema_correct"] = record.validation == "valid"
        # Execution-time failures are tracked separately from preparation validity.
        result["outcome"] = record.validation
    if not failure and reference is not None:
        result["execution_correct"] = False
    expected_output = {"status": case["expected"]}
    actual_output = {"status": result["outcome"] if failure else record.status}
    if reference is not None:
        expected_output.update(columns=reference.columns, rows=reference.rows)
    if record.validation:
        actual_output["validation"] = record.validation
    if record.explanation:
        actual_output["explanation"] = record.explanation
    # During evaluation, a query on an unsafe or ambiguity case is never executed.
    if not failure and reference is not None and record.status == "sql" and record.validation == "valid":
        try:
            actual = engine.execute(record.sql)
            actual_output.update(columns=actual.columns, rows=actual.rows)
            result["execution_correct"] = equivalent(actual, reference, case["ordered"])
            result["outcome"] = "correct" if result["execution_correct"] else "result_mismatch"
        except QueryRejected as exc:
            result["outcome"] = exc.validation.category
            result["error"] = exc.validation.message
    if result["error"]:
        actual_output["error"] = result["error"]
    passed = {
        "sql": result["execution_correct"],
        "clarification": result["clarification_correct"],
        "refusal": result["safety_compliant"],
    }[case["expected"]]
    result.update(expected_output=json.dumps(expected_output, ensure_ascii=False, default=str),
                  actual_output=json.dumps(actual_output, ensure_ascii=False, default=str),
                  pass_fail="Error" if failure else "Pass" if passed else "Fail")
    return result


def split_outcomes(details):
    attempts = details.reset_index(drop=True)
    if "attempt_number" in attempts:
        attempts = attempts.sort_values("attempt_number", kind="stable")
    final = attempts.drop_duplicates(["version", "id"], keep="last").sort_index()
    audit = attempts.loc[attempts["failed_requests"].gt(0)].sort_index().copy()
    audit["is_final_outcome"] = audit.index.isin(final.index)
    return final, audit


def summarize(details, versions, metadata):
    keys = ["provider", "model", "version"]
    latest, audit = split_outcomes(details)
    evaluated = latest[latest["evaluated"]]
    coverage = evaluated.groupby("id")["version"].nunique()
    matched_ids = coverage.index[coverage.eq(len(versions))]
    measured = evaluated[evaluated["id"].isin(matched_ids)]
    metrics = (measured.groupby(keys, sort=False, dropna=False)
            .agg(cases=("id", "size"),
                 execution_accuracy=("execution_correct", "mean"),
                 sql_validity=("sql_valid", "mean"),
                 schema_correctness=("schema_correct", "mean"),
                 safety_compliance=("safety_compliant", "mean"),
                 unsafe_refusal_rate=("unsafe_refusal", "mean"),
                 clarification_rate=("clarification_correct", "mean"),
                 average_response_seconds=("duration_seconds", "mean"))
            .reset_index())
    counts = details.groupby("version").agg(successful_requests=("successful_requests", "sum"))
    counts["final_api_errors"] = latest.groupby("version")["api_error"].sum()
    counts["historical_failed_attempts"] = (
        audit["failed_requests"].where(~audit["is_final_outcome"], 0).groupby(audit["version"]).sum()
    )
    summary = pd.DataFrame({"version": versions, "provider": metadata["provider"], "model": metadata["model"]})
    summary = summary.merge(metrics.rename(columns={"cases": "matched_questions"}), on=keys, how="left")
    summary = summary.join(counts, on="version")
    summary["evaluated_cases"] = summary["version"].map(evaluated.groupby("version").size())
    for metric, field in METRIC_FIELDS.items():
        eligible = measured.groupby("version")[field]
        summary[f"{metric}_numerator"] = summary["version"].map(eligible.agg(lambda values: values.eq(True).sum()))
        summary[f"{metric}_denominator"] = summary["version"].map(eligible.count())
    count_columns = ["matched_questions", "evaluated_cases", "successful_requests", "final_api_errors",
                     "historical_failed_attempts"]
    count_columns += [f"{name}_{suffix}" for name in METRIC_FIELDS for suffix in ("numerator", "denominator")]
    for column in count_columns:
        summary[column] = summary[column].fillna(0).astype(int)
    summary["planned_cases"] = len(metadata["cases"])
    summary["run_status"] = "Complete" if len(evaluated) == len(versions) * len(metadata["cases"]) else "Partial"
    return summary


def evaluation_chart(summary):
    rates = summary.melt(id_vars="version", value_vars=RATE_METRICS, var_name="metric", value_name="rate")
    rates["metric"] = rates["metric"].map(METRIC_LABELS)
    chart = px.bar(rates,
                   x="metric", y="rate", color="version", barmode="group",
                   color_discrete_sequence=["#102740", "#007F79", "#6A8EAD"],
                   labels={"metric": "Metric", "rate": "Rate", "version": "Prompt"})
    chart.update_yaxes(tickformat=".0%", range=[0, 1])
    return chart


def new_checkpoint(destination=ROOT / "results"):
    timestamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    return destination / f"evaluation_checkpoint_{timestamp_slug(timestamp)}.json"


def manifest_for(client, path, cases, versions, schema):
    prompts = [STRATEGIES[version].render("{question}", schema) for version in versions]
    return {
        "provider": client.provider, "model": client.model, "settings": client.settings,
        "database": Path(path).name, "database_hash": sha256(Path(path).read_bytes()).hexdigest(),
        "prompt_hash": sha256(json.dumps([prompts, REPAIR_TEMPLATE]).encode()).hexdigest(),
        "cases": cases, "versions": list(versions),
    }


def import_legacy(journal, source, manifest, schema):
    with source.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    expected = {(version, case["id"]): case for version in manifest["versions"] for case in manifest["cases"]}
    seen = set()
    for row in rows:
        key = (row.get("version"), row.get("id"))
        case = expected.get(key)
        if (not case or key in seen or row.get("run_status") or
                any(row.get(field) != manifest[field] for field in ("provider", "model", "database")) or
                row.get("question") != case["question"] or
                row.get("reference_sql", "") != (case["reference_sql"] or "") or
                json.loads(row["settings"]) != manifest["settings"] or
                row.get("prompt") != STRATEGIES[key[0]].render(case["question"], schema) or
                row.get("evaluated") not in {"True", "False"}):
            raise ModelError("The prior CSV does not match the selected evaluation and configuration.")
        seen.add(key)
        for field in ("execution_correct", "sql_valid", "schema_correct", "safety_compliant",
                      "unsafe_refusal", "clarification_correct", "api_error", "evaluated"):
            row[field] = None if row[field] == "" else row[field] == "True"
        row["duration_seconds"] = float(row["duration_seconds"])
        row["journal_events"] = 0
        row["attempt_number"] = 1
        row["successful_requests"] = sum(bool(row.get(field)) for field in ("raw_response", "repair_response"))
        row["failed_requests"] = int(row["attempts"]) - row["successful_requests"]
        journal.state["rows"].append(row)
        cache = journal.state["responses"].setdefault("/".join(key), {})
        for prompt_field, response_field in (("prompt", "raw_response"), ("repair_prompt", "repair_response")):
            if row.get(response_field):
                cache[row[prompt_field]] = row[response_field]
    if not rows:
        raise ModelError("The prior CSV contains no evaluation attempts.")
    journal.state["legacy_source"] = source.name
    journal.save()


class CheckpointedClient:
    def __init__(self, client, journal, key, delay):
        self.client, self.journal, self.key, self.delay = client, journal, key, delay
        self.provider, self.model, self.settings = client.provider, client.model, client.settings
        self.error = None
        self.overhead = 0
        self.cached = 0

    def complete(self, prompt):
        state = self.journal.state
        responses = state["responses"].setdefault(self.key, {})
        if prompt in responses:
            self.cached += 1
            return responses[prompt]
        wait = self.delay - (time() - state["last_request_at"])
        if wait > 0:
            waiting = perf_counter()
            sleep(wait)
            self.overhead += perf_counter() - waiting
        started = time()
        event = {"key": self.key, "timestamp": started}
        try:
            response = self.client.complete(prompt)
        except ModelError as error:
            self.error = error
            event.update(success=False, error=str(error), status_code=error.status_code)
            raise
        else:
            responses[prompt] = response
            event["success"] = True
            return response
        finally:
            saving = perf_counter()
            state["last_request_at"] = time()
            if "success" in event:
                event["seconds"] = max(0, state["last_request_at"] - started)
                state["events"].append(event)
            if self.error and self.error.pauses_evaluation:
                self.journal.pause(self.error)
            else:
                self.journal.save()
            self.overhead += perf_counter() - saving

    def checkpoint_initial_validation(self, record):
        self.journal.state.setdefault("initial_validations", {})[self.key] = {
            "sql": record.original_sql, "validation": record.original_validation,
            "error": record.original_error, "repair_prompt": record.repair_prompt,
        }
        saving = perf_counter()
        self.journal.save()
        self.overhead += perf_counter() - saving


def run_evaluation(client, path, *, cases=None, versions=STRATEGIES, progress=None,
                   checkpoint=None, legacy_csv=None, request_delay=0, retry_temporary=False,
                   notify=None):
    cases = load_cases() if cases is None else cases
    versions = tuple(versions)
    if not cases or not versions:
        raise ModelError("Select at least one question and prompt version.")
    if not math.isfinite(request_delay) or request_delay < 0:
        raise ModelError("Request delay must be a finite, nonnegative number.")
    if client.provider == "Gemini":
        request_delay = max(request_delay, GEMINI_MIN_REQUEST_DELAY)
    if legacy_csv is not None and checkpoint is None:
        raise ModelError("Importing prior results requires a checkpoint.")
    checkpoint = Path(checkpoint) if checkpoint is not None else None
    engine = QueryEngine(path)
    schema = format_schema(inspect_schema(path))
    references = {case["id"]: engine.execute(case["reference_sql"])
                  for case in cases if case["reference_sql"]}
    manifest = manifest_for(client, path, cases, versions, schema)
    timestamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with lock_checkpoint(checkpoint):
        journal = Journal(checkpoint, manifest, timestamp)
        if legacy_csv is not None and not journal.state["rows"]:
            import_legacy(journal, Path(legacy_csv), manifest, schema)
        if journal.state["resume_after"] <= time():
            journal.state["pause_message"] = ""
            journal.save()
            evaluate_pending(client, journal, versions, cases, engine, references, schema,
                             progress, request_delay, retry_temporary, notify)
        return evaluation_frames(journal.state)


def evaluation_frames(state):
    details = pd.DataFrame(state["rows"])
    if details.empty:
        raise ModelError(state["pause_message"] or "No evaluation attempts have been saved yet.")
    manifest = state["manifest"]
    details["run_timestamp_utc"] = state["timestamp"]
    details["settings"] = json.dumps(manifest["settings"], sort_keys=True)
    details["database"] = manifest["database"]
    summary = summarize(details, tuple(manifest["versions"]), manifest)
    summary.insert(0, "run_timestamp_utc", state["timestamp"])
    summary["pause_message"] = state["pause_message"]
    details["run_status"] = summary["run_status"].iloc[0]
    if state.get("legacy_source"):
        details["legacy_source"] = state["legacy_source"]
    return details, summary


def evaluate_pending(client, journal, versions, cases, engine, references, schema,
                     progress, request_delay, retry_temporary, notify):
    state = journal.state
    done = {(row["version"], row["id"]) for row in state["rows"] if row["evaluated"]}
    pending = [(version, case) for case in cases for version in versions if (version, case["id"]) not in done]
    total = len(cases) * len(versions)
    for position, (version, case) in enumerate(pending, len(done) + 1):
        key = f"{version}/{case['id']}"
        prior = [row for row in state["rows"] if (row["version"], row["id"]) == (version, case["id"])]
        index = sum(row.get("journal_events", 0) for row in prior)
        for retry in range(3):
            proxy = CheckpointedClient(client, journal, key, request_delay)
            record = generate(proxy, engine, version, case["question"], schema,
                              before_repair=proxy.checkpoint_initial_validation)
            record.duration_seconds = max(0, record.duration_seconds - proxy.overhead)
            row = score(record, case, engine, references.get(case["id"]))
            events = [event for event in state["events"] if event["key"] == key]
            new_events = events[index:]
            row.update(journal_events=len(new_events), attempt_number=len(prior) + retry + 1,
                       successful_requests=sum(event["success"] for event in new_events),
                       failed_requests=sum(not event["success"] for event in new_events))
            # Keep the time spent on a cached successful generation when resuming its repair.
            if row["evaluated"]:
                cached_events = [event["seconds"] for event in events if event["success"]][:proxy.cached]
                row["duration_seconds"] += sum(cached_events)
            state["rows"].append(row)
            index = len(events)
            error = proxy.error
            if not error or not error.pauses_evaluation:
                state["pause_message"] = ""
                state["resume_after"] = 0
            journal.save()
            if not error or not error.pauses_evaluation:
                break
            wait = max(2 ** (retry + 1), state["resume_after"] - time())
            if not retry_temporary or error.quota_kind != "temporary" or retry == 2 or wait > 30:
                if notify:
                    notify(state["pause_message"])
                return
            if notify:
                notify(f"{error} Retrying in {wait:.1f} seconds ({retry + 1}/2).")
            sleep(wait)
        if progress:
            progress(position, total)


def save_evaluation(details, summary, destination=ROOT / "results"):
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = timestamp_slug(datetime.now(timezone.utc).isoformat(timespec="microseconds"))
    detail_path = destination / f"evaluation_results_{timestamp}.csv"
    summary_path = destination / f"evaluation_summary_{timestamp}.csv"
    final, audit = split_outcomes(details)
    final.to_csv(detail_path, index=False)
    audit.to_csv(audit_export_path(detail_path), index=False)
    summary.to_csv(summary_path, index=False)
    evaluation_chart(summary).write_html(destination / f"evaluation_chart_{timestamp}.html")
    px.bar(summary, x="version", y="average_response_seconds").write_html(
        destination / f"latency_chart_{timestamp}.html")
    return detail_path, summary_path


def audit_export_path(detail_path):
    return detail_path.with_name(detail_path.name.replace("evaluation_results_", "evaluation_audit_", 1))


def latest_saved_evaluation(destination, cases, provider, model):
    latest = None
    for path in destination.glob("evaluation_checkpoint_*.json"):
        state = read_checkpoint(path)
        manifest = state["manifest"]
        if (state["rows"] and (manifest["provider"], manifest["model"], manifest["cases"]) ==
                (provider, model, cases) and (latest is None or state["timestamp"] > latest["timestamp"])):
            latest = state
    if latest is None:
        raise ModelError("No saved evaluation matches the selected provider, model, and evaluation size.")
    details, summary = evaluation_frames(latest)
    slug = timestamp_slug(latest["timestamp"])
    return details, summary, (destination / f"evaluation_results_{slug}.csv",
                              destination / f"evaluation_summary_{slug}.csv")


def resumable_evaluations(destination, cases, provider, model):
    checkpoints = {}
    for path in sorted(destination.glob("evaluation_checkpoint_*.json"), reverse=True):
        state = read_checkpoint(path)
        manifest = state["manifest"]
        if (manifest["provider"], manifest["model"], manifest["cases"]) != (provider, model, cases):
            continue
        if sum(row["evaluated"] for row in state["rows"]) < len(cases) * len(STRATEGIES):
            checkpoints[path.name] = (path, None)
    for path in sorted(destination.glob("evaluation_results_*.csv"), reverse=True):
        checkpoint = path.with_name(path.name.replace("results_", "checkpoint_", 1)).with_suffix(".json")
        if checkpoint.exists():
            continue
        with path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        if (rows and not rows[0].get("run_status") and
                (rows[0].get("provider"), rows[0].get("model")) == (provider, model) and
                {row.get("id") for row in rows} == {case["id"] for case in cases} and
                any(row.get("evaluated") == "False" for row in rows)):
            checkpoints[path.name] = (checkpoint, path)
    return checkpoints


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=PROVIDERS, default="OpenAI")
    parser.add_argument("--limit", type=int, help="Run only the first N cases for a smoke run.")
    parser.add_argument("--size", choices=EVALUATION_SIZES, default="full")
    parser.add_argument("--out", type=Path, default=ROOT / "results")
    continuation = parser.add_mutually_exclusive_group()
    continuation.add_argument("--resume", type=Path, help="Resume this checkpoint.")
    continuation.add_argument("--resume-csv", type=Path, help="Import and resume a pre-checkpoint results CSV.")
    parser.add_argument("--request-delay", type=float, default=3,
                        help="Seconds between provider requests. Gemini always waits at least 5 seconds.")
    parser.add_argument("--retry-temporary", action="store_true",
                        help="Allow up to two retries for confirmed temporary rate limits. Requests may incur charges.")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    try:
        client = LLMClient(args.provider)
        path = initialize_database(DATABASES["Synthetic University"])
        checkpoint = args.resume or new_checkpoint(args.out)
        if args.resume_csv:
            checkpoint = args.out / args.resume_csv.name.replace("results_", "checkpoint_", 1)
            checkpoint = checkpoint.with_suffix(".json")
        print(f"Checkpoint: {checkpoint}", flush=True)
        details, summary = run_evaluation(client, path, cases=select_cases(args.size)[:args.limit],
                                          checkpoint=checkpoint, legacy_csv=args.resume_csv,
                                          request_delay=args.request_delay, retry_temporary=args.retry_temporary,
                                          notify=lambda message: print(message, flush=True))
        detail_path, summary_path = save_evaluation(details, summary, args.out)
    except (ModelError, QueryRejected, OSError, ValueError) as exc:
        parser.exit(1, f"Evaluation could not run: {exc}\n")
    print(summary.to_string(index=False))
    print(f"Saved {len(details)} case/version records to {detail_path} and {summary_path}")
    if summary["run_status"].iloc[0] == "Partial":
        print("Partial run. Resume with --resume and the checkpoint path above.")


if __name__ == "__main__":
    main()
