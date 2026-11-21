from database import ROOT
from prompts import EXAMPLES, REPAIR_TEMPLATE, STRATEGIES
from tests.milestone_cases import run_controlled_cases


def test_prompt_artifacts_match_runtime():
    folder = ROOT / "prompts"
    for version, strategy in STRATEGIES.items():
        expected = strategy.template.replace("{examples}", EXAMPLES)
        assert (folder / version.lower() / "generate.txt").read_text(encoding="utf-8").rstrip("\n") == expected
    assert (folder / "final/generate.txt").read_bytes() == (folder / "v3/generate.txt").read_bytes()
    for subfolder in ("v3", "final"):
        assert (folder / subfolder / "repair.txt").read_text(encoding="utf-8").rstrip("\n") == REPAIR_TEMPLATE


def test_controlled_evidence_measures_passes_failures_and_repair(tmp_path):
    from database import initialize_database

    path = initialize_database(tmp_path / "evidence.sqlite3")
    details = run_controlled_cases(path)
    assert details["question"].nunique() == 12
    assert details.loc[details.edge_case, "question"].nunique() == 3
    assert details["expected_output"].str.len().gt(0).all()
    assert details["actual_output"].str.len().gt(0).all()
    assert details.groupby("version")["pass_fail"].agg(lambda x: x.eq("Pass").sum()).to_dict() == {
        "V1": 9, "V2": 11, "V3": 12
    }
    repaired = details.loc[details.corrected_sql.ne("")].iloc[0]
    assert repaired.original_validation == "schema"
    assert repaired.repair_validation == "valid"
    assert repaired.pass_fail == "Pass"
