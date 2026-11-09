"""Extract audit fields from the images/PDFs in a zip using the Gemini API.

Setup (Colab):
    !pip install google-genai pydantic
    # and set GEMINI_API_KEY (see gemini_handler.py for the Colab Secrets steps)

Files needed in the same folder:
    zip_to_llm.py, audit_prompt.py, schema.py, audit_extractor.py
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path

from google import genai
from google.genai import errors, types

from audit_prompt import AUDIT_EXTRACTION_INSTRUCTIONS
from schema import (  # noqa: F401 (other files import some of these from here)
    OUTPUT_FORMAT_NOTE, ChangeRecord, ExtractionResult, FieldResult, SourcedValue,
    gemini_response_schema, result_from_json,
)
from zip_to_llm import process_zip_grouped

# Model names change often. Check https://ai.google.dev/gemini-api/docs/models
MODEL = "gemini-3.8-flash"

# Every attempt, even a failed one, counts toward the free tier's daily limit,
# so we only try a few times and wait a long time between attempts.
MAX_ATTEMPTS = 3
SERVER_BUSY_WAIT_SECONDS = 90

last_raw_response = None  # Gemini's raw reply from the latest request, kept for troubleshooting
raw_responses = []        # every raw reply from the latest run (one per request)

MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".pdf": "application/pdf",
}


# ------------------------------------------------------------------
# Sending the evidence to Gemini
# ------------------------------------------------------------------

def _wait_seconds(error, default: int) -> int:
    """Google's rate-limit (429) message says "Please retry in 26.2s". Use that."""
    match = re.search(r"retry in ([\d.]+)s", str(error))
    return int(float(match.group(1))) + 2 if match else default


def _generate_with_retry(client, contents, config, max_attempts: int = MAX_ATTEMPTS):
    """Call Gemini, retrying a few times on temporary problems.

    Retried:  429 (too many requests per minute) and 5xx (Google's servers busy).
    Not retried: other errors (wrong model name, bad API key, invalid request...),
    and daily-quota errors, because trying again won't fix them.

    Every attempt, even a failed one, uses 1 of the free tier's 20 daily requests,
    so we try only a few times and wait a long time between attempts.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return client.models.generate_content(
                model=MODEL, contents=contents, config=config
            )
        except errors.APIError as e:
            is_rate_limit = e.code == 429
            is_server_busy = e.code is not None and e.code >= 500
            daily_quota = "PerDay" in str(e)
            if not (is_rate_limit or is_server_busy) or daily_quota or attempt == max_attempts:
                raise
            if is_rate_limit:
                delay = _wait_seconds(e, SERVER_BUSY_WAIT_SECONDS)  # Google tells us how long
            else:
                delay = SERVER_BUSY_WAIT_SECONDS * attempt  # 90s, then 180s
            if delay > 180:  # waiting this long is not worth it; stop and show the error
                raise
            print(f"Gemini returned {e.code} (attempt {attempt}/{max_attempts}). "
                  f"Waiting {delay}s, then trying again. Each attempt counts toward your daily quota.")
            time.sleep(delay)


def _strip_code_fence(text: str) -> str:
    """Remove a ```json ... ``` wrapper if the model added one."""
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.S)
    return match.group(1) if match else text


FOLDER_NOTE = """
EVIDENCE FOLDERS
Each evidence file is listed with its folder path, for example "CHG-10482/ticket.png".
Files in the same folder normally belong to the same change request, so use the folder as a
strong hint when grouping files by Change ID. The Change ID written in the documents decides.
Use the full path exactly as shown as the source_filename.
"""


def _send(files: list) -> ExtractionResult:
    """One request to Gemini. `files` is a list of (label, path); the label is what the model sees."""
    global last_raw_response
    client = genai.Client()  # reads GEMINI_API_KEY from the environment

    contents = []
    for label, path in files:
        # The file bytes carry no filename, so we tell the model the name in a text part
        # just before each file. This is what lets it fill in "source_filename" correctly.
        contents.append(f"Evidence file: {label}")
        contents.append(
            types.Part.from_bytes(
                data=path.read_bytes(),
                mime_type=MIME_TYPES[path.suffix.lower()],
            )
        )
    note = OUTPUT_FORMAT_NOTE + (FOLDER_NOTE if any("/" in label for label, _ in files) else "")
    contents.append("Extract the audit fields from all the evidence files above.\n" + note)

    config = types.GenerateContentConfig(
        system_instruction=AUDIT_EXTRACTION_INSTRUCTIONS,
        response_mime_type="application/json",
        response_json_schema=gemini_response_schema(),
        temperature=0,  # we want consistent extraction, not creativity
    )
    response = _generate_with_retry(client, contents, config)

    # Gemini returns a flat list of entries as JSON text; regroup it into one record per Change ID.
    last_raw_response = _strip_code_fence(response.text)
    raw_responses.append(last_raw_response)
    result = result_from_json(last_raw_response)
    if not result.changes:
        raise ValueError("Gemini returned no entries, so no change requests could be extracted. "
                         "See audit_extractor.last_raw_response.")
    return result


def extract_from_files(images: list[Path], pdfs: list[Path]) -> ExtractionResult:
    """All files in one request, without folder information."""
    return _send([(p.name, p) for p in images + pdfs])


def extract_from_groups(groups: list) -> ExtractionResult:
    """The files of several folders in one request, labelled with their folder paths."""
    return _send([(g.label(p), p) for g in groups for p in g.images + g.pdfs])


def _sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_audit_fields(
    zip_path: str,
    folders_per_request: int = 5,
    pause_seconds: int = 13,
    checkpoint_path: str = "extraction_progress.json",
    resume: bool = True,
) -> ExtractionResult:
    """Extract every folder of the zip (one folder = one change request).

    Several folders are sent in one request (`folders_per_request`) to save your daily quota.
    After every request the progress is saved to `checkpoint_path`, so if something fails
    half way, running it again continues where it stopped instead of paying for the same
    folders twice. It is tied to this exact zip file. Use resume=False to start from scratch.
    """
    raw_responses.clear()
    digest = _sha256(zip_path)
    done, changes, files_reviewed = set(), [], []

    if resume and os.path.exists(checkpoint_path):
        with open(checkpoint_path) as f:
            saved = json.load(f)
        if saved.get("zip_sha256") == digest:
            earlier = ExtractionResult.model_validate(saved["result"])
            done, changes, files_reviewed = set(saved["done_folders"]), list(earlier.changes), list(earlier.files_reviewed)
            print(f"Using saved results for {len(done)} folder(s) from {checkpoint_path} "
                  f"(pass resume=False to extract them again).")

    def run(groups):
        todo = [g for g in groups if g.name not in done]
        size = max(1, folders_per_request)
        batches = [todo[i:i + size] for i in range(0, len(todo), size)]
        for number, batch in enumerate(batches, start=1):
            if number > 1:
                time.sleep(pause_seconds)  # the free tier allows only a few requests per minute
            print(f"Request {number}/{len(batches)}: {', '.join(g.name for g in batch)}")
            try:
                part = extract_from_groups(batch)
            except Exception:
                print(f"Stopped at request {number}/{len(batches)}. Finished folders are saved in "
                      f"{checkpoint_path}; run it again to continue.")
                raise
            changes.extend(part.changes)
            files_reviewed.extend(part.files_reviewed)
            done.update(g.name for g in batch)
            combined = ExtractionResult(files_reviewed=list(dict.fromkeys(files_reviewed)), changes=changes)
            with open(checkpoint_path, "w") as f:
                json.dump({"zip_sha256": digest, "done_folders": sorted(done),
                           "result": combined.model_dump()}, f)
        return ExtractionResult(files_reviewed=list(dict.fromkeys(files_reviewed)), changes=changes)

    return process_zip_grouped(zip_path, run)


if __name__ == "__main__":
    result = extract_audit_fields("documents.zip")
    print(result.model_dump_json(indent=2))
