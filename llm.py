"""Provider adapter and traceable generation pipeline."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
from math import isfinite
import os
import re
from time import perf_counter

from dotenv import load_dotenv

from database import ROOT
from prompts import REPAIR_TEMPLATE, STRATEGIES


PROVIDERS = {
    "OpenAI": ("OPENAI_API_KEY", "OPENAI_MODEL", "gpt-4.1-mini"),
    "Gemini": ("GOOGLE_API_KEY", "GEMINI_MODEL", "gemini-3.5-flash-lite"),
}


def provider_config(provider):
    load_dotenv(ROOT / ".env")
    key, model, default = PROVIDERS[provider]
    return os.getenv(key, "").strip(), os.getenv(model, "").strip() or default


class ModelError(RuntimeError):
    def __init__(self, message, *, status_code=None, retry_after=None, quota_kind=None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.quota_kind = quota_kind

    @property
    def pauses_evaluation(self):
        return self.status_code == 429 or self.retry_after is not None


def retry_after_seconds(exc):
    headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
    delay = next((value for name, value in headers.items() if name.lower() == "retry-after"), None)
    if delay is None:
        return None
    try:
        seconds = float(delay)
    except (TypeError, ValueError):
        try:
            when = parsedate_to_datetime(delay)
            seconds = ((when if when.tzinfo else when.replace(tzinfo=timezone.utc))
                       - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0, seconds) if isfinite(seconds) else None


def rate_limit_error(provider, exc):
    retry_after = retry_after_seconds(exc)
    details = getattr(exc, "details", None) or getattr(exc, "body", None) or {}
    error = details.get("error", details) if isinstance(details, dict) else {}
    metadata = json.dumps(error, default=str).lower()
    compact = re.sub(r"[^a-z0-9]", "", metadata)
    if any(signal in compact for signal in ("perday", "daily", "perprojectperday", "insufficientquota")):
        kind = "quota"
    elif any(signal in compact for signal in ("perminute", "persecond", "ratelimitexceeded")):
        kind = "temporary"
    else:
        kind = "unknown"
    if isinstance(error, dict):
        for detail in error.get("details", []) or []:
            if isinstance(detail, dict) and detail.get("@type", "").endswith("RetryInfo"):
                value = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(detail.get("retryDelay", "")))
                if value:
                    retry_after = max(retry_after or 0, float(value[1]))
    if retry_after is not None and (retry_after < 0 or not float("-inf") < retry_after < float("inf")):
        retry_after = None
    reason = {
        "quota": "Quota exhausted. Check the project quota or wait for it to reset before resuming.",
        "temporary": "Temporary rate limit. Resume after the provider delay.",
        "unknown": "Rate limit or quota exceeded. Check the project quota before resuming.",
    }[kind]
    return ModelError(f"{provider} request failed (HTTP 429). {reason}",
                      status_code=429, retry_after=retry_after, quota_kind=kind)


class LLMClient:
    def __init__(self, provider):
        self.provider = provider
        key, self.model = provider_config(provider)
        if not key:
            raise ModelError(f"Set {PROVIDERS[provider][0]} to use {provider}.")
        if provider == "OpenAI":
            from openai import OpenAI
            self.client = OpenAI(api_key=key, timeout=30, max_retries=0)
        else:
            from google import genai
            self.client = genai.Client(api_key=key, vertexai=False, http_options={
                "timeout": 30000, "retry_options": {"attempts": 1}
            })

    @property
    def settings(self):
        return {"timeout_seconds": 30, "sampling": "provider defaults"}

    def complete(self, prompt):
        try:
            if self.provider == "OpenAI":
                response = self.client.responses.create(model=self.model, input=prompt)
                result = response.output_text
            else:
                result = self.client.models.generate_content(model=self.model, contents=prompt).text
        except Exception as exc:
            status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
            if status == 429:
                raise rate_limit_error(self.provider, exc) from None
            retry_after = retry_after_seconds(exc)
            if retry_after is not None:
                raise ModelError(f"{self.provider} request failed (HTTP {status}). The provider requested a delay.",
                                 status_code=status, retry_after=retry_after) from None
            if self.provider == "Gemini":
                # Provider error bodies can contain credentials or request data.
                reason = {
                    400: "Check the request and ensure GOOGLE_API_KEY is valid.",
                    401: "Check that GOOGLE_API_KEY is valid.",
                    403: "Check API key restrictions and Gemini API access for this project.",
                    404: "The configured model is unavailable for this project. Check GEMINI_MODEL and model access.",
                    500: "Google reported an internal error. Try again shortly.",
                    503: "Gemini is temporarily unavailable. Try again shortly.",
                }.get(status)
                if reason:
                    raise ModelError(f"Gemini request failed (HTTP {status}). {reason}") from None
                if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
                    raise ModelError("Gemini request timed out. Try again.") from None
            detail = f" (status {status})" if isinstance(status, int) else ""
            raise ModelError(f"{self.provider} request failed: {type(exc).__name__}{detail}.") from None
        if not result or not result.strip():
            raise ModelError(f"{self.provider} returned no text.")
        return result.strip()


def unfence(text):
    match = re.fullmatch(r"\s*```(?:sql|sqlite|json)?\s*\n?(.*?)\s*```\s*", text,
                         re.DOTALL | re.IGNORECASE)
    return match[1].strip() if match else text.strip()


def decode_response(text, version):
    cleaned = unfence(text)
    if version == "V3":
        data = json.loads(cleaned)
        if not isinstance(data, dict) or data.get("status") not in {"sql", "clarification", "refusal"}:
            raise ValueError("Expected a JSON response with a recognized status.")
        if not all(isinstance(data.get(key), str) for key in ("sql", "explanation")):
            raise ValueError("Expected string fields for SQL and explanation.")
        if (data["status"] == "sql") != bool(data["sql"].strip()):
            raise ValueError("Only a SQL response may contain a query.")
        return data["status"], data["sql"].strip(), data["explanation"]
    if cleaned.upper().startswith("REFUSAL:"):
        return "refusal", "", cleaned.split(":", 1)[1].strip()
    return "sql", cleaned, ""


@dataclass
class Generation:
    version: str
    prompt: str
    timestamp: str
    status: str = "output_format"
    original_sql: str = ""
    corrected_sql: str = ""
    explanation: str = ""
    validation: str = ""
    original_validation: str = ""
    error: str = ""
    original_error: str = ""
    raw_response: str = ""
    repair_prompt: str = ""
    repair_response: str = ""
    duration_seconds: float = 0.0
    attempts: int = 0
    repair_status: str = ""
    repair_validation: str = ""
    repair_error: str = ""
    provider: str = ""
    model: str = ""
    settings: dict = field(default_factory=dict)

    @property
    def sql(self):
        return self.corrected_sql if self.repair_status == "sql" else self.original_sql


def generate(client, engine, version, question, schema, *, before_repair=None):
    started = perf_counter()
    record = Generation(version, STRATEGIES[version].render(question, schema),
                        datetime.now(timezone.utc).isoformat(), provider=client.provider,
                        model=client.model, settings=client.settings)
    try:
        record.attempts += 1
        record.raw_response = client.complete(record.prompt)
        record.status, record.original_sql, record.explanation = decode_response(record.raw_response, version)
        if record.status == "sql":
            check = engine.validate(record.original_sql)
            record.original_validation = record.validation = check.category
            record.original_error = record.error = check.message
            if version == "V3" and check.category in {"syntax", "schema", "sqlite"}:
                fields = {"schema": schema, "question": question, "sql": record.original_sql, "error": check.message}
                record.repair_prompt = REPAIR_TEMPLATE.format(**fields)
                if before_repair:
                    before_repair(record)
                record.attempts += 1
                try:
                    record.repair_response = client.complete(record.repair_prompt)
                    status, record.corrected_sql, explanation = decode_response(record.repair_response, version)
                    record.repair_status = record.status = status
                    record.explanation = explanation
                    check = engine.validate(record.corrected_sql) if status == "sql" else None
                    record.repair_validation = record.validation = check.category if check else ""
                    record.repair_error = record.error = check.message if check else ""
                except (ModelError, ValueError) as exc:
                    record.repair_status = "api_error" if isinstance(exc, ModelError) else "output_format"
                    record.repair_error = str(exc)
    except (ModelError, ValueError) as exc:
        record.status = "api_error" if isinstance(exc, ModelError) else "output_format"
        record.error = str(exc)
    finally:
        record.duration_seconds = perf_counter() - started
    return record
