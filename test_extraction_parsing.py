"""Tests for the flat-entries schema and regrouping. No Gemini needed.   Run: python test_extraction_parsing.py"""

import json

from rules_engine import evaluate_change
from schema import FIELD_LABELS, ChangeRecord, gemini_response_schema, result_from_json


def entry(change_id, field, value="", status="Available", src="a.png", note="", explanation="", kind="Explicit"):
    return {"change_id": change_id, "field": field, "status": status, "value": value,
            "source_filename": src, "explicit_or_inferred": kind, "note": note, "explanation": explanation}


def reply(*entries, files=("a.png",)):
    return json.dumps({"files_reviewed": list(files), "entries": list(entries)})


def test_schema_is_small_and_plain():
    schema = gemini_response_schema()
    text = json.dumps(schema)
    assert len(text) < 3000, len(text)
    for bad in ("$ref", "$defs", "anyOf", "default", "null", "title"):
        assert bad not in text, bad
    props = schema["properties"]["entries"]["items"]
    assert props["required"] == list(props["properties"])
    assert len(props["properties"]["field"]["enum"]) == 26 == len(ChangeRecord.model_fields)
    assert set(FIELD_LABELS.values()) == set(ChangeRecord.model_fields)
    assert "enum" not in json.dumps(gemini_response_schema(use_enums=False))
    print("PASS: schema is small, flat, and covers all 26 fields")


def test_basic_grouping():
    r = result_from_json(reply(
        entry("CHG1", "Change ID", "CHG1"),
        entry("CHG1", "Risk", "High", src="ticket.png"),
        entry("CHG1", "Priority", "", status="Missing", src=""),
        entry("CHG1", "Peer Review By", "", status="Unable to Determine", src="blur.pdf", explanation="blurred"),
    ))
    assert len(r.changes) == 1
    c = r.changes[0]
    assert c.change_id.values[0].value == "CHG1" and c.change_id.status == "Available"
    assert c.risk.values[0].source_filename == "ticket.png"
    assert c.priority.status == "Missing" and c.priority.values == []
    assert c.peer_review_by.status == "Unable to Determine" and c.peer_review_by.explanation == "blurred"
    assert c.deployment_by.status == "Missing" and "did not return" in c.deployment_by.explanation
    print("PASS: entries are regrouped; omitted fields become Missing")


def test_multiple_values_and_conflicts():
    r = result_from_json(reply(
        entry("CHG1", "Category", "Normal", src="ticket.png", note="ticket details", status="Conflicting Evidence"),
        entry("CHG1", "Category", "Emergency", src="form.png", note="dropdown", status="Conflicting Evidence"),
        entry("CHG1", "QA Sign-off By", "Tess"),
        entry("CHG1", "UAT Sign-off By", "Uma", src="uat.png"),
    ))
    c = r.changes[0]
    assert [v.value for v in c.category.values] == ["Normal", "Emergency"] and c.category.status == "Conflicting Evidence"
    assert c.category.values[1].note == "dropdown"
    assert c.qa_signoff_by.values[0].value == "Tess" and c.uat_signoff_by.values[0].value == "Uma"
    print("PASS: several values per field and QA/UAT are kept")


def test_several_changes_and_junk():
    r = result_from_json(reply(
        entry("CHG1", "Risk", "High"), entry("CHG2", "Risk", "Low"),
        entry("CHG2", "Not A Real Field", "x"),          # unknown field is ignored
        entry("", "Risk", "Medium"),                      # no change id -> UNKNOWN group
    ))
    assert len(r.changes) == 3
    assert [c.risk.values[0].value for c in r.changes] == ["High", "Low", "Medium"]
    assert r.changes[2].change_id.values == []            # UNKNOWN: no invented id
    print("PASS: several change requests, unknown fields ignored, no invented IDs")


def test_empty_values_are_not_evidence():
    r = result_from_json(reply(entry("CHG1", "Deployment By", "   ", status="Available")))
    c = r.changes[0]
    assert c.deployment_by.values == [] and c.deployment_by.status == "Missing"
    print("PASS: a blank value is treated as missing, not as evidence")


def test_feeds_rules_engine():
    e = lambda f, v, s="a.png": entry("CHG1", f, v, src=s)
    r = result_from_json(reply(
        e("Change ID", "CHG1"), e("Category", "Normal"),
        e("Change Initiated By", "Asha"), e("Change Initiated On", "1 Dec 2026"),
        e("Change Developed By", "Dev"), e("Change Developed On", "3 Dec 2026"),
        e("QA Sign-off By", "Tess"), e("QA Sign-off On", "5 Dec 2026"),
        e("Pre-deployment / CAB Approval By", "Cabby"), e("Pre-deployment / CAB Approval On", "6 Dec 2026"),
        e("Deployment By", "Dev"), e("Deployment On", "7 Dec 2026"),   # developer deployed it
    ))
    ev = evaluate_change(r.changes[0])
    assert ev.category == "Normal" and ev.sod_result.startswith("Fail"), (ev.category, ev.sod_result)
    assert ev.exception_types == ["Segregation of Duties (SoD)"], ev.exception_types
    print("PASS: regrouped result works in the rules engine")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("\nAll tests passed.")
