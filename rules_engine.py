"""Steps 5-9 of the audit workflow, in plain Python (no Gemini calls here).

Input : the extraction result (ExtractionResult) produced by audit_extractor.py
Output: for each change request, a list of findings, an SoD result and one row
        for the testing table, plus an Excel file.

    5. determine_category      6. the WORKFLOWS table in workflows.py
    7. check_controls, check_sequence, evaluate_sod
    8. Finding objects, mapped to your exception types (workflows.EXCEPTION_TYPE_BY_KIND)
    9. build_row, save_testing_table
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations

import workflows as wf
from schema import ChangeRecord, ExtractionResult, FieldResult

COLUMNS = [
    "Change ID", "Release / Version ID", "Description", "Priority", "Risk", "Category",
    "Change initiated by", "Change initiated on",
    "Pre-authorization approved by", "Pre-authorization approved on",
    "Change developed by", "Change developed on",
    "Peer review by", "Peer review on",
    "QA/UAT sign-off by", "QA/UAT sign-off on",
    "CAB approval by", "CAB approval on",
    "E-CAB approval by", "E-CAB approval on",
    "Deployment by", "Deployment on",
    "PIR by", "PIR on",
    "SoD Result", "Exception / Observation",
    "Evidence folder",
]

# Which testing-table columns belong to which workflow step.
STEP_COLUMNS = {
    "initiation": ("Change initiated by", "Change initiated on"),
    "pre_authorization": ("Pre-authorization approved by", "Pre-authorization approved on"),
    "development": ("Change developed by", "Change developed on"),
    "peer_review": ("Peer review by", "Peer review on"),
    "qa_uat": ("QA/UAT sign-off by", "QA/UAT sign-off on"),
    "cab": ("CAB approval by", "CAB approval on"),
    "ecab": ("E-CAB approval by", "E-CAB approval on"),
    "deployment": ("Deployment by", "Deployment on"),
    "pir": ("PIR by", "PIR on"),
}


@dataclass
class Finding:
    kind: str      # a key of workflows.EXCEPTION_NAMES
    step: str
    message: str
    sources: list = field(default_factory=list)  # evidence filenames


@dataclass
class ChangeEvaluation:
    change_id: str
    category: str | None
    findings: list
    exception_types: list   # your exception types, e.g. ["Control Exception"]; empty = No Exception
    sod_result: str
    sod_details: list
    row: dict
    record: object = None   # the ChangeRecord this evaluation is based on
    folder: str = "-"       # the folder the evidence came from ("-" when the zip has no folders)

    def rebuild(self):
        """Refresh the exception types and table row after findings were added."""
        self.exception_types = exception_types(self.findings)
        self.row = build_row(self.record, self.category, self.findings, self.sod_result,
                             self.sod_details, self.folder)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _values(fr: FieldResult) -> list[str]:
    return [v.value.strip() for v in fr.values if v.value and v.value.strip()]


def _distinct(items: list[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        key = re.sub(r"\s+", " ", item).strip().casefold()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _sources(frs) -> list[str]:
    out = []
    for fr in frs:
        for v in fr.values:
            if v.source_filename and v.source_filename not in out:
                out.append(v.source_filename)
    return out


def _label(field_name: str) -> str:
    return field_name.replace("_", " ").capitalize()


# ---------------------------------------------------------------------------
# Dates: the evidence uses the format "3 Dec 2026" (optionally with a time)
# ---------------------------------------------------------------------------

_DATE_FORMATS = []
# Day first ("3 Dec 2026") and month first ("December 3, 2026"). Only dates with a
# written month are read: numeric dates like 12/03/2026 are ambiguous, so they are not guessed.
for _layout in ("%d {m} %Y", "{m} %d %Y"):
    for _month in ("%b", "%B"):
        _day = _layout.format(m=_month)
        _DATE_FORMATS += [
            (_day + " %H:%M:%S", True), (_day + " %H:%M", True),
            (_day + " %I:%M:%S %p", True), (_day + " %I:%M %p", True),
            (_day, False),
        ]


def parse_date(text: str):
    """Return (datetime, has_time) or None if the text is not a readable date."""
    cleaned = text.replace(",", " ")
    cleaned = re.sub(r"\bSept\b", "Sep", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*(UTC|GMT|IST|[+-]\d{2}:?\d{2})$", "", cleaned.strip(), flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for fmt, has_time in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt), has_time
        except ValueError:
            continue
    return None


@dataclass
class StepDate:
    when: datetime
    has_time: bool
    text: str
    source: str


def _is_after(a: StepDate, b: StepDate) -> bool:
    """Is a dated strictly after b? Times are compared only if both have one."""
    if a.has_time and b.has_time:
        return a.when > b.when
    return a.when.date() > b.when.date()


def _step_dates(record: ChangeRecord, step_key: str):
    good, unreadable = [], []
    for _, on_name in wf.STEPS[step_key]["pairs"]:
        for v in getattr(record, on_name).values:
            parsed = parse_date(v.value)
            if parsed:
                good.append(StepDate(parsed[0], parsed[1], v.value, v.source_filename))
            else:
                unreadable.append(v)
    return good, unreadable


# ---------------------------------------------------------------------------
# Step 5: category
# ---------------------------------------------------------------------------

_CATEGORY_WORDS = {"normal": "Normal", "standard": "Standard", "emergency": "Emergency"}


def determine_category(record: ChangeRecord):
    """Return (category or None, Finding or None)."""
    fr = record.category
    found = []
    for v in fr.values:
        text = v.value.casefold()
        matches = [cat for word, cat in _CATEGORY_WORDS.items() if re.search(rf"\b{word}\b", text)]
        if len(matches) == 1:
            found.append((matches[0], v))
    categories = {cat for cat, _ in found}

    if len(categories) == 1:
        return categories.pop(), None
    if len(categories) > 1:
        shown = "; ".join(
            f"{v.value} ({v.source_filename}{', ' + v.note if v.note else ''})" for _, v in found
        )
        return None, Finding(
            "conflicting_evidence", "category",
            f"Category differs between sources: {shown}. The workflow could not be applied; auditor to decide.",
            _sources([fr]),
        )
    if fr.status == "Unable to Determine":
        return None, Finding(
            "unable_to_determine", "category",
            "Category could not be determined because the evidence is unreadable or ambiguous. "
            "The workflow could not be applied.",
            _sources([fr]),
        )
    return None, Finding(
        "missing_control", "category",
        "Category could not be determined: no category was found in the evidence. "
        "The workflow could not be applied.",
        _sources([fr]),
    )


# ---------------------------------------------------------------------------
# Step 7a: are the required controls evidenced?
# ---------------------------------------------------------------------------

def step_status(record: ChangeRecord, step_key: str) -> str:
    """Evidenced / Incomplete / Unable to Determine / Missing"""
    any_value = complete = unreadable = False
    for by_name, on_name in wf.STEPS[step_key]["pairs"]:
        by, on = getattr(record, by_name), getattr(record, on_name)
        if _values(by) and _values(on):
            complete = True
        if _values(by) or _values(on):
            any_value = True
        if by.status == "Unable to Determine" or on.status == "Unable to Determine":
            unreadable = True
    if complete:
        return "Evidenced"
    if any_value:
        return "Incomplete"
    if unreadable:
        return "Unable to Determine"
    return "Missing"


def _what_is_missing(record: ChangeRecord, step_key: str) -> str:
    pairs = wf.STEPS[step_key]["pairs"]
    has_who = any(_values(getattr(record, b)) for b, _ in pairs)
    has_when = any(_values(getattr(record, o)) for _, o in pairs)
    if has_who and not has_when:
        return "a name is recorded but no date"
    if has_when and not has_who:
        return "a date is recorded but no name"
    return "the name or date is missing"


def check_controls(record: ChangeRecord, category: str) -> list[Finding]:
    findings = []
    for key, cfg in wf.STEPS.items():
        requirement = wf.WORKFLOWS[category][key]
        state = step_status(record, key)
        label = cfg["label"]
        sources = _sources([getattr(record, n) for pair in cfg["pairs"] for n in pair])

        if requirement in (wf.REQUIRED, wf.QA_OR_UAT):
            need = "QA or UAT sign-off is" if requirement == wf.QA_OR_UAT else f"{label} is"
            if state == "Missing":
                findings.append(Finding("missing_control", key,
                    f"{label}: no evidence found, but {need} required for a {category} change.", sources))
            elif state == "Incomplete":
                findings.append(Finding("incomplete_evidence", key,
                    f"{label}: evidence is incomplete ({_what_is_missing(record, key)}).", sources))
            elif state == "Unable to Determine":
                findings.append(Finding("unable_to_determine", key,
                    f"{label}: the evidence is blurred, cropped or ambiguous, so it could not be determined "
                    f"whether this required control was performed.", sources))
        if state == "Evidenced" and len(cfg["pairs"]) > 1:
            # QA and UAT are separate: one readable sign-off satisfies the control, but an unreadable
            # sibling (e.g. a blurred UAT page) is still worth telling the auditor about.
            for (by_name, on_name), part in zip(cfg["pairs"], ("QA", "UAT")):
                by, on = getattr(record, by_name), getattr(record, on_name)
                if not (_values(by) and _values(on)) and "Unable to Determine" in (by.status, on.status):
                    findings.append(Finding("observation", key,
                        f"{part} sign-off: could not be determined (unreadable evidence). "
                        f"The {label} control is still met by the other sign-off.",
                        _sources([by, on])))

        if requirement in (wf.REQUIRED, wf.QA_OR_UAT):
            pass
        elif requirement == wf.NOT_STANDARD:
            if state in ("Evidenced", "Incomplete"):
                findings.append(Finding("observation", key,
                    f"{label}: evidence found, although this is not the standard path for a {category} change.",
                    sources))
        else:  # optional
            if state == "Incomplete":
                findings.append(Finding("observation", key,
                    f"{label} (optional): evidence is incomplete ({_what_is_missing(record, key)}).", sources))
            elif state == "Unable to Determine":
                findings.append(Finding("observation", key,
                    f"{label} (optional): could not be determined whether this was performed (unreadable evidence).",
                    sources))
    return findings


def check_conflicts(record: ChangeRecord) -> list[Finding]:
    findings = []
    for name in ChangeRecord.model_fields:
        if name == "category":
            continue  # handled in determine_category
        fr = getattr(record, name)
        if fr.status == "Conflicting Evidence":
            shown = "; ".join(f"{v.value} ({v.source_filename})" for v in fr.values) or "no values listed"
            extra = f" {fr.explanation}" if fr.explanation else ""
            findings.append(Finding("conflicting_evidence", name,
                f"{_label(name)}: sources disagree: {shown}.{extra}", _sources([fr])))
    return findings


# ---------------------------------------------------------------------------
# Step 7b: sequence
# ---------------------------------------------------------------------------

def check_sequence(record: ChangeRecord, category: str) -> list[Finding]:
    findings = []
    dates = {}
    for key, cfg in wf.STEPS.items():
        dates[key], unreadable = _step_dates(record, key)
        if unreadable:
            findings.append(Finding("observation", key,
                f"{cfg['label']}: the date '{unreadable[0].value}' could not be read as a date "
                f"(expected a format like 3 Dec 2026), so sequence checks using it were skipped.",
                [v.source_filename for v in unreadable]))

    previous = None
    for key in wf.WORKFLOW_ORDER[category]:
        if not dates[key]:
            continue
        if previous:
            earlier, later = previous, key
            violation = next(((a, b) for a in dates[earlier] for b in dates[later] if _is_after(a, b)), None)
            if violation:
                a, b = violation
                findings.append(Finding("sequence", f"{earlier}->{later}",
                    f"{wf.STEPS[earlier]['label']} ({a.text}) is dated after {wf.STEPS[later]['label']} ({b.text}); "
                    f"the workflow order expects it first.",
                    [s for s in dict.fromkeys([a.source, b.source]) if s]))
        previous = key
    return findings


# ---------------------------------------------------------------------------
# Step 7c: segregation of duties
# ---------------------------------------------------------------------------

def _split_people(values: list[str]) -> list[str]:
    """Safety net: split "Priya Nair, Rahul Mehta and Meera Kapoor" into separate people.

    Gemini is asked to return one entry per person, but if it still joins names we split them.
    "Smith, John" is NOT split, because each piece of a split must itself be a full name (2+ words).
    """
    people = []
    for value in values:
        parts = [p.strip() for p in re.split(r";|\band\b|&", value) if p.strip()]
        for part in parts:
            pieces = [p.strip() for p in part.split(",") if p.strip()]
            if len(pieces) > 1 and all(len(p.split()) >= 2 for p in pieces):
                people.extend(pieces)
            else:
                people.append(part)
    return people


def evaluate_sod(record: ChangeRecord, category: str):
    """Return (result, details, findings)."""
    roles = {}
    for role, cfg in wf.SOD_ROLES.items():
        step = cfg["step"] if "step" in cfg else cfg["step_by_category"][category]
        requirement = wf.WORKFLOWS[category][step]
        by_fields = [getattr(record, by) for by, _ in wf.STEPS[step]["pairs"]]
        people = {}
        for fr in by_fields:
            for name in _split_people(_values(fr)):
                people.setdefault(re.sub(r"\s+", " ", name).casefold(), name)
        roles[role] = {
            "people": people,
            "required": requirement.startswith("Required"),
            "sources": _sources(by_fields),
        }

    findings, details = [], []
    for (r1, d1), (r2, d2) in combinations(roles.items(), 2):
        shared = d1["people"].keys() & d2["people"].keys()
        if shared:
            who = ", ".join(d1["people"][k] for k in sorted(shared))
            text = f"{r1} and {r2} are the same individual ({who})"
            details.append(text)
            findings.append(Finding("sod_violation", "sod",
                f"Segregation of duties: {text}.", list(dict.fromkeys(d1["sources"] + d2["sources"]))))

    if findings:
        return "Fail", details, findings
    unevaluated = [r for r, d in roles.items() if d["required"] and not d["people"]]
    if unevaluated:
        return "Not fully evaluated", [f"no name found for: {', '.join(unevaluated)}"], []
    return "Pass", [], []


# ---------------------------------------------------------------------------
# Step 9: the testing-table row and remarks
# ---------------------------------------------------------------------------

def _cell(frs, requirement=None, labels=None) -> str:
    parts = []
    for i, fr in enumerate(frs):
        shown = []
        for v in fr.values:
            value = v.value.strip()
            if value:
                shown.append(value + (" (inferred)" if v.explicit_or_inferred == "Inferred" else ""))
        shown = _distinct(shown)
        if not shown:
            continue
        if fr.status == "Conflicting Evidence" and len(shown) > 1:
            text = " | ".join(shown) + " (conflicting)"
        else:
            text = "; ".join(shown)  # several people for one step are simply listed
        parts.append(f"{labels[i]}: {text}" if labels else text)
    if parts:
        return "; ".join(parts)
    if any(fr.status == "Unable to Determine" for fr in frs):
        return "Unable to Determine"
    if requirement == wf.OPTIONAL:
        return "Not evidenced (optional)"
    if requirement == wf.NOT_STANDARD:
        return "N/A"
    return "Missing"


def exception_types(findings: list[Finding]) -> list[str]:
    """Your exception types present in these findings, in a fixed order. Empty = No Exception."""
    present = {wf.EXCEPTION_TYPE_BY_KIND.get(f.kind) for f in findings}
    return [t for t in wf.EXCEPTION_ORDER if t in present]


def _remarks(findings: list[Finding]) -> str:
    types = exception_types(findings)
    header = "Exception type: " + ("; ".join(types) if types else wf.NO_EXCEPTION)
    if not findings:
        return header + ". All applicable requirements are satisfied based on the extracted evidence."
    lines = [header]
    for i, f in enumerate(findings, 1):
        label = wf.EXCEPTION_TYPE_BY_KIND.get(f.kind) or "Observation"
        text = f"{i}. [{label}] {f.message}"
        if f.sources:
            text += f" (Evidence: {', '.join(f.sources)})"
        lines.append(text)
    return "\n".join(lines)


def build_row(record, category, findings, sod_result, sod_details, folder="-") -> dict:
    row = {
        "Change ID": _cell([record.change_id]),
        "Release / Version ID": _cell([record.release_version_id]),
        "Description": _cell([record.description]),
        "Priority": _cell([record.priority]),
        "Risk": _cell([record.risk]),
        "Category": category or _cell([record.category]),
    }
    for key, (by_col, on_col) in STEP_COLUMNS.items():
        requirement = wf.WORKFLOWS[category][key] if category else None
        pairs = wf.STEPS[key]["pairs"]
        labels = ["QA", "UAT"] if len(pairs) > 1 else None
        row[by_col] = _cell([getattr(record, b) for b, _ in pairs], requirement, labels)
        row[on_col] = _cell([getattr(record, o) for _, o in pairs], requirement, labels)
    row["SoD Result"] = sod_result + (": " + "; ".join(sod_details) if sod_details else "")
    row["Exception / Observation"] = _remarks(findings)
    row["Evidence folder"] = folder
    return {col: row[col] for col in COLUMNS}


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------

def _evidence_folders(record: ChangeRecord) -> Counter:
    """Count the evidence files per top-level folder (file labels look like 'CHG-10482/ticket.png')."""
    files = {}
    for name in ChangeRecord.model_fields:
        for v in getattr(record, name).values:
            if "/" in v.source_filename:
                files[v.source_filename] = v.source_filename.split("/")[0]
    return Counter(files.values())


def _drop_incomplete_steps(record: ChangeRecord) -> ChangeRecord:
    """Treat a half-filled step listed in workflows.DROP_IF_INCOMPLETE as not evidenced (name AND date cleared)."""
    updates = {}
    for key in wf.DROP_IF_INCOMPLETE:
        if step_status(record, key) == "Incomplete":
            for by_name, on_name in wf.STEPS[key]["pairs"]:
                updates[by_name] = FieldResult(values=[], status="Missing")
                updates[on_name] = FieldResult(values=[], status="Missing")
    return record.model_copy(update=updates) if updates else record


def evaluate_change(record: ChangeRecord) -> ChangeEvaluation:
    record = _drop_incomplete_steps(record)
    findings = []
    category, category_finding = determine_category(record)
    if category_finding:
        findings.append(category_finding)

    if category:
        findings += check_controls(record, category)
        findings += check_sequence(record, category)
        sod_result, sod_details, sod_findings = evaluate_sod(record, category)
        findings += sod_findings
    else:
        sod_result, sod_details = "Not evaluated (category unresolved)", []
    findings += check_conflicts(record)

    folders = _evidence_folders(record)
    folder = folders.most_common(1)[0][0] if folders else "-"
    if len(folders) > 1:
        listed = ", ".join(f"{name} ({count} file{'s' if count > 1 else ''})" for name, count in folders.most_common())
        findings.append(Finding("observation", "folders",
            f"Evidence for this change came from several folders: {listed}. "
            f"Check that none of it belongs to another change request.", []))

    ids = _values(record.change_id)
    return ChangeEvaluation(
        change_id=ids[0] if ids else "UNKNOWN",
        category=category,
        findings=findings,
        exception_types=exception_types(findings),
        sod_result=sod_result,
        sod_details=sod_details,
        row=build_row(record, category, findings, sod_result, sod_details, folder),
        record=record,
        folder=folder,
    )


def evaluate_extraction(result: ExtractionResult) -> list[ChangeEvaluation]:
    evaluations = [evaluate_change(change) for change in result.changes]

    # One folder should hold ONE change request. If the model read the files of one folder as
    # several change requests, say so on each of them.
    by_folder = {}
    for ev in evaluations:
        if ev.folder != "-":
            by_folder.setdefault(ev.folder, []).append(ev)
    for folder, group in by_folder.items():
        if len(group) > 1:
            for ev in group:
                others = ", ".join(o.change_id for o in group if o is not ev)
                ev.findings.append(Finding("observation", "folders",
                    f"Folder {folder} also contains evidence read as another change request ({others}). "
                    f"Check that the folder holds a single change.", []))
                ev.rebuild()
    return evaluations


def save_testing_table(evaluations: list[ChangeEvaluation], path: str = "audit_testing_table.xlsx") -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Testing table"
    ws.append(COLUMNS)
    for ev in evaluations:
        ws.append([ev.row[col] for col in COLUMNS])

    wide = {"Description": 40, "SoD Result": 40, "Exception / Observation": 80}
    for idx, col in enumerate(COLUMNS, start=1):
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = wide.get(col, 22)
    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "B2"
    wb.save(path)
    return path


def audit_from_json(json_path: str, xlsx_path: str = "audit_testing_table.xlsx") -> list[ChangeEvaluation]:
    """Run steps 5-9 on a saved extraction result. Makes no Gemini calls."""
    with open(json_path) as f:
        result = ExtractionResult.model_validate_json(f.read())
    evaluations = evaluate_extraction(result)
    save_testing_table(evaluations, xlsx_path)
    return evaluations
