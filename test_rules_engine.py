"""Tests for the rules engine. No Gemini needed.   Run:  python test_rules_engine.py"""

import os
import tempfile

from rules_engine import COLUMNS, evaluate_change, parse_date, save_testing_table
from schema import ChangeRecord, FieldResult, SourcedValue

ALL_FIELDS = list(ChangeRecord.model_fields)


# ---- helpers to build fake extraction data ----

def ok(*values, src="evidence.png", note=None, status="Available"):
    return FieldResult(
        values=[SourcedValue(value=v, source_filename=src, explicit_or_inferred="Explicit", note=note)
                for v in values],
        status=status,
    )


def unreadable():
    return FieldResult(values=[], status="Unable to Determine", explanation="blurred")


def record(**given):
    data = {name: FieldResult(values=[], status="Missing") for name in ALL_FIELDS}
    data.update(given)
    return ChangeRecord(**data)


def good_normal(**override):
    base = dict(
        change_id=ok("CHG001"), category=ok("Normal"),
        change_initiated_by=ok("Asha"), change_initiated_on=ok("1 Dec 2026"),
        pre_authorization_approval_by=ok("Boss"), pre_authorization_approval_on=ok("2 Dec 2026"),
        change_developed_by=ok("Dev Kumar"), change_developed_on=ok("3 Dec 2026"),
        peer_review_by=ok("Rita"), peer_review_on=ok("4 Dec 2026"),
        qa_signoff_by=ok("Tess"), qa_signoff_on=ok("5 Dec 2026"),
        pre_deployment_cab_approval_by=ok("Cabby"), pre_deployment_cab_approval_on=ok("6 Dec 2026"),
        deployment_by=ok("Depp"), deployment_on=ok("7 Dec 2026"),
    )
    base.update(override)
    return record(**base)


def good_emergency(**override):
    base = dict(
        change_id=ok("CHG900"), category=ok("Emergency"),
        change_initiated_by=ok("Asha"), change_initiated_on=ok("1 Dec 2026"),
        change_developed_by=ok("Dev Kumar"), change_developed_on=ok("1 Dec 2026"),
        ecab_approval_by=ok("Eve"), ecab_approval_on=ok("1 Dec 2026"),
        deployment_by=ok("Depp"), deployment_on=ok("2 Dec 2026"),
    )
    base.update(override)
    return record(**base)


def kinds(ev):
    return [f.kind for f in ev.findings]


# ---- tests ----

def test_dates():
    assert parse_date("3 Dec 2026")[1] is False
    assert parse_date("03 Dec 2026, 14:30")[1] is True
    assert parse_date("3 Dec 2026 2:30 PM")[0].hour == 14
    assert parse_date("3 December 2026") is not None
    assert parse_date("3 Sept 2026 14:30 IST") is not None
    assert parse_date("12/03/2026") is None
    print("PASS: date parsing")


def test_clean_normal():
    ev = evaluate_change(good_normal())
    assert ev.category == "Normal" and kinds(ev) == [], kinds(ev)
    assert ev.sod_result == "Pass"
    assert ev.exception_types == []
    assert ev.row["Exception / Observation"].startswith("Exception type: No Exception")
    assert list(ev.row) == COLUMNS
    print("PASS: clean Normal change has no findings and SoD passes")


def test_normal_missing_cab():
    ev = evaluate_change(good_normal(pre_deployment_cab_approval_by=ok(), pre_deployment_cab_approval_on=ok()))
    assert kinds(ev) == ["missing_control"], kinds(ev)
    assert ev.sod_result == "Not fully evaluated"
    print("PASS: Normal change without CAB -> missing control")


def test_normal_needs_qa_or_uat():
    no_test = good_normal(qa_signoff_by=ok(), qa_signoff_on=ok())
    assert "missing_control" in kinds(evaluate_change(no_test))
    uat_only = good_normal(qa_signoff_by=ok(), qa_signoff_on=ok(), uat_signoff_by=ok("Uma"), uat_signoff_on=ok("5 Dec 2026"))
    assert kinds(evaluate_change(uat_only)) == []
    both = good_normal(uat_signoff_by=ok("Uma"), uat_signoff_on=ok("5 Dec 2026"))
    ev = evaluate_change(both)
    assert kinds(ev) == [] and ev.row["QA/UAT sign-off by"] == "QA: Tess; UAT: Uma", ev.row["QA/UAT sign-off by"]
    print("PASS: QA only, UAT only and both are all accepted; neither is a finding")


def test_standard_optional_steps():
    rec = good_normal(category=ok("Standard"), qa_signoff_by=ok(), qa_signoff_on=ok(),
                      pre_deployment_cab_approval_by=ok(), pre_deployment_cab_approval_on=ok())
    ev = evaluate_change(rec)
    assert kinds(ev) == [], kinds(ev)
    assert ev.row["CAB approval by"] == "Not evidenced (optional)"
    print("PASS: Standard change may skip QA/UAT and CAB")


def test_emergency():
    assert kinds(evaluate_change(good_emergency())) == []
    ev = evaluate_change(good_emergency(ecab_approval_by=ok(), ecab_approval_on=ok()))
    assert kinds(ev) == ["missing_control"] and ev.sod_result == "Not fully evaluated"
    ev = evaluate_change(good_normal(ecab_approval_by=ok("Eve"), ecab_approval_on=ok("6 Dec 2026")))
    assert kinds(ev) == ["observation"], kinds(ev)
    print("PASS: Emergency needs E-CAB; E-CAB on a Normal change is only an observation")


def test_sod():
    ev = evaluate_change(good_normal(deployment_by=ok("dev  kumar")))  # same person, different spacing/case
    assert ev.sod_result.startswith("Fail") and kinds(ev) == ["sod_violation"], (ev.sod_result, kinds(ev))
    ev = evaluate_change(good_normal(pre_deployment_cab_approval_by=ok("Tess")))  # tester = approver
    assert ev.sod_result.startswith("Fail")
    ev = evaluate_change(good_normal(peer_review_by=ok("Dev Kumar")))  # peer review is not an SoD role
    assert ev.sod_result == "Pass"
    ev = evaluate_change(good_emergency(ecab_approval_by=ok("Depp")))  # E-CAB approver = deployer
    assert ev.sod_result.startswith("Fail")
    print("PASS: SoD fails for developer/tester/approver/deployer overlaps only")


def test_sequence():
    ev = evaluate_change(good_normal(pre_authorization_approval_on=ok("5 Dec 2026")))
    assert kinds(ev) == ["sequence"] and "Pre-authorization" in ev.findings[0].message
    ev = evaluate_change(good_normal(pre_deployment_cab_approval_on=ok("9 Dec 2026")))  # CAB after deployment
    assert kinds(ev) == ["sequence"]
    ev = evaluate_change(good_normal(change_developed_on=ok("2 Dec 2026"), pre_authorization_approval_on=ok("2 Dec 2026")))
    assert kinds(ev) == [], "same day must be accepted"
    ev = evaluate_change(good_normal(change_developed_on=ok("2 Dec 2026 14:00"), pre_authorization_approval_on=ok("2 Dec 2026 15:00")))
    assert kinds(ev) == ["sequence"], "same day but later time is a violation"
    ev = evaluate_change(good_normal(pre_authorization_approval_on=ok("12/03/2026")))
    assert kinds(ev) == ["observation"], "unreadable date must not be an exception"
    print("PASS: sequence checks (before/after, same day, times, unreadable dates)")


def test_category_problems():
    ev = evaluate_change(good_normal(category=ok("Normal", "Emergency", status="Conflicting Evidence")))
    assert ev.category is None and kinds(ev) == ["conflicting_evidence"], kinds(ev)
    assert ev.exception_types == ["Discrepancy / Conflicting Evidence"]
    assert ev.sod_result.startswith("Not evaluated")
    ev = evaluate_change(good_normal(category=FieldResult(values=[], status="Missing")))
    assert ev.category is None and kinds(ev) == ["missing_control"]
    assert "could not be determined" in ev.findings[0].message
    ev = evaluate_change(good_normal(category=unreadable()))
    assert ev.category is None and kinds(ev) == ["unable_to_determine"]
    ev = evaluate_change(good_normal(category=ok("Normal", "normal")))
    assert ev.category == "Normal"
    print("PASS: conflicting or missing category is flagged, not guessed")


def test_unreadable_is_not_a_failure():
    ev = evaluate_change(good_normal(pre_deployment_cab_approval_by=unreadable(), pre_deployment_cab_approval_on=unreadable()))
    assert kinds(ev) == ["unable_to_determine"], kinds(ev)
    assert ev.row["CAB approval by"] == "Unable to Determine"
    print("PASS: unreadable evidence -> 'unable to determine', not 'missing'")


def test_incomplete_and_conflicts():
    ev = evaluate_change(good_normal(pre_deployment_cab_approval_on=ok()))
    assert kinds(ev) == ["incomplete_evidence"], kinds(ev)
    ev = evaluate_change(good_normal(priority=ok("High", "Low", status="Conflicting Evidence")))
    assert kinds(ev) == ["conflicting_evidence"]
    assert ev.row["Priority"] == "High | Low (conflicting)"
    print("PASS: incomplete evidence and conflicting values are reported")


def test_exception_types():
    # each of your six types
    cases = {
        "Control Exception": good_normal(pre_deployment_cab_approval_by=ok(), pre_deployment_cab_approval_on=ok()),
        "Blurry / Unreadable Evidence (Unable to Determine)": good_normal(pre_deployment_cab_approval_by=unreadable(), pre_deployment_cab_approval_on=unreadable()),
        "Workflow Not Followed": good_normal(pre_authorization_approval_on=ok("5 Dec 2026")),
        "Segregation of Duties (SoD)": good_normal(deployment_by=ok("Dev Kumar")),
        "Discrepancy / Conflicting Evidence": good_normal(priority=ok("High", "Low", status="Conflicting Evidence")),
    }
    for expected, rec in cases.items():
        ev = evaluate_change(rec)
        assert ev.exception_types == [expected], (expected, ev.exception_types)
        assert ev.row["Exception / Observation"].startswith(f"Exception type: {expected}")
    # several at once are listed in a fixed order
    ev = evaluate_change(good_normal(
        pre_deployment_cab_approval_by=ok(), pre_deployment_cab_approval_on=ok(),
        deployment_by=ok("Dev Kumar"), pre_authorization_approval_on=ok("5 Dec 2026")))
    assert ev.exception_types == ["Control Exception", "Workflow Not Followed", "Segregation of Duties (SoD)"], ev.exception_types
    # an observation alone is NOT an exception
    ev = evaluate_change(good_normal(ecab_approval_by=ok("Eve"), ecab_approval_on=ok("6 Dec 2026")))
    assert ev.exception_types == [] and "[Observation]" in ev.row["Exception / Observation"]
    assert ev.row["Exception / Observation"].startswith("Exception type: No Exception")
    print("PASS: all six exception types, ordering, and observations are not exceptions")


def test_table_cells_and_new_formats():
    ev = evaluate_change(good_normal(
        release_version_id=FieldResult(values=[SourcedValue(value="16.4", source_filename="t.png",
                                                            explicit_or_inferred="Inferred")], status="Available"),
        pre_deployment_cab_approval_by=ok("Priya Nair", "Rahul Mehta")))
    assert ev.row["Release / Version ID"] == "16.4 (inferred)"
    assert ev.row["CAB approval by"] == "Priya Nair; Rahul Mehta", ev.row["CAB approval by"]  # not "conflicting"
    # month-first dates, as in Jira exports
    assert parse_date("September 8, 2020, 9:42 AM")[0].hour == 9
    assert parse_date("Oct 12, 2026 11:26 PM IST")[0].hour == 23
    assert kinds(evaluate_change(good_normal(change_initiated_on=ok("September 8, 2026, 9:42 AM")))) == []
    print("PASS: (inferred) marker, several approvers listed, month-first dates")


def test_sod_with_several_approvers():
    # one entry per person
    ev = evaluate_change(good_normal(pre_deployment_cab_approval_by=ok("Priya Nair", "Dev Kumar")))
    assert ev.sod_result.startswith("Fail"), ev.sod_result
    # names joined into one value are split as a safety net
    ev = evaluate_change(good_normal(pre_deployment_cab_approval_by=ok("Priya Nair, Dev Kumar, Meera Kapoor")))
    assert ev.sod_result.startswith("Fail"), ev.sod_result
    ev = evaluate_change(good_normal(pre_deployment_cab_approval_by=ok("Priya Nair and Dev Kumar")))
    assert ev.sod_result.startswith("Fail"), ev.sod_result
    # "Last, First" is one person and must not be split
    ev = evaluate_change(good_normal(change_developed_by=ok("Kumar, Dev"), pre_deployment_cab_approval_by=ok("Priya Nair")))
    assert ev.sod_result == "Pass", ev.sod_result
    print("PASS: SoD checks every CAB approver, including names joined in one value")


def test_excel():
    evs = [evaluate_change(good_normal()), evaluate_change(good_emergency())]
    with tempfile.TemporaryDirectory() as tmp:
        path = save_testing_table(evs, os.path.join(tmp, "t.xlsx"))
        from openpyxl import load_workbook
        ws = load_workbook(path).active
        assert [c.value for c in ws[1]] == COLUMNS
        assert ws.max_row == 3 and ws["A2"].value == "CHG001" and ws["A3"].value == "CHG900"
    print("PASS: Excel file has the right columns and rows")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("\nAll tests passed.")
