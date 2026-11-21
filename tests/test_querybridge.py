import json
import sqlite3
import sys

import pytest
from streamlit.testing.v1 import AppTest

from database import ROOT, format_schema, initialize_database, inspect_schema, open_readonly
from evaluate import equivalent, load_cases, run_evaluation, save_evaluation, select_cases
from llm import ModelError, generate
from prompts import EXAMPLES, STRATEGIES
from validator import QueryEngine, QueryRejected, QueryResults


@pytest.fixture(scope="module")
def database(tmp_path_factory):
    return initialize_database(tmp_path_factory.mktemp("university") / "fixture.sqlite3")


@pytest.fixture(scope="module")
def engine(database):
    return QueryEngine(database)


def test_database_is_populated_and_idempotent(database, engine):
    expected = {"students": 120, "departments": 5, "courses": 20, "instructors": 10, "enrollments": 360}
    assert initialize_database(database) == database
    with open_readonly(database) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    for table, count in expected.items():
        assert engine.execute(f"SELECT COUNT(*) FROM {table}").rows == ((count,),)


def test_schema_and_prompt_versions(database):
    schema = inspect_schema(database)
    assert {"column": "course_id", "table": "courses", "target": "course_id"} in schema["enrollments"]["foreign_keys"]
    rendered = format_schema(schema)
    question = "Return {schema} scores"
    assert rendered not in STRATEGIES["V1"].render(question, rendered)
    assert rendered in STRATEGIES["V2"].render(question, rendered)
    assert EXAMPLES not in STRATEGIES["V2"].render(question, rendered)
    assert EXAMPLES in STRATEGIES["V3"].render(question, rendered)
    assert all(question in item.render(question, rendered) for item in STRATEGIES.values())


@pytest.mark.parametrize(("sql", "category"), [
    ("SELECT name FROM students", "valid"),
    ("SELECT 1; -- comment", "valid"),
    ("WITH names AS (SELECT name FROM students) SELECT * FROM names", "valid"),
    ("SELECT name FROM students UNION SELECT name FROM instructors", "valid"),
    ("SELECT not_a_column FROM students", "schema"),
    ('SELECT "not_a_column" FROM students', "schema"),
    ("SELECT name FROM missing_table", "schema"),
    ("SELECT FROM", "syntax"),
    ("SELECT 1; SELECT 2", "unsafe"),
    ("INSERT INTO students VALUES (1)", "unsafe"),
    ("UPDATE students SET gpa=0", "unsafe"),
    ("DELETE FROM students", "unsafe"),
    ("DROP TABLE students", "unsafe"),
    ("ALTER TABLE students RENAME TO alumni", "unsafe"),
    ("CREATE TABLE forbidden(id INT)", "unsafe"),
    ("ATTACH DATABASE 'secondary.db' AS secondary", "unsafe"),
    ("PRAGMA table_info(students)", "unsafe"),
    ("SELECT name FROM sqlite_master", "unsafe"),
    ("SELECT load_extension('disabled')", "unsafe"),
])
def test_validation(engine, sql, category):
    assert engine.validate(sql).category == category


def test_read_only_and_row_limit(database):
    with open_readonly(database) as connection, pytest.raises(sqlite3.OperationalError, match="readonly"):
        connection.execute("DELETE FROM students")
    with pytest.raises(QueryRejected, match="exceeds"):
        QueryEngine(database, max_rows=1).execute("SELECT * FROM students")


def test_benchmark_references(engine):
    cases = load_cases()
    assert len(cases) == 34
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        assert case["question"] not in EXAMPLES
        if case["expected"] == "sql":
            assert engine.execute(case["reference_sql"]).columns


def test_equivalence():
    first = QueryResults(("a",), ((1,), (2,), (2,)))
    second = QueryResults(("alias",), ((2,), (1,), (2.0000000001,)))
    assert equivalent(first, second, ordered=False)
    assert not equivalent(first, second, ordered=True)
    assert not equivalent(first, QueryResults(("a",), ((1,), (2,))), ordered=False)


class TestClient:
    __test__ = False
    provider = "Fixture"
    model = "fixture-model"
    settings = {"fixture": True}

    def __init__(self, *responses):
        self.responses = iter(responses)

    def complete(self, prompt):
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


def structured(sql="", status="sql"):
    return json.dumps({"sql": sql, "status": status, "explanation": "Fixture explanation"})


def test_repair_retains_original(database, engine):
    schema = format_schema(inspect_schema(database))
    original = "SELECT nonexistent FROM students"
    corrected = "SELECT name FROM students"
    result = generate(TestClient(structured(original), structured(corrected)), engine, "V3", "Names?", schema)
    assert result.original_sql == original
    assert result.corrected_sql == result.sql == corrected
    assert result.original_validation == "schema"
    assert result.validation == "valid"
    assert result.attempts == 2
    unsafe = generate(TestClient(structured("DROP TABLE students")), engine, "V3", "Drop?", schema)
    assert unsafe.validation == "unsafe"
    assert unsafe.attempts == 1


def test_evaluation_calculations_and_exports(database, tmp_path):
    cases = [load_cases()[0], load_cases()[-1]]
    result, summary = run_evaluation(TestClient(
        "SELECT name FROM departments", "SELECT name FROM imaginary_table",
        structured("SELECT name FROM departments"), "DROP TABLE students",
        "REFUSAL: unauthorized", structured(status="refusal"),
    ), database, cases=cases)
    rates = summary.set_index("version")
    assert len(result) == 6
    assert rates.loc["V1", "execution_accuracy"] == 1
    assert rates.loc["V1", "unsafe_refusal_rate"] == 0
    assert rates.loc["V2", "execution_accuracy"] == 0
    assert rates.loc["V2", "sql_validity"] == 1
    assert rates.loc["V2", "schema_correctness"] == 0
    assert rates.loc["V3", "safety_compliance"] == 1
    detail_path, summary_path = save_evaluation(result, summary, tmp_path)
    timestamp = detail_path.stem.removeprefix("evaluation_results_")
    assert summary_path.name == f"evaluation_summary_{timestamp}.csv"
    assert {file.name for file in tmp_path.iterdir()} == {
        detail_path.name, summary_path.name, f"evaluation_audit_{timestamp}.csv",
        f"evaluation_chart_{timestamp}.html", f"latency_chart_{timestamp}.html"
    }


def test_provider_failure_has_no_scores(database, tmp_path):
    details, summary = run_evaluation(TestClient(ModelError("offline")), database,
                                      cases=load_cases()[:1], versions=("V1",))
    assert details.loc[0, "pass_fail"] == "Error"
    assert details.loc[0, "error"] == "offline"
    assert summary.loc[0, "final_api_errors"] == 1
    assert summary.loc[0, "evaluated_cases"] == 0
    assert summary["execution_accuracy"].isna().all()
    assert summary["safety_compliance"].isna().all()
    paths = save_evaluation(details, summary, tmp_path)
    assert all(path.is_file() for path in paths)


def test_repair_api_error_excluded_from_accuracy(database):
    cases = load_cases()[:2]
    details, summary = run_evaluation(TestClient(
        structured("SELECT missing_column FROM departments"), ModelError("quota exhausted"),
        structured(cases[1]["reference_sql"]),
    ), database, cases=cases, versions=("V3",))
    failed = details.iloc[0]
    assert failed["original_validation"] == "schema"
    assert failed["original_sql"] == "SELECT missing_column FROM departments"
    assert failed["outcome"] == "api_error"
    assert failed["error"] == "quota exhausted"
    assert summary.loc[0, "final_api_errors"] == 1
    assert summary.loc[0, "evaluated_cases"] == 1
    assert summary.loc[0, "execution_accuracy"] == 1


def test_quick_and_full_case_selection_is_fixed_and_matched(database):
    quick, full = select_cases("quick"), select_cases("full")
    assert len({case["id"] for case in quick}) == 12
    assert full == load_cases() and len(full) == 34
    assert sum(case["expected"] != "sql" for case in quick) >= 3
    assert quick == select_cases("quick") and all(case in full for case in quick)
    calls = []
    details, summary = run_evaluation(TestClient(*[ModelError("offline")] * 36),
                                      database, cases=quick,
                                      progress=lambda done, total: calls.append((done, total)))
    assert calls == [(i, 36) for i in range(1, 37)]
    assert summary["final_api_errors"].tolist() == [12, 12, 12]
    for _, group in details.groupby("version"):
        assert group["id"].tolist() == [case["id"] for case in quick]
    assert details["model"].nunique() == details["settings"].nunique() == 1


def test_cli_with_configured_openai_and_mock_transport(monkeypatch, tmp_path):
    import httpx
    import openai

    import evaluate

    prompts = []
    sql = "SELECT name FROM departments"

    def reply(request):
        assert request.url.path == "/v1/responses"
        body = json.loads(request.content)
        prompts.append(body["input"])
        assert body["model"] == "fixture-model"
        response = structured(sql) if "Return one JSON object" in body["input"] else sql
        return httpx.Response(200, json={"output": [{
            "type": "message", "role": "assistant", "status": "completed", "id": "msg_fixture",
            "content": [{"type": "output_text", "text": response, "annotations": []}],
        }]})

    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key")
    monkeypatch.setenv("OPENAI_MODEL", "fixture-model")
    create_client = openai.OpenAI
    with httpx.Client(transport=httpx.MockTransport(reply)) as transport:
        monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: create_client(http_client=transport, **kwargs))
        monkeypatch.setattr(sys, "argv", ["evaluate.py", "--limit", "1", "--out", str(tmp_path), "--request-delay", "0"])
        evaluate.main()
    assert len(prompts) == 3
    assert len(list(tmp_path.glob("evaluation_results_*.csv"))) == 1


def test_streamlit_pages_and_version_selection(monkeypatch):
    import llm
    monkeypatch.setattr(llm, "load_dotenv", lambda *_: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = AppTest.from_file(ROOT / "app.py", default_timeout=10).run()
    assert not app.exception
    assert app.button[0].disabled
    app.selectbox[0].select("V3").run()
    assert not app.exception
    assert app.selectbox[0].value == "V3"
    for page in ["Database Explorer", "Prompt Engineering Lab", "Evaluation Dashboard", "About Project"]:
        app.sidebar.radio[0].set_value(page).run()
        assert not app.exception, page


def test_playground_and_lab_render_model_output(monkeypatch):
    import llm
    monkeypatch.setattr(llm, "provider_config", lambda *_: ("fixture-key", "fixture-model"))
    sql = "SELECT COUNT(*) AS total, COUNT(*) AS total FROM students"
    client = TestClient(structured(sql), sql, sql, structured(sql))
    monkeypatch.setattr(llm, "LLMClient", lambda *_: client)
    app = AppTest.from_file(ROOT / "app.py", default_timeout=10).run()
    app.selectbox[0].select("V3").run()
    app.text_input[0].set_value("How many students are there?").run()
    app.button[0].click().run()
    assert not app.exception
    assert app.dataframe[0].value.iloc[0, 0] == 120
    assert list(app.dataframe[0].value.columns) == ["total", "total (2)"]
    app.sidebar.radio[0].set_value("Prompt Engineering Lab").run()
    app.text_area[0].set_value("How many students are there?").run()
    app.button[0].click().run()
    assert not app.exception
    assert len(app.success) == 3


def test_generate_button_requires_gemini_configuration_and_nonblank_question(monkeypatch):
    import llm

    monkeypatch.setattr(llm, "provider_config", lambda provider: (
        "fixture-key" if provider == "Gemini" else "", "fixture-model"
    ))
    app = AppTest.from_file(ROOT / "app.py", default_timeout=10).run()
    app.text_input[0].set_value("How many students?").run()
    assert app.button[0].disabled
    app.sidebar.selectbox[1].select("Gemini").run()
    assert not app.button[0].disabled
    for question in ("", "   "):
        app.text_input[0].set_value(question).run()
        assert app.button[0].disabled
    app.text_input[0].set_value("How many students?").run()
    assert not app.button[0].disabled
    assert not app.exception


def test_gemini_error_does_not_expose_provider_payload(monkeypatch):
    from types import SimpleNamespace

    import llm

    class UnavailableModel(Exception):
        code = 404

    def fail(**_kwargs):
        raise UnavailableModel("secret-fixture credential in upstream body")

    monkeypatch.setattr(llm, "provider_config", lambda _: ("fixture-key", "fixture-model"))
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda **_: SimpleNamespace(
        models=SimpleNamespace(generate_content=fail)
    ))
    with pytest.raises(ModelError, match=r"HTTP 404.*GEMINI_MODEL") as raised:
        llm.LLMClient("Gemini").complete("Test question")
    assert "secret-fixture" not in str(raised.value)


def test_evaluation_dashboard_renders_live_flow_with_fixture(monkeypatch, tmp_path):
    import evaluate
    import llm

    cases = evaluate.load_cases()[:1]
    save = evaluate.save_evaluation
    sql = cases[0]["reference_sql"]
    client = TestClient(sql, sql, structured(sql))
    monkeypatch.setattr(llm, "provider_config", lambda *_: ("fixture-key", "fixture-model"))
    monkeypatch.setattr(llm, "LLMClient", lambda *_: client)
    monkeypatch.setattr(evaluate, "select_cases", lambda _: cases)
    monkeypatch.setattr(evaluate, "new_checkpoint", lambda: tmp_path / "fixture.json")
    monkeypatch.setattr(evaluate, "save_evaluation", lambda details, summary: save(details, summary, tmp_path))
    app = AppTest.from_file(ROOT / "app.py", default_timeout=10).run()
    app.sidebar.radio[0].set_value("Evaluation Dashboard").run()
    app.number_input[0].set_value(0).run()
    app.button[0].click().run()
    assert not app.exception
    assert len(app.get("plotly_chart")) == 2
    assert len(app.get("download_button")) == 3
    assert len(list(tmp_path.glob("evaluation_summary_*.csv"))) == 1


def test_dashboard_shows_quick_and_full_request_counts(monkeypatch):
    import llm

    monkeypatch.setattr(llm, "provider_config", lambda _: ("", "fixture-model"))
    app = AppTest.from_file(ROOT / "app.py", default_timeout=10).run()
    app.sidebar.radio[0].set_value("Evaluation Dashboard").run()
    assert app.radio[0].options == ["Quick evaluation", "Full evaluation"]
    assert any("12 held-out questions · 36 initial" in item.value for item in app.markdown)
    app.radio[0].set_value("full").run()
    assert any("34 held-out questions · 102 initial" in item.value for item in app.markdown)
    assert not app.exception
