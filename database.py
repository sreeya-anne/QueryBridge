"""Deterministic synthetic university database and schema inspection."""
from contextlib import closing, contextmanager
from pathlib import Path
import random
import sqlite3

ROOT = Path(__file__).resolve().parent
DATABASES = {"Synthetic University": ROOT / "data" / "university.sqlite3"}
DDL = """
CREATE TABLE departments (
    department_id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE instructors (
    instructor_id INTEGER PRIMARY KEY, name TEXT NOT NULL,
    department_id INTEGER NOT NULL REFERENCES departments(department_id));
CREATE TABLE students (
    student_id INTEGER PRIMARY KEY, name TEXT NOT NULL,
    department_id INTEGER NOT NULL REFERENCES departments(department_id),
    admission_year INTEGER NOT NULL, gpa REAL NOT NULL CHECK(gpa BETWEEN 0 AND 4));
CREATE TABLE courses (
    course_id INTEGER PRIMARY KEY, title TEXT NOT NULL UNIQUE,
    department_id INTEGER NOT NULL REFERENCES departments(department_id),
    instructor_id INTEGER NOT NULL REFERENCES instructors(instructor_id),
    credits INTEGER NOT NULL CHECK(credits BETWEEN 2 AND 4));
CREATE TABLE enrollments (
    enrollment_id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(student_id),
    course_id INTEGER NOT NULL REFERENCES courses(course_id),
    term TEXT NOT NULL, score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
    UNIQUE(student_id, course_id, term));
"""


def initialize_database(path=DATABASES["Synthetic University"]):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(82)
    departments = ["Computer Science", "Mathematics", "Physics", "Economics", "Literature"]
    subjects = [
        ["Programming", "Algorithms", "Databases", "Networks"],
        ["Calculus", "Linear Algebra", "Statistics", "Discrete Mathematics"],
        ["Mechanics", "Optics", "Thermodynamics", "Electromagnetism"],
        ["Microeconomics", "Macroeconomics", "Econometrics", "Public Finance"],
        ["World Literature", "Poetry", "Creative Writing", "Literary Theory"],
    ]
    first = ["Avery", "Jordan", "Casey", "Morgan", "Riley", "Taylor", "Quinn", "Reese", "Skyler", "Drew"]
    last = ["Vale", "Hart", "Reed", "Barton", "Marsh", "Wren", "Dale", "Rowan", "Holt", "Finch", "Lake", "Stone"]
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='enrollments'").fetchone():
            connection.commit()
            return path
        try:
            for statement in DDL.strip().rstrip(";").split(";"):
                connection.execute(statement)
            connection.executemany("INSERT INTO departments VALUES (?,?)", enumerate(departments, 1))
            connection.executemany("INSERT INTO instructors VALUES (?,?,?)", [
                (i + 1, f"{name} {last[-i-1]}", i // 2 + 1) for i, name in enumerate(first)
            ])
            connection.executemany("INSERT INTO students VALUES (?,?,?,?,?)", [
                (i + 1, f"{first[i % 10]} {last[i // 10]}", i % 5 + 1,
                 rng.randint(2022, 2025), round(rng.uniform(2, 4), 2)) for i in range(120)
            ])
            connection.executemany("INSERT INTO courses VALUES (?,?,?,?,?)", [
                (d * 4 + c + 1, title, d + 1, d * 2 + c % 2 + 1, 2 + c % 3)
                for d, titles in enumerate(subjects) for c, title in enumerate(titles)
            ])
            enrollments = [
                (student, course, rng.choice(["2025 Spring", "2025 Fall", "2026 Spring"]),
                 max(35, min(100, round(rng.gauss(76, 12)))))
                for student in range(1, 121) for course in rng.sample(range(1, 21), 3)
            ]
            connection.executemany(
                "INSERT INTO enrollments(student_id,course_id,term,score) VALUES (?,?,?,?)",
                enrollments,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return path


@contextmanager
def open_readonly(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        yield connection
    finally:
        connection.close()


def inspect_schema(path):
    with open_readonly(path) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return {
            table: {
                "columns": [
                    {"name": row[1], "type": row[2], "primary_key": bool(row[5])}
                    for row in connection.execute("SELECT * FROM pragma_table_info(?)", (table,))
                ],
                "foreign_keys": [
                    {"column": row[3], "table": row[2], "target": row[4]}
                    for row in connection.execute("SELECT * FROM pragma_foreign_key_list(?)", (table,))
                ],
            }
            for (table,) in tables
        }


def format_schema(schema):
    lines = []
    for table, details in schema.items():
        columns = ", ".join(
            f"{col['name']} {col['type']}" + (" PRIMARY KEY" if col["primary_key"] else "")
            for col in details["columns"]
        )
        lines.append(f"{table}({columns})")
        lines.extend(f"  FOREIGN KEY {fk['column']} REFERENCES {fk['table']}({fk['target']})"
                     for fk in details["foreign_keys"])
    return "\n".join(lines)


if __name__ == "__main__":
    print(initialize_database())
