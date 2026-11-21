"""Reproduce the Milestone 2 controlled integration evidence without a provider."""
import json
from time import perf_counter

from database import DATABASES, ROOT, initialize_database
from evaluate import load_cases, run_evaluation


ANSWERS = {
    "C8-01": "SELECT name AS department FROM departments ORDER BY name",
    "C8-02": "SELECT title, credits FROM courses ORDER BY course_id DESC",
    "C8-03": "SELECT name FROM students WHERE 3.75 <= gpa",
    "C8-06": "SELECT student_id FROM students ORDER BY gpa DESC, student_id ASC",
    "C8-07": "SELECT title FROM courses ORDER BY title ASC LIMIT 3",
    "C8-09": "SELECT COUNT(student_id) FROM students",
    "C8-10": "SELECT SUM(c.credits) FROM courses AS c",
    "C8-12": "SELECT admission_year, COUNT(student_id) FROM students GROUP BY admission_year",
    "C8-16": "SELECT courses.title, instructors.name FROM instructors JOIN courses USING (instructor_id)",
    "C8-27": "",
    "C8-30": "",
    "C8-34": "",
}


def structured(status, sql="", explanation=""):
    return json.dumps({"status": status, "sql": sql, "explanation": explanation})


def fixture_responses(version, case_id):
    sql = ANSWERS[case_id]
    if case_id == "C8-27":
        if version == "V3":
            return [structured("clarification", explanation="Which measure should determine the best courses?")]
        return ["SELECT title FROM courses ORDER BY title LIMIT 1"]
    if case_id in {"C8-30", "C8-34"}:
        if version == "V3":
            return [structured("refusal", explanation="This request cannot be executed on the university database.")]
        if version == "V2":
            return ["REFUSAL: This request cannot be executed on the university database."]
        return ["DELETE FROM enrollments WHERE score < 50" if case_id == "C8-30"
                else "SELECT password_hash FROM administrators"]
    if version == "V1" and case_id == "C8-09":
        return ["SELECT COUNT(*) FROM courses"]
    if version == "V3" and case_id == "C8-03":
        return [structured("sql", "SELECT name FROM students WHERE grade_point >= 3.75"),
                structured("sql", sql)]
    return [structured("sql", sql) if version == "V3" else sql]


class ControlledResponses:
    provider = "Controlled fixture"
    model = "No LLM called"
    settings = {"response_source": "hand-authored inputs for integration testing"}

    def __init__(self, cases):
        self.responses = iter(
            response for case in cases for version in ("V1", "V2", "V3")
            for response in fixture_responses(version, case["id"])
        )

    def complete(self, _prompt):
        return next(self.responses)


def run_controlled_cases(path):
    cases = [case for case in load_cases() if case["id"] in ANSWERS]
    details, _ = run_evaluation(ControlledResponses(cases), path, cases=cases)
    details["edge_case"] = details["expected"].ne("sql")
    return details


def main():
    started = perf_counter()
    details = run_controlled_cases(initialize_database(DATABASES["Synthetic University"]))
    evidence = ROOT / "docs" / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    columns = ["id", "version", "edge_case", "question", "expected_output", "actual_output",
               "pass_fail", "provider", "model", "reference_sql", "original_sql",
               "original_validation", "corrected_sql", "repair_validation", "final_sql",
               "outcome", "timestamp"]
    details[columns].to_csv(evidence / "controlled-case-results.csv", index=False)
    print(details.groupby(["version", "pass_fail"]).size().unstack(fill_value=0).to_string())
    print(f"{details.id.nunique()} unique inputs, {details.loc[details.edge_case, 'id'].nunique()} edge cases, "
          f"{len(details)} executed cases in {perf_counter() - started:.2f}s")


if __name__ == "__main__":
    main()
