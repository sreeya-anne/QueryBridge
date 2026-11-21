import json
from types import SimpleNamespace

import pytest

import evaluate
import evaluation_checkpoint
from database import initialize_database
from llm import ModelError, rate_limit_error
from tests.test_querybridge import TestClient, structured


@pytest.fixture
def paths(tmp_path):
    return initialize_database(tmp_path / "university.sqlite3"), tmp_path / "checkpoint.json"


def limit(kind, delay=None):
    return ModelError("Gemini request failed (HTTP 429).", status_code=429,
                      quota_kind=kind, retry_after=delay)


def test_daily_quota_pauses_and_resume_only_requests_missing_cases(paths):
    database, checkpoint = paths
    cases = evaluate.load_cases()[:2]
    sql = cases[0]["reference_sql"]
    details, summary = evaluate.run_evaluation(
        TestClient(sql, sql, structured(sql), limit("quota")), database,
        cases=cases, checkpoint=checkpoint, retry_temporary=True,
    )
    assert len(details) == 4
    assert summary["run_status"].tolist() == ["Partial"] * 3
    assert summary["matched_questions"].tolist() == [1] * 3
    assert summary["final_api_errors"].tolist() == [1, 0, 0]
    assert summary["historical_failed_attempts"].tolist() == [0, 0, 0]
    assert summary["execution_accuracy"].tolist() == [1, 1, 1]

    second_sql = cases[1]["reference_sql"]
    details, summary = evaluate.run_evaluation(
        TestClient(second_sql, second_sql, structured(second_sql)), database,
        cases=cases, checkpoint=checkpoint,
    )
    assert len(details) == 7
    assert summary["run_status"].tolist() == ["Complete"] * 3
    assert summary["matched_questions"].tolist() == [2] * 3
    assert summary["successful_requests"].tolist() == [2] * 3
    assert summary["final_api_errors"].tolist() == [0, 0, 0]
    assert summary["historical_failed_attempts"].tolist() == [1, 0, 0]
    final, audit = evaluate.split_outcomes(details)
    assert len(final) == 6 and not final.duplicated(["id", "version"]).any()
    assert len(audit) == 1 and not audit["is_final_outcome"].any()


def test_repair_resume_respects_retry_delay_and_reuses_original_response(paths, monkeypatch):
    database, checkpoint = paths
    case = evaluate.load_cases()[0]
    clock = [100.0]
    monkeypatch.setattr(evaluate, "time", lambda: clock[0])
    monkeypatch.setattr(evaluation_checkpoint, "time", lambda: clock[0])
    bad = structured("SELECT unknown FROM departments")
    initial = TestClient(bad, limit("temporary", 45))
    details, summary = evaluate.run_evaluation(initial, database, cases=[case], versions=("V3",),
                                              checkpoint=checkpoint, retry_temporary=True)
    assert details.loc[0, "raw_response"] == bad
    assert details.loc[0, "successful_requests"] == 1
    assert summary.loc[0, "matched_questions"] == 0

    details, summary = evaluate.run_evaluation(TestClient(), database, cases=[case], versions=("V3",),
                                              checkpoint=checkpoint)
    assert "Provider retry delay" in summary.loc[0, "pause_message"]
    assert len(details) == 1
    clock[0] += 45
    details, summary = evaluate.run_evaluation(TestClient(structured(case["reference_sql"])), database,
                                              cases=[case], versions=("V3",), checkpoint=checkpoint)
    assert details.iloc[-1]["original_validation"] == "schema"
    assert details.iloc[-1]["pass_fail"] == "Pass"
    assert summary.loc[0, "successful_requests"] == 2
    assert summary.loc[0, "run_status"] == "Complete"


def test_temporary_retries_are_bounded_and_paced(paths, monkeypatch):
    database, checkpoint = paths
    clock = [100.0]
    waits = []

    def advance(seconds):
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(evaluate, "time", lambda: clock[0])
    monkeypatch.setattr(evaluation_checkpoint, "time", lambda: clock[0])
    monkeypatch.setattr(evaluate, "sleep", advance)
    details, summary = evaluate.run_evaluation(
        TestClient(limit("temporary", 3), limit("temporary"), limit("temporary")), database,
        cases=evaluate.load_cases()[:1], versions=("V1",), checkpoint=checkpoint,
        request_delay=5, retry_temporary=True,
    )
    assert waits == [3, 2, 4, 1]
    assert len(details) == 3
    assert summary.loc[0, "final_api_errors"] == 1
    assert summary.loc[0, "historical_failed_attempts"] == 2
    assert summary.loc[0, "run_status"] == "Partial"


def test_gemini_enforces_five_seconds_through_v3_repair(paths, monkeypatch):
    database, checkpoint = paths
    case = evaluate.load_cases()[0]
    sql = case["reference_sql"]
    clock = [100.0]
    calls = []

    class GeminiFixture(TestClient):
        provider = "Gemini"

        def complete(self, prompt):
            calls.append(clock[0])
            clock[0] += 2
            return super().complete(prompt)

    monkeypatch.setattr(evaluate, "time", lambda: clock[0])
    monkeypatch.setattr(evaluate, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    client = GeminiFixture(sql, sql, structured("SELECT unknown FROM departments"), structured(sql))
    _, summary = evaluate.run_evaluation(client, database, cases=[case], checkpoint=checkpoint, request_delay=1)
    assert calls == [100, 107, 114, 121]
    assert summary["run_status"].eq("Complete").all()


def test_non_429_retry_after_pauses_and_blocks_resume(paths, monkeypatch):
    database, checkpoint = paths
    case = evaluate.load_cases()[0]
    clock = [100.0]
    monkeypatch.setattr(evaluate, "time", lambda: clock[0])
    monkeypatch.setattr(evaluation_checkpoint, "time", lambda: clock[0])
    error = ModelError("Gemini unavailable", status_code=503, retry_after=20)
    kwargs = {"cases": [case], "versions": ("V1",), "checkpoint": checkpoint, "retry_temporary": True}
    _, summary = evaluate.run_evaluation(TestClient(error), database, **kwargs)
    assert summary.loc[0, "run_status"] == "Partial"
    clock[0] += 19
    details, _ = evaluate.run_evaluation(TestClient(), database, **kwargs)
    assert len(details) == 1
    clock[0] += 1
    _, summary = evaluate.run_evaluation(TestClient(case["reference_sql"]), database, **kwargs)
    assert summary.loc[0, "run_status"] == "Complete"


def test_response_is_saved_before_scoring(paths, monkeypatch):
    database, checkpoint = paths
    case = evaluate.load_cases()[0]
    score = evaluate.score

    def crash(*_):
        state = json.loads(checkpoint.read_text(encoding="utf-8"))
        assert case["reference_sql"] in state["responses"]["V1/" + case["id"]].values()
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr(evaluate, "score", crash)
    with pytest.raises(RuntimeError, match="simulated"):
        evaluate.run_evaluation(TestClient(case["reference_sql"]), database, cases=[case],
                                versions=("V1",), checkpoint=checkpoint)
    monkeypatch.setattr(evaluate, "score", score)
    details, summary = evaluate.run_evaluation(TestClient(), database, cases=[case],
                                              versions=("V1",), checkpoint=checkpoint)
    assert details.loc[0, "pass_fail"] == "Pass"
    assert summary.loc[0, "successful_requests"] == 1


def test_legacy_csv_preserves_success_and_error_history(paths, tmp_path):
    database, checkpoint = paths
    case = evaluate.load_cases()[0]
    sql = case["reference_sql"]
    prior, _ = evaluate.run_evaluation(
        TestClient(sql, ModelError("old rate limit"), structured(sql)), database, cases=[case]
    )
    source = tmp_path / "old.csv"
    prior.drop(columns="run_status").to_csv(source, index=False)
    details, summary = evaluate.run_evaluation(
        TestClient(sql), database, cases=[case], checkpoint=checkpoint, legacy_csv=source,
    )
    assert len(details) == 4
    assert details["legacy_source"].eq(source.name).all()
    assert summary["run_status"].eq("Complete").all()
    assert summary["successful_requests"].tolist() == [1, 1, 1]
    assert summary["final_api_errors"].tolist() == [0, 0, 0]
    assert summary["historical_failed_attempts"].tolist() == [0, 1, 0]


@pytest.mark.parametrize(("quota_id", "expected"), [
    ("GenerateRequestsPerDayPerProjectPerModel", "quota"),
    ("GenerateRequestsPerMinutePerProjectPerModel", "temporary"),
    ("OtherLimit", "unknown"),
])
def test_gemini_classifies_quota_and_keeps_longest_delay(quota_id, expected):
    upstream = SimpleNamespace(
        response=SimpleNamespace(headers={"retry-after": "12"}),
        details={"error": {"details": [
            {"violations": [{"quotaId": quota_id}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "15.5s"},
        ]}},
    )
    error = rate_limit_error("Gemini", upstream)
    assert error.quota_kind == expected
    assert error.retry_after == 15.5
    assert quota_id not in str(error)
