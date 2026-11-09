"""The shape of the data Gemini returns (the "answer form").

Kept in its own file so the rules engine can use it without needing the Gemini library.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ------------------------------------------------------------------
# Output schema: the shape of the JSON that Gemini must return.
# ------------------------------------------------------------------

class SourcedValue(BaseModel):
    """One value found in the evidence, and where it came from."""
    value: str
    source_filename: str
    explicit_or_inferred: Literal["Explicit", "Inferred"]
    note: Optional[str] = None  # e.g. "ticket details page" vs "dropdown in form"


class FieldResult(BaseModel):
    """Everything we know about one field."""
    values: list[SourcedValue]  # several entries if sources conflict; empty if Missing
    status: Literal["Available", "Missing", "Unable to Determine", "Conflicting Evidence"]
    explanation: Optional[str] = None  # filled in when uncertain or conflicting


class ChangeRecord(BaseModel):
    """The 26 audit fields for one Change ID.

    QA and UAT are separate fields because either one, or both, may exist.
    Whether one of them is enough is decided later by your rules engine.

    SoD Result and Exception / Observation are NOT here on purpose: your Python
    rules engine works those out later from these extracted fields.
    """
    change_id: FieldResult
    release_version_id: FieldResult
    description: FieldResult
    priority: FieldResult
    risk: FieldResult
    category: FieldResult
    change_initiated_by: FieldResult
    change_initiated_on: FieldResult
    pre_authorization_approval_by: FieldResult
    pre_authorization_approval_on: FieldResult
    change_developed_by: FieldResult
    change_developed_on: FieldResult
    peer_review_by: FieldResult
    peer_review_on: FieldResult
    qa_signoff_by: FieldResult
    qa_signoff_on: FieldResult
    uat_signoff_by: FieldResult
    uat_signoff_on: FieldResult
    pre_deployment_cab_approval_by: FieldResult
    pre_deployment_cab_approval_on: FieldResult
    ecab_approval_by: FieldResult
    ecab_approval_on: FieldResult
    deployment_by: FieldResult
    deployment_on: FieldResult
    # PIR was added later: older saved extraction files do not have it, so default to Missing
    pir_by: FieldResult = Field(default_factory=lambda: FieldResult(values=[], status="Missing"))
    pir_on: FieldResult = Field(default_factory=lambda: FieldResult(values=[], status="Missing"))


class ExtractionResult(BaseModel):
    files_reviewed: list[str]        # every filename the model says it looked at
    changes: list[ChangeRecord]      # one entry per Change ID found


# ---------------------------------------------------------------------------
# What we actually send to Gemini: a SMALL, FLAT schema
#
# Google's docs warn that large or deeply nested schemas can be rejected with a 400.
# So Gemini returns a flat list of "entries" (one per field value), and
# result_from_json() regroups them into the ExtractionResult used by the rules engine.
# ---------------------------------------------------------------------------

import json

# The names Gemini uses for fields (the same names as in audit_prompt.py)
# mapped to the matching field in ChangeRecord.
FIELD_LABELS = {
    "Change ID": "change_id",
    "Release / Version ID": "release_version_id",
    "Description": "description",
    "Priority": "priority",
    "Risk": "risk",
    "Category": "category",
    "Change Initiated By": "change_initiated_by",
    "Change Initiated On": "change_initiated_on",
    "Pre-authorization Approval By": "pre_authorization_approval_by",
    "Pre-authorization Approval On": "pre_authorization_approval_on",
    "Change Developed By": "change_developed_by",
    "Change Developed On": "change_developed_on",
    "Peer Review By": "peer_review_by",
    "Peer Review On": "peer_review_on",
    "QA Sign-off By": "qa_signoff_by",
    "QA Sign-off On": "qa_signoff_on",
    "UAT Sign-off By": "uat_signoff_by",
    "UAT Sign-off On": "uat_signoff_on",
    "Pre-deployment / CAB Approval By": "pre_deployment_cab_approval_by",
    "Pre-deployment / CAB Approval On": "pre_deployment_cab_approval_on",
    "E-CAB Approval By": "ecab_approval_by",
    "E-CAB Approval On": "ecab_approval_on",
    "Deployment By": "deployment_by",
    "Deployment On": "deployment_on",
    "Post-Implementation Review (PIR) By": "pir_by",
    "Post-Implementation Review (PIR) On": "pir_on",
}
STATUSES = ["Available", "Missing", "Unable to Determine", "Conflicting Evidence"]

OUTPUT_FORMAT_NOTE = """
OUTPUT FORMAT
Return a list of entries.
- For EVERY Change ID, return entries covering ALL 26 fields listed in the instructions.
  Use the field names exactly as they appear in the schema.
- "change_id" on every entry is the Change ID that the entry belongs to.
- Return one entry per extracted value. If a field has several values (for example
  conflicting sources), return one entry per value, each with its own source_filename.
- If several people performed the same step (for example several CAB approvers), return one
  entry per person. Never put a list of names into one value.
- QA and UAT are separate fields (QA Sign-off By/On and UAT Sign-off By/On).
- If a field is not in the evidence, return one entry with an empty value, an empty
  source_filename and the status Missing.
- If evidence exists but is unreadable or ambiguous, return one entry with an empty value
  (or the partly readable text), the status Unable to Determine, and say why in "explanation".
- Use an empty string for "note" and "explanation" when there is nothing to add.
"""


def gemini_response_schema(use_enums: bool = True) -> dict:
    """The flat answer schema sent to Gemini (about 1.5 KB, 3 levels deep)."""
    def text(values=None):
        return {"type": "string", "enum": values} if (values and use_enums) else {"type": "string"}

    properties = {
        "change_id": text(),
        "field": text(list(FIELD_LABELS)),
        "status": text(STATUSES),
        "value": text(),
        "source_filename": text(),
        "explicit_or_inferred": text(["Explicit", "Inferred"]),
        "note": text(),
        "explanation": text(),
    }
    entry = {"type": "object", "properties": properties, "required": list(properties)}
    return {
        "type": "object",
        "properties": {
            "files_reviewed": {"type": "array", "items": {"type": "string"}},
            "entries": {"type": "array", "items": entry},
        },
        "required": ["files_reviewed", "entries"],
    }


def _field_result(entries: list) -> FieldResult:
    if not entries:
        return FieldResult(values=[], status="Missing", explanation="The model did not return this field.")
    values = []
    for e in entries:
        value = (e.get("value") or "").strip()
        if value:
            values.append(SourcedValue(
                value=value,
                source_filename=(e.get("source_filename") or "").strip(),
                explicit_or_inferred="Inferred" if e.get("explicit_or_inferred") == "Inferred" else "Explicit",
                note=(e.get("note") or "").strip() or None,
            ))
    statuses = {e.get("status") for e in entries}
    if "Conflicting Evidence" in statuses:
        status = "Conflicting Evidence"
    elif "Unable to Determine" in statuses:
        status = "Unable to Determine"
    elif values:
        status = "Available"
    else:
        status = "Missing"
    explanations = list(dict.fromkeys((e.get("explanation") or "").strip() for e in entries))
    explanation = " ".join(x for x in explanations if x) or None
    return FieldResult(values=values, status=status, explanation=explanation)


def result_from_json(text: str) -> ExtractionResult:
    """Turn Gemini's flat list of entries into an ExtractionResult (one ChangeRecord per Change ID)."""
    data = json.loads(text)
    grouped = {}  # change id -> field key -> entries
    for e in data.get("entries", []):
        key = FIELD_LABELS.get(e.get("field"))
        if key is None:
            continue  # unknown field name: ignore
        change_id = (e.get("change_id") or "").strip() or "UNKNOWN"
        grouped.setdefault(change_id, {}).setdefault(key, []).append(e)

    changes = []
    for change_id, fields in grouped.items():
        record = {key: _field_result(fields.get(key, [])) for key in ChangeRecord.model_fields}
        if not record["change_id"].values and change_id != "UNKNOWN":
            record["change_id"] = FieldResult(
                values=[SourcedValue(value=change_id, source_filename="", explicit_or_inferred="Inferred",
                                     note="taken from the change_id of the entries")],
                status="Available")
        changes.append(ChangeRecord(**record))
    return ExtractionResult(files_reviewed=list(data.get("files_reviewed", [])), changes=changes)
