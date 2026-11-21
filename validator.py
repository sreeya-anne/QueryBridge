"""Validate a single SELECT and execute it within SQLite resource limits."""
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from time import monotonic

import sqlglot
from sqlglot import exp
from sqlglot.tokens import TokenType

from database import inspect_schema, open_readonly


@dataclass(frozen=True)
class Validation:
    category: str
    message: str = ""

    @property
    def valid(self):
        return self.category == "valid"


@dataclass(frozen=True)
class QueryResults:
    columns: tuple
    rows: tuple


class QueryRejected(ValueError):
    def __init__(self, validation):
        self.validation = validation
        super().__init__(validation.message)


def parse_select(sql):
    statements = [item for item in sqlglot.parse(sql, read="sqlite")
                  if item is not None and not isinstance(item, exp.Semicolon)]
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.SetOperation)):
        raise QueryRejected(Validation("unsafe", "Only a single read-only SELECT is allowed."))
    if any(node.key in {"insert", "update", "delete", "create", "drop", "alter", "attach",
                        "pragma", "command", "into", "transaction", "merge"}
           for node in statements[0].walk()):
        raise QueryRejected(Validation("unsafe", "The statement contains a prohibited operation."))
    return statements[0]


def strict_identifiers(sql):
    # Python 3.11 has no SQLite connection configuration API for legacy double-quoted strings.
    for token in reversed(sqlglot.tokenize(sql, read="sqlite")):
        if token.token_type == TokenType.IDENTIFIER and sql[token.start] == '"':
            identifier = token.text.replace("`", "``")
            sql = f"{sql[:token.start]}`{identifier}`{sql[token.end + 1:]}"
    return sql


class QueryEngine:
    def __init__(self, path, timeout=2.0, max_rows=5000):
        self.path = Path(path)
        self.tables = {name.casefold() for name in inspect_schema(path)}
        self.timeout = timeout
        self.max_rows = max_rows

    def _authorize(self, action, first, second, database, _source):
        if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE}:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and (second or "").casefold() not in {
            "load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"
        }:
            return sqlite3.SQLITE_OK
        # SQLite omits the database name for a table read without a column, as in COUNT(*).
        if (action == sqlite3.SQLITE_READ and (database == "main" or (database is None and second == ""))
                and (first or "").casefold() in self.tables):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def _run(self, sql, *, explain):
        parse_select(sql)
        with open_readonly(self.path) as connection:
            connection.set_authorizer(self._authorize)
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
            if hasattr(sqlite3, "SQLITE_DBCONFIG_DQS_DML"):
                connection.setconfig(sqlite3.SQLITE_DBCONFIG_DQS_DML, False)
            else:
                sql = strict_identifiers(sql)
            deadline = monotonic() + self.timeout
            connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
            try:
                cursor = connection.execute(("EXPLAIN QUERY PLAN " if explain else "") + sql)
                if explain:
                    return None
                rows = []
                result_bytes = 0
                for row in cursor:
                    if len(rows) == self.max_rows:
                        raise QueryRejected(Validation("limit", f"Result exceeds {self.max_rows:,} rows."))
                    result_bytes += sum(len(value) * 4 if isinstance(value, str)
                                        else len(value) if isinstance(value, bytes) else 16
                                        for value in row)
                    if result_bytes > 8_000_000:
                        raise QueryRejected(Validation("limit", "Result exceeds the output size limit."))
                    rows.append(row)
                return QueryResults(tuple(col[0] for col in cursor.description), tuple(rows))
            except sqlite3.Error as exc:
                message = str(exc)
                if "prohibited" in message or "authoriz" in message:
                    category = "unsafe"
                elif any(term in message for term in ("no such table", "no such column", "ambiguous column")):
                    category = "schema"
                elif "syntax error" in message or "incomplete input" in message:
                    category = "syntax"
                elif "interrupted" in message:
                    category = "timeout"
                else:
                    category = "sqlite"
                raise QueryRejected(Validation(category, message)) from exc

    def validate(self, sql):
        if not sql or not sql.strip():
            return Validation("syntax", "SQL is empty.")
        if len(sql) > 20_000:
            return Validation("limit", "SQL is too long.")
        try:
            self._run(sql, explain=True)
        except QueryRejected as exc:
            return exc.validation
        except (sqlglot.errors.SqlglotError, RecursionError) as exc:
            return Validation("syntax", str(exc))
        return Validation("valid")

    def execute(self, sql):
        validation = self.validate(sql)
        if not validation.valid:
            raise QueryRejected(validation)
        return self._run(sql, explain=False)
