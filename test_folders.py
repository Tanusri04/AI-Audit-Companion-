"""Tests for folder grouping, batching and saved progress. No Gemini needed.   Run: python test_folders.py"""

import json
import os
import sys
import tempfile
import types as T
import zipfile
from unittest import mock

# --- stand-in for the Gemini library, so this runs anywhere ---
_g = T.ModuleType("google"); _ge = T.ModuleType("google.genai"); _gt = T.ModuleType("google.genai.types")
_ger = T.ModuleType("google.genai.errors")


class APIError(Exception):
    def __init__(self, code, msg=""):
        super().__init__(msg)
        self.code = code


_ger.APIError = APIError
_gt.Part = type("P", (), {"from_bytes": staticmethod(lambda data, mime_type: ("PART", mime_type))})
_gt.GenerateContentConfig = lambda **k: k
_ge.Client = mock.Mock(); _ge.types = _gt; _ge.errors = _ger; _g.genai = _ge
sys.modules.update({"google": _g, "google.genai": _ge, "google.genai.types": _gt, "google.genai.errors": _ger})

import audit_extractor as ae  # noqa: E402
from rules_engine import evaluate_extraction  # noqa: E402
from schema import ExtractionResult  # noqa: E402
from zip_to_llm import extract_and_group  # noqa: E402

gen = _ge.Client.return_value.models.generate_content


def make_zip(path, names):
    with zipfile.ZipFile(path, "w") as zf:
        for n in names:
            zf.writestr(n, b"data-" + n.encode())


def groups_of(zip_path, **kw):
    with extract_and_group(zip_path, **kw) as groups:
        return {g.name: sorted(g.label(p) for p in g.images + g.pdfs) for g in groups}


# ---------------------------------------------------------------- grouping

def test_grouping(tmp):
    z = tmp / "a.zip"
    make_zip(z, ["Zipfolder-test/CHG-1/ticket.png", "Zipfolder-test/CHG-1/qa.png",
                 "Zipfolder-test/CHG-2/ticket.png", "Zipfolder-test/CHG-2/ev/cab.pdf",
                 "Zipfolder-test/CHG-2/notes.txt", "__MACOSX/Zipfolder-test/._x.png", "Zipfolder-test/.DS_Store"])
    assert groups_of(z) == {
        "CHG-1": ["CHG-1/qa.png", "CHG-1/ticket.png"],
        "CHG-2": ["CHG-2/ev/cab.pdf", "CHG-2/ticket.png"],     # wrapper skipped, subfolder kept, txt/junk ignored
    }, groups_of(z)

    make_zip(z, ["CHG-1/a.png", "CHG-2/b.png"])                 # no wrapper
    assert groups_of(z) == {"CHG-1": ["CHG-1/a.png"], "CHG-2": ["CHG-2/b.png"]}

    make_zip(z, ["CHG-1/a.png", "CHG-1/b.pdf"])                 # ONE change folder is not a wrapper
    assert groups_of(z) == {"CHG-1": ["CHG-1/a.pdf".replace("a.pdf", "a.png"), "CHG-1/b.pdf"]}

    make_zip(z, ["a.png", "b.pdf"])                             # no folders at all
    assert groups_of(z) == {"(root)": ["a.png", "b.pdf"]}

    make_zip(z, ["loose.png", "CHG-1/a.png"])                   # mixed
    assert groups_of(z) == {"(root)": ["loose.png"], "CHG-1": ["CHG-1/a.png"]}

    make_zip(z, ["CHG-1/ticket.png", "CHG-2/ticket.png"])       # same filename in two folders
    assert groups_of(z) == {"CHG-1": ["CHG-1/ticket.png"], "CHG-2": ["CHG-2/ticket.png"]}

    make_zip(z, ["Wrapper/CHG-1/Evidence/x.png", "Wrapper/CHG-2/Evidence/y.png"])
    assert groups_of(z) == {"CHG-1": ["CHG-1/Evidence/x.png"], "CHG-2": ["CHG-2/Evidence/y.png"]}
    print("PASS: grouping (wrapper folder, subfolders, single folder, no folders, same filenames)")


# ---------------------------------------------------------------- fake Gemini

class FakeGemini:
    """Answers each request with a change per top-level folder it was sent."""

    def __init__(self, fail_on_request=None, shared_folder_split=False):
        self.requests = []
        self.fail_on_request = fail_on_request
        self.shared_folder_split = shared_folder_split

    def __call__(self, model, contents, config):
        labels = [c.replace("Evidence file: ", "") for c in contents if isinstance(c, str) and c.startswith("Evidence file: ")]
        self.requests.append({"labels": labels, "last_text": contents[-1]})
        if self.fail_on_request == len(self.requests):
            raise APIError(400, "bad")
        entries = []
        folders = list(dict.fromkeys(l.split("/")[0] for l in labels))
        for folder in folders:
            files = [l for l in labels if l.split("/")[0] == folder]
            ids = [folder, folder + "-B"] if self.shared_folder_split else [folder]
            for cid in ids:
                entries.append({"change_id": cid, "field": "Change ID", "status": "Available", "value": cid,
                                "source_filename": files[0], "explicit_or_inferred": "Explicit", "note": "", "explanation": ""})
        reply = mock.Mock()
        reply.text = json.dumps({"files_reviewed": labels, "entries": entries})
        return reply


def run_extract(zip_path, fake, **kw):
    gen.side_effect = fake
    with mock.patch.object(ae.time, "sleep") as sleep:
        result = ae.extract_audit_fields(str(zip_path), **kw)
    return result, sleep.call_count


def test_batching(tmp):
    z = tmp / "seven.zip"
    make_zip(z, [f"Z/CHG-{i}/ticket.png" for i in range(1, 8)] + ["Z/CHG-1/extra/x.png"])
    cp = str(tmp / "progress.json")
    fake = FakeGemini()
    result, sleeps = run_extract(z, fake, folders_per_request=3, checkpoint_path=cp)
    assert [len(r["labels"]) for r in fake.requests] == [4, 3, 1], [r["labels"] for r in fake.requests]   # 3+3+1 folders
    assert sleeps == 2, "waits between requests only"
    assert sorted(c.change_id.values[0].value for c in result.changes) == [f"CHG-{i}" for i in range(1, 8)]
    assert all("/" in l for r in fake.requests for l in r["labels"]), "labels carry folder paths"
    assert "EVIDENCE FOLDERS" in fake.requests[0]["last_text"] and "OUTPUT FORMAT" in fake.requests[0]["last_text"]
    assert len(ae.raw_responses) == 3
    print("PASS: 7 folders / 3 per request = 3 requests, labelled with folder paths")


def test_resume(tmp):
    z = tmp / "resume.zip"
    make_zip(z, [f"Z/CHG-{i}/ticket.png" for i in range(1, 8)])
    cp = str(tmp / "progress2.json")

    fake = FakeGemini(fail_on_request=2)                        # first request works, second fails
    try:
        run_extract(z, fake, folders_per_request=3, checkpoint_path=cp)
        raise AssertionError("expected an error")
    except APIError:
        pass
    saved = json.load(open(cp))
    assert saved["done_folders"] == ["CHG-1", "CHG-2", "CHG-3"], saved["done_folders"]

    fake2 = FakeGemini()                                        # run again: only the rest is sent
    result, _ = run_extract(z, fake2, folders_per_request=3, checkpoint_path=cp)
    sent = [l.split("/")[0] for r in fake2.requests for l in r["labels"]]
    assert sent == ["CHG-4", "CHG-5", "CHG-6", "CHG-7"], sent
    assert len(result.changes) == 7

    fake3 = FakeGemini()                                        # everything done: no requests at all
    result, _ = run_extract(z, fake3, folders_per_request=3, checkpoint_path=cp)
    assert fake3.requests == [] and len(result.changes) == 7

    fake4 = FakeGemini()                                        # resume=False starts again
    run_extract(z, fake4, folders_per_request=3, checkpoint_path=cp, resume=False)
    assert len(fake4.requests) == 3

    other = tmp / "other.zip"                                   # a different zip must not reuse the progress
    make_zip(other, [f"Z/CHG-{i}/ticket.png" for i in range(1, 4)])
    fake5 = FakeGemini()
    run_extract(other, fake5, folders_per_request=3, checkpoint_path=cp)
    assert len(fake5.requests) == 1
    print("PASS: a failed run continues where it stopped; a different zip or resume=False starts fresh")


def test_folder_column_and_checks(tmp):
    z = tmp / "f.zip"
    make_zip(z, ["Z/CHG-1/ticket.png", "Z/CHG-2/ticket.png"])
    result, _ = run_extract(z, FakeGemini(), folders_per_request=5, checkpoint_path=str(tmp / "p3.json"))
    evs = evaluate_extraction(result)
    assert [e.row["Evidence folder"] for e in evs] == ["CHG-1", "CHG-2"]
    assert all("several folders" not in e.row["Exception / Observation"] for e in evs)

    # one folder read as TWO change requests -> an observation on both (not an exception)
    z2 = tmp / "g.zip"
    make_zip(z2, ["Z/CHG-1/ticket.png", "Z/CHG-9/ticket.png"])
    result, _ = run_extract(z2, FakeGemini(shared_folder_split=True), folders_per_request=5, checkpoint_path=str(tmp / "p4.json"))
    evs = evaluate_extraction(result)
    shared = [e for e in evs if e.folder == "CHG-1"]
    assert len(shared) == 2 and all("also contains evidence read as another change" in e.row["Exception / Observation"] for e in shared)

    # one change built from two folders -> an observation
    from schema import ChangeRecord, FieldResult, SourcedValue
    def f(v, src):
        return FieldResult(values=[SourcedValue(value=v, source_filename=src, explicit_or_inferred="Explicit")], status="Available")
    data = {n: FieldResult(values=[], status="Missing") for n in ChangeRecord.model_fields}
    data.update(change_id=f("CHG-1", "A/t.png"), risk=f("High", "B/r.png"), priority=f("High", "A/p.png"))
    evs = evaluate_extraction(ExtractionResult(files_reviewed=[], changes=[ChangeRecord(**data)]))
    assert "several folders: A (2 files), B (1 file)" in evs[0].row["Exception / Observation"]
    assert evs[0].row["Evidence folder"] == "A"
    print("PASS: Evidence folder column; shared-folder and mixed-folder observations")


if __name__ == "__main__":
    from pathlib import Path
    with tempfile.TemporaryDirectory() as t:
        for fn in (test_grouping, test_batching, test_resume, test_folder_column_and_checks):
            fn(Path(t))
    print("\nAll tests passed.")
