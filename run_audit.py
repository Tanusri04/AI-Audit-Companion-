"""Run the whole audit on a zip.

    from run_audit import run_audit, rerun_from_saved
    run_audit("Zipfolder-test.zip")       # one zip, one folder per change request; up to 5 folders per Gemini request
    run_audit("x.zip", fresh=True)        # ignore saved progress (extraction_progress.json) and extract again
    rerun_from_saved()                    # changed a rule? re-run WITHOUT using any Gemini quota
"""

from audit_extractor import extract_audit_fields
from rules_engine import audit_from_json

JSON_PATH = "extraction_output.json"
XLSX_PATH = "audit_testing_table.xlsx"


def _print_summary(evaluations):
    for ev in evaluations:
        types = "; ".join(ev.exception_types) or "No Exception"
        print(f"{ev.change_id:12} category={ev.category or 'UNRESOLVED':10} "
              f"SoD={ev.sod_result.split(':')[0]:22} {types}")


def run_audit(zip_path: str, json_path: str = JSON_PATH, xlsx_path: str = XLSX_PATH,
              folders_per_request: int = 5, fresh: bool = False):
    # One Gemini request per `folders_per_request` folders. fresh=True ignores saved progress.
    result = extract_audit_fields(zip_path, folders_per_request=folders_per_request, resume=not fresh)
    with open(json_path, "w") as f:
        f.write(result.model_dump_json(indent=2))           # saved so we never pay for it twice
    import audit_extractor
    with open("gemini_raw_response.json", "w") as f:        # Gemini's untouched replies, for checking
        f.write("\n".join(audit_extractor.raw_responses))
    evaluations = audit_from_json(json_path, xlsx_path)
    _print_summary(evaluations)
    print(f"\nExtraction saved to {json_path}\nTesting table saved to {xlsx_path}")
    return evaluations


def rerun_from_saved(json_path: str = JSON_PATH, xlsx_path: str = XLSX_PATH):
    evaluations = audit_from_json(json_path, xlsx_path)
    _print_summary(evaluations)
    print(f"\nTesting table saved to {xlsx_path}")
    return evaluations
