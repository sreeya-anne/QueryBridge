"""Durable evaluation journals and per-run locking."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile
from time import time

from llm import ModelError


def timestamp_slug(value):
    return datetime.fromisoformat(value).strftime("%Y%m%dT%H%M%S_%fZ")


def read_checkpoint(path):
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def lock_checkpoint(path):
    if path is None:
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # A separate SQLite file holds the OS-backed lock while the journal is replaced atomically.
    with closing(sqlite3.connect(path.with_suffix(".lock"), timeout=0)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise ModelError("This evaluation is already open in another worker.") from exc
        try:
            yield
        finally:
            connection.rollback()


class Journal:
    def __init__(self, path, manifest, timestamp):
        self.path = path
        self.state = {
            "manifest": manifest, "timestamp": timestamp, "rows": [], "responses": {},
            "events": [], "last_request_at": 0, "resume_after": 0, "pause_message": "",
        }
        if path and path.exists():
            self.state = read_checkpoint(path)
            if self.state["manifest"] != manifest:
                raise ModelError("Resume requires the same model, settings, questions, prompts and database.")

    def save(self):
        if self.path is None:
            return
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                delete=False) as handle:
            temporary = Path(handle.name)
            try:
                json.dump(self.state, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            except BaseException:
                handle.close()
                temporary.unlink(missing_ok=True)
                raise
        try:
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def pause(self, error):
        delay = error.retry_after
        self.state["resume_after"] = time() + delay if delay else 0
        self.state["pause_message"] = str(error)
        if delay:
            until = datetime.fromtimestamp(self.state["resume_after"], timezone.utc)
            self.state["pause_message"] += f" Provider retry delay ends at {until:%H:%M:%S UTC}."
        self.save()
