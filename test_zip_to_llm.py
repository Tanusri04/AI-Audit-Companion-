"""Simple tests for zip_to_llm.py. Run with:  python test_zip_to_llm.py"""

import tempfile
import zipfile
from pathlib import Path

from zip_to_llm import process_zip, extract_and_segregate


def make_zip(zip_path, files):
    """Create a zip at zip_path. `files` is {name_inside_zip: bytes}."""
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)


def test_sorting_and_cleanup(tmp):
    zip_path = tmp / "sample.zip"
    make_zip(zip_path, {
        "a.jpg": b"fake jpg",
        "sub/b.PNG": b"fake png",          # uppercase + subfolder
        "c.jpeg": b"fake jpeg",
        "docs/report.pdf": b"fake pdf",
        "notes.txt": b"ignore me",
        "anim.gif": b"gif not allowed",
        "photo.webp": b"webp not allowed",
        "__MACOSX/._a.jpg": b"mac junk",
        ".DS_Store": b"mac junk",
    })

    seen = {}

    def handler(images, pdfs):
        seen["images"] = sorted(p.name for p in images)
        seen["pdfs"] = sorted(p.name for p in pdfs)
        seen["folder"] = images[0].parent
        assert images[0].exists()          # files exist inside the handler
        return "ok"

    assert process_zip(zip_path, handler) == "ok"
    assert seen["images"] == ["a.jpg", "b.PNG", "c.jpeg"], seen["images"]
    assert seen["pdfs"] == ["report.pdf"], seen["pdfs"]
    assert not seen["folder"].exists()     # temp folder deleted afterwards
    print("PASS: sorting + cleanup")


def test_zip_slip_blocked(tmp):
    zip_path = tmp / "evil.zip"
    make_zip(zip_path, {"../evil.png": b"x"})
    try:
        with extract_and_segregate(zip_path):
            pass
    except ValueError:
        print("PASS: zip-slip blocked")
        return
    raise AssertionError("zip-slip was NOT blocked")


def test_no_matching_files(tmp):
    zip_path = tmp / "empty.zip"
    make_zip(zip_path, {"notes.txt": b"hello"})
    try:
        process_zip(zip_path, lambda i, p: None)
    except ValueError:
        print("PASS: zip with no images/PDFs raises an error")
        return
    raise AssertionError("expected ValueError")


def test_bad_zip_file(tmp):
    bad = tmp / "notazip.zip"
    bad.write_bytes(b"this is not a zip")
    try:
        process_zip(bad, lambda i, p: None)
    except zipfile.BadZipFile:
        print("PASS: corrupt zip raises BadZipFile")
        return
    raise AssertionError("expected BadZipFile")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        test_sorting_and_cleanup(tmp)
        test_zip_slip_blocked(tmp)
        test_no_matching_files(tmp)
        test_bad_zip_file(tmp)
    print("\nAll tests passed.")
