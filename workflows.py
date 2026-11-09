"""Your audit rules, written as data.

When a rule changes, edit THIS file. No other code needs to change.
Anything marked ASSUMPTION is my guess, not something you told me. Please check it.
"""

# ---------------------------------------------------------------------------
# What each workflow step requires (from your workflow table)
# ---------------------------------------------------------------------------
REQUIRED = "Required"
QA_OR_UAT = "Required: QA or UAT"   # at least one of QA or UAT must be evidenced
OPTIONAL = "Optional"
NOT_STANDARD = "Not the standard path"

# Each step lists the (who, when) field pairs in the extraction result that evidence it.
# QA/UAT has two pairs because QA and UAT are extracted separately.
STEPS = {
    "initiation": {
        "label": "Change initiation",
        "pairs": [("change_initiated_by", "change_initiated_on")],
    },
    "pre_authorization": {
        "label": "Pre-authorization before development",
        "pairs": [("pre_authorization_approval_by", "pre_authorization_approval_on")],
    },
    "development": {
        "label": "Development",
        "pairs": [("change_developed_by", "change_developed_on")],
    },
    "peer_review": {
        "label": "Peer review",
        "pairs": [("peer_review_by", "peer_review_on")],
    },
    "qa_uat": {
        "label": "QA/UAT sign-off",
        "pairs": [("qa_signoff_by", "qa_signoff_on"), ("uat_signoff_by", "uat_signoff_on")],
    },
    "cab": {
        "label": "CAB / pre-deployment approval",
        "pairs": [("pre_deployment_cab_approval_by", "pre_deployment_cab_approval_on")],
    },
    "ecab": {
        "label": "E-CAB approval",
        "pairs": [("ecab_approval_by", "ecab_approval_on")],
    },
    "deployment": {
        "label": "Deployment",
        "pairs": [("deployment_by", "deployment_on")],
    },
    "pir": {
        "label": "Post-implementation review (PIR)",
        "pairs": [("pir_by", "pir_on")],
    },
}

WORKFLOWS = {
    "Normal": {
        "initiation": REQUIRED,
        "pre_authorization": OPTIONAL,
        "development": REQUIRED,
        "peer_review": OPTIONAL,
        "qa_uat": QA_OR_UAT,
        "cab": REQUIRED,
        "ecab": NOT_STANDARD,
        "deployment": REQUIRED,
        "pir": OPTIONAL,
    },
    "Standard": {
        "initiation": REQUIRED,
        "pre_authorization": OPTIONAL,
        "development": REQUIRED,
        "peer_review": OPTIONAL,
        "qa_uat": OPTIONAL,
        "cab": OPTIONAL,
        "ecab": NOT_STANDARD,
        "deployment": REQUIRED,
        "pir": OPTIONAL,
    },
    "Emergency": {
        "initiation": REQUIRED,
        "pre_authorization": OPTIONAL,
        "development": REQUIRED,
        "peer_review": OPTIONAL,
        "qa_uat": OPTIONAL,
        "cab": OPTIONAL,
        "ecab": REQUIRED,
        "deployment": REQUIRED,
        "pir": OPTIONAL,
    },
}

# ---------------------------------------------------------------------------
# Sequence rules: (earlier step, later step). The earlier step must not be dated after
# the later one. Same day is accepted unless both dates include a time.
# ---------------------------------------------------------------------------
# Steps must happen in exactly the order of your workflow table. Each step present in the
# evidence is compared with the previous step that has a date (optional steps that were skipped
# are simply passed over), so one out-of-order step gives one finding.
_BASE = ["initiation", "pre_authorization", "development", "peer_review", "qa_uat", "cab"]
WORKFLOW_ORDER = {
    "Normal": _BASE + ["deployment", "pir"],
    "Standard": _BASE + ["deployment", "pir"],
    "Emergency": _BASE + ["ecab", "deployment", "pir"],
}

# ---------------------------------------------------------------------------
# Segregation of duties: these roles must be different people (from your SoD rule).
# The approver is the CAB approver for Normal/Standard and the E-CAB approver for Emergency.
# ---------------------------------------------------------------------------
SOD_ROLES = {
    "Developer": {"step": "development"},
    "Tester": {"step": "qa_uat"},
    "Applicable CAB/E-CAB approver": {"step_by_category": {"Normal": "cab", "Standard": "cab", "Emergency": "ecab"}},
    "Deployer": {"step": "deployment"},
}

# ---------------------------------------------------------------------------
# Your exception types
# ---------------------------------------------------------------------------
CONTROL = "Control Exception"
UNREADABLE = "Blurry / Unreadable Evidence (Unable to Determine)"
WORKFLOW = "Workflow Not Followed"
SOD = "Segregation of Duties (SoD)"
CONFLICT = "Discrepancy / Conflicting Evidence"
NO_EXCEPTION = "No Exception"

# How the engine's internal finding kinds map to your exception types.
# None = an observation only (noted in the remarks, but NOT an exception).
EXCEPTION_TYPE_BY_KIND = {
    "missing_control": CONTROL,        # required evidence is missing
    "incomplete_evidence": CONTROL,    # e.g. a name but no date for a required step
    "unable_to_determine": UNREADABLE,
    "sequence": WORKFLOW,              # a step performed out of sequence
    "sod_violation": SOD,
    "conflicting_evidence": CONFLICT,
    "observation": None,
}

# The order in which types are listed when a change has several.
EXCEPTION_ORDER = [CONTROL, UNREADABLE, WORKFLOW, SOD, CONFLICT]


# ---------------------------------------------------------------------------
# Steps where a half-filled entry (a name with no date, or a date with no name) is treated as "not evidenced".
# A real post-implementation review always has both, and a lone date is usually picked up from another step's
# page (for example an E-CAB approval time). Remove "pir" from this set if you want such entries reported.
# ---------------------------------------------------------------------------
DROP_IF_INCOMPLETE = {"pir"}
