"""Plain-Python helpers behind app.py, kept separate so they can be tested without a browser."""

import os
import tempfile

import pandas as pd

import workflows as wf
from rules_engine import COLUMNS, evaluate_extraction, save_testing_table
from schema import ExtractionResult

NO_EXCEPTION = "No Exception"


def evaluations_to_dataframe(evaluations) -> pd.DataFrame:
    return pd.DataFrame([ev.row for ev in evaluations], columns=COLUMNS)


def summary(evaluations) -> dict:
    return {
        "changes": len(evaluations),
        "with_exceptions": sum(1 for ev in evaluations if ev.exception_types),
        "sod_failures": sum(1 for ev in evaluations if ev.sod_result.startswith("Fail")),
        "workflow": sum(1 for ev in evaluations if wf.WORKFLOW in ev.exception_types),
        "unreadable": sum(1 for ev in evaluations if "Blurry / Unreadable Evidence (Unable to Determine)" in ev.exception_types),
    }


def exception_choices(evaluations) -> list:
    # every exception type is always offered, so the list does not change from run to run
    return list(wf.EXCEPTION_ORDER) + [NO_EXCEPTION]


def filter_evaluations(evaluations, chosen: list):
    """Keep changes that have ANY of the chosen exception types ('No Exception' = clean ones). Empty = all."""
    if not chosen:
        return list(evaluations)
    keep = []
    for ev in evaluations:
        types = set(ev.exception_types) or {NO_EXCEPTION}
        if types & set(chosen):
            keep.append(ev)
    return keep


def excel_bytes(evaluations) -> bytes:
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        save_testing_table(evaluations, path)
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.remove(path)


def evaluations_from_extraction_json(text: str):
    """Rules only: no Gemini call, no quota."""
    return evaluate_extraction(ExtractionResult.model_validate_json(text))


def run_extraction_on_zip(zip_bytes: bytes, folders_per_request: int = 5):
    """Save the upload to a temp file, ask Gemini, return (evaluations, extraction_json_text)."""
    from audit_extractor import extract_audit_fields  # imported here so the demo mode needs no Gemini library

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = os.path.join(tmp, "upload.zip")
        with open(zip_path, "wb") as f:
            f.write(zip_bytes)
        result = extract_audit_fields(zip_path, folders_per_request=folders_per_request,
                                      checkpoint_path=os.path.join(tmp, "progress.json"), resume=False)
    return evaluate_extraction(result), result.model_dump_json(indent=2)


# ---------------------------------------------------------------- short summary for the page

def change_status(ev) -> str:
    """'exception' (red) or 'clean' (green). Observations are not exceptions: they stay in the Excel remarks."""
    return "exception" if ev.exception_types else "clean"


def short_remarks(ev, limit: int = 2):
    """(first few findings as plain sentences, how many more there are). Most serious first."""
    ordered = sorted(ev.findings, key=lambda f: 0 if f.kind != "observation" else 1)
    return [f.message for f in ordered[:limit]], max(0, len(ordered) - limit)
