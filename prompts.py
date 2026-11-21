"""Versioned prompts and training examples, independent of the benchmark."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Strategy:
    title: str
    technique: str
    template: str

    def render(self, question, schema):
        return self.template.replace("{schema}", schema).replace("{examples}", EXAMPLES).replace("{question}", question)


STRATEGIES = {
    "V1": Strategy("Baseline", "Zero-shot instruction without schema or examples.", """Convert the English question to a SQLite SELECT query. Return only SQL.
Question: {question}"""),
    "V2": Strategy("Schema-aware", "Grounds identifiers and joins in the selected database schema.", """Translate the question to one SQLite SELECT using only the tables, columns and relationships below.
Never invent schema identifiers or generate writes. Return only SQL. If no safe query is possible, start your reply with REFUSAL: followed by a short reason.
Schema:
{schema}
Question: {question}"""),
    "V3": Strategy("Few-shot + structured", "Adds worked examples, explicit ambiguity handling and one validation-guided repair attempt.", """You translate university analytics questions into read-only SQLite queries.
Use only schema identifiers below and respect foreign keys. Avoid unsupported assumptions. Ask a focused clarification when a material detail is missing. Refuse operations that mutate data or access unauthorized tables.
Return one JSON object with keys status, sql, explanation. Status must be sql, clarification, or refusal. Use an empty sql string for clarification or refusal. For a query, explain its logic briefly.
Schema:
{schema}
Examples:
{examples}
Question: {question}"""),
}

EXAMPLES = """Question: List the titles of two-credit courses.
Response: {"status":"sql","sql":"SELECT title FROM courses WHERE credits = 2","explanation":"Filters courses by credit value."}
Question: Show the name of each student and their department if they entered in 2022.
Response: {"status":"sql","sql":"SELECT s.name, d.name FROM students s JOIN departments d ON s.department_id = d.department_id WHERE s.admission_year = 2022","explanation":"Joins the department to each qualifying student."}
Question: Which instructors performed better?
Response: {"status":"clarification","sql":"","explanation":"What measure should define instructor performance?"}
Question: Rename the departments in the database.
Response: {"status":"refusal","sql":"","explanation":"Renaming departments would modify the database."}"""

REPAIR_TEMPLATE = """Correct the rejected SQLite query for the question below. Use the supplied schema only.
Return the same JSON format as before with status, sql and explanation. If ambiguous, ask for clarification.
Schema:
{schema}
Question: {question}
Rejected SQL: {sql}
Validation error: {error}"""
