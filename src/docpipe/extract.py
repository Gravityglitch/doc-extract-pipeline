"""Text extraction from inbox files: PDF (pypdf), .eml, .txt."""

from __future__ import annotations

import email
import hashlib
from dataclasses import dataclass
from email import policy
from pathlib import Path

from pypdf import PdfReader

SUPPORTED_SUFFIXES = {".pdf", ".eml", ".txt"}


@dataclass(frozen=True)
class SourceDocument:
    path: Path
    sha256: str
    text: str
    pdf_bytes: bytes | None  # kept so a vision fallback can send the PDF to Claude

    @property
    def text_is_empty(self) -> bool:
        return not self.text.strip()


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_pdf_text(data: bytes, path: Path) -> str:
    reader = PdfReader(path)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def extract_eml_text(data: bytes) -> str:
    msg = email.message_from_bytes(data, policy=policy.default)
    header = "\n".join(
        f"{k}: {msg[k]}" for k in ("From", "To", "Date", "Subject") if msg[k] is not None
    )
    body = msg.get_body(preferencelist=("plain", "html"))
    body_text = body.get_content() if body is not None else ""
    return f"{header}\n\n{body_text}"


def load_document(path: Path) -> SourceDocument:
    data = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return SourceDocument(path, file_sha256(data), extract_pdf_text(data, path), data)
    if suffix == ".eml":
        return SourceDocument(path, file_sha256(data), extract_eml_text(data), None)
    if suffix == ".txt":
        return SourceDocument(path, file_sha256(data), data.decode("utf-8", "replace"), None)
    raise ValueError(f"Unsupported file type: {path.name}")


def list_inbox(inbox: Path) -> list[Path]:
    return sorted(p for p in inbox.iterdir() if p.suffix.lower() in SUPPORTED_SUFFIXES)
