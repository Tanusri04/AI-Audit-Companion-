"""Extract a zip into a temporary folder and segregate images (.jpg/.jpeg/.png)
and PDFs. Provider-agnostic: you pass in your own function that sends the
files to whichever LLM you use.

The temp folder is deleted automatically when the `with` block exits,
so anything that needs the files must run inside the block.
"""

import base64
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
PDF_EXTS = {".pdf"}

MAX_TOTAL_UNCOMPRESSED = 500 * 1024 * 1024  # 500 MB guard against zip bombs

T = TypeVar("T")


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract while blocking path traversal ("zip slip") and oversized archives."""
    dest = dest.resolve()
    if sum(i.file_size for i in zf.infolist()) > MAX_TOTAL_UNCOMPRESSED:
        raise ValueError("Zip too large when uncompressed")
    for info in zf.infolist():
        target = (dest / info.filename).resolve()
        if not target.is_relative_to(dest):
            raise ValueError(f"Unsafe path in zip: {info.filename}")
    zf.extractall(dest)


@contextmanager
def extract_and_segregate(zip_path: str | Path):
    """Extract `zip_path` into a temp dir and yield (images, pdfs) as lists of Paths.

    Files are removed when the context exits.
    """
    with tempfile.TemporaryDirectory(prefix="zip_extract_") as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(zip_path) as zf:
            _safe_extract(zf, tmp_path)

        images, pdfs = [], []
        for p in sorted(tmp_path.rglob("*")):
            # skip folders and macOS junk
            if not p.is_file() or "__MACOSX" in p.parts or p.name.startswith("."):
                continue
            ext = p.suffix.lower()
            if ext in IMAGE_EXTS:
                images.append(p)
            elif ext in PDF_EXTS:
                pdfs.append(p)
        yield images, pdfs


def process_zip(
    zip_path: str | Path,
    handler: Callable[[list[Path], list[Path]], T],
) -> T:
    """Extract, segregate, and call `handler(images, pdfs)` while files still exist.

    Whatever `handler` returns (e.g. the LLM's answer) is returned to you.
    """
    with extract_and_segregate(zip_path) as (images, pdfs):
        if not images and not pdfs:
            raise ValueError("No images or PDFs found in the zip")
        return handler(images, pdfs)


# ---------- one group of evidence per folder (one folder = one change request) ----------

@dataclass
class EvidenceGroup:
    name: str            # the folder name, or "(root)" for files not inside any folder
    images: list
    pdfs: list
    root: Path           # where the zip was unpacked
    prefix: str = ""     # a wrapper folder around everything, removed from labels

    def label(self, path: Path) -> str:
        """The file's path inside the zip, e.g. 'CHG-10482/ticket.png' (without any wrapper folder)."""
        rel = path.relative_to(self.root).as_posix()
        return rel[len(self.prefix):] if self.prefix and rel.startswith(self.prefix) else rel


@contextmanager
def extract_and_group(zip_path: str | Path, depth: int = 1):
    """Extract `zip_path` into a temp dir and yield a list of EvidenceGroup, one per folder.

    depth=1 means the first folder level is the group, e.g. CHG-10482/anything/file.png belongs to
    the group "CHG-10482". If everything is inside ONE wrapper folder (what you get when you zip a
    folder), that wrapper is skipped. Files are removed when the context exits.
    """
    with tempfile.TemporaryDirectory(prefix="zip_extract_") as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(zip_path) as zf:
            _safe_extract(zf, tmp_path)

        files = []
        for p in sorted(tmp_path.rglob("*")):
            if not p.is_file() or "__MACOSX" in p.parts or p.name.startswith("."):
                continue
            if p.suffix.lower() in IMAGE_EXTS | PDF_EXTS:
                files.append((p, p.relative_to(tmp_path).parts))

        # One folder around everything is a wrapper only if there are still folders inside it.
        # (A zip holding just "CHG-1/ticket.png" is a single change folder, not a wrapper.)
        prefix = ""
        if (files and all(len(parts) >= 2 for _, parts in files)
                and len({parts[0] for _, parts in files}) == 1
                and any(len(parts) >= 3 for _, parts in files)):
            prefix = files[0][1][0] + "/"

        groups = {}
        for p, parts in files:
            inside = parts[1:] if prefix else parts
            name = "/".join(inside[:-1][:depth]) or "(root)"
            group = groups.setdefault(name, EvidenceGroup(name, [], [], tmp_path, prefix))
            (group.images if p.suffix.lower() in IMAGE_EXTS else group.pdfs).append(p)
        yield list(groups.values())


def process_zip_grouped(zip_path: str | Path, handler: Callable[[list], T], depth: int = 1) -> T:
    """Extract, group by folder, and call `handler(groups)` while the files still exist."""
    with extract_and_group(zip_path, depth) as groups:
        if not groups:
            raise ValueError("No images or PDFs found in the zip")
        return handler(groups)


# ---------- optional helpers for building LLM payloads ----------

def to_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def image_data_url(path: Path) -> str:
    """e.g. 'data:image/png;base64,...' (the format OpenAI-style APIs accept)."""
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{to_base64(path)}"


# ---------- example usage ----------

def my_llm_handler(images: list[Path], pdfs: list[Path]) -> str:
    # Replace this with your own LLM call. For example:
    #   - read bytes:       path.read_bytes()
    #   - base64 string:    to_base64(path)
    #   - data URL (image): image_data_url(path)
    #   - PDF -> text:      extract with pypdf / pdfplumber, then send as text
    #   - PDF -> images:    render pages with pdf2image / PyMuPDF
    print(f"{len(images)} images, {len(pdfs)} PDFs")
    return "..."


if __name__ == "__main__":
    print(process_zip("documents.zip", my_llm_handler))
