"""IMAP poller: pull mail from a mailbox/label, save it into the inbox folder, move it
to a 'done' folder. Built on `imaplib` + the `email` stdlib package only.

What gets written to the inbox folder (and therefore processed by `load_document`):
  * every supported attachment (.pdf/.eml/.txt) as `<stem>__<attachment name>`;
  * the message itself as `<stem>.eml` when it has no supported attachment, because then
    the invoice (if any) is in the body. When attachments exist the body is usually just
    "please find attached", and saving it too would create a near-duplicate row.
The raw message is always archived to `archive_dir` for audit regardless.

The poller talks to anything that implements `IMAPConnection` - `imaplib.IMAP4_SSL`
does, and `docpipe.ingest.fake_imap.FakeIMAPServer` does for tests.
"""

from __future__ import annotations

import email
import hashlib
import imaplib
import re
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Protocol

from docpipe.config import Settings
from docpipe.extract import SUPPORTED_SUFFIXES
from docpipe.logging_setup import get_logger

log = get_logger("docpipe.imap")

IMAPResponse = tuple[str, list[Any]]


class IMAPConnection(Protocol):
    """The subset of `imaplib.IMAP4` the poller uses."""

    def select(self, mailbox: str = "INBOX", readonly: bool = False) -> IMAPResponse: ...
    def search(self, charset: str | None, *criteria: str) -> IMAPResponse: ...
    def fetch(self, message_set: str, message_parts: str) -> IMAPResponse: ...
    def copy(self, message_set: str, new_mailbox: str) -> IMAPResponse: ...
    def store(self, message_set: str, command: str, flags: str) -> IMAPResponse: ...
    def create(self, mailbox: str) -> IMAPResponse: ...
    def expunge(self) -> IMAPResponse: ...
    def logout(self) -> IMAPResponse: ...


@dataclass
class PollResult:
    fetched: int = 0
    moved: int = 0
    saved: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _slug(text: str, limit: int = 40) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return cleaned[:limit] or "mail"


def message_stem(msg: EmailMessage, raw: bytes) -> str:
    """Stable, filesystem-safe name: short hash of the raw bytes + subject slug."""
    digest = hashlib.sha256(raw).hexdigest()[:10]
    return f"{digest}_{_slug(msg.get('Subject', ''))}"


def save_message(msg: EmailMessage, raw: bytes, inbox_dir: Path, archive_dir: Path) -> list[Path]:
    inbox_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    stem = message_stem(msg, raw)
    (archive_dir / f"{stem}.eml").write_bytes(raw)

    saved: list[Path] = []
    for part in msg.iter_attachments():
        name = part.get_filename() or ""
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        target = inbox_dir / f"{stem}__{_slug(Path(name).stem)}{suffix}"
        target.write_bytes(payload)
        saved.append(target)
    if not saved:
        target = inbox_dir / f"{stem}.eml"
        target.write_bytes(raw)
        saved.append(target)
    return saved


def _ok(resp: IMAPResponse, what: str) -> list[Any]:
    status, data = resp
    if status != "OK":
        raise RuntimeError(f"IMAP {what} failed: {status} {data!r}")
    return data


def _raw_from_fetch(data: list[Any]) -> bytes:
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes | bytearray):
            return bytes(item[1])
    raise RuntimeError("IMAP fetch returned no message body")


def poll_mailbox(
    conn: IMAPConnection,
    inbox_dir: Path,
    archive_dir: Path,
    mailbox: str = "INBOX",
    done_mailbox: str = "AP/Processed",
) -> PollResult:
    """Fetch every message in `mailbox`, save it, move it to `done_mailbox`.

    A message that cannot be saved is left in place (not moved) and reported in
    `errors`, so it is retried next poll and never silently lost."""
    result = PollResult()
    conn.create(done_mailbox)  # idempotent on real servers ("already exists" is fine)
    _ok(conn.select(mailbox), f"select {mailbox}")
    data = _ok(conn.search(None, "ALL"), "search")
    ids = data[0].split() if data and data[0] else []
    log.info("imap.poll", mailbox=mailbox, messages=len(ids))
    for num in ids:
        num_s = num.decode() if isinstance(num, bytes) else str(num)
        try:
            raw = _raw_from_fetch(_ok(conn.fetch(num_s, "(RFC822)"), f"fetch {num_s}"))
            msg = email.message_from_bytes(raw, policy=policy.default)
            assert isinstance(msg, EmailMessage)
            result.fetched += 1
            saved = save_message(msg, raw, inbox_dir, archive_dir)
            result.saved.extend(saved)
            _ok(conn.copy(num_s, done_mailbox), f"copy {num_s}")
            _ok(conn.store(num_s, "+FLAGS", "\\Deleted"), f"flag {num_s}")
            result.moved += 1
            log.info("imap.saved", subject=msg.get("Subject", ""), files=[p.name for p in saved])
        except Exception as exc:
            log.error("imap.message_failed", message=num_s, error=str(exc))
            result.errors.append(f"{num_s}: {exc}")
    conn.expunge()
    return result


def open_connection(settings: Settings) -> IMAPConnection:
    assert settings.imap_host and settings.imap_user and settings.imap_password
    cls = imaplib.IMAP4_SSL if settings.imap_ssl else imaplib.IMAP4
    conn = cls(settings.imap_host, settings.imap_port)
    conn.login(settings.imap_user, settings.imap_password.get_secret_value())
    return conn  # type: ignore[return-value]


def poll_from_settings(settings: Settings, conn: IMAPConnection | None = None) -> PollResult:
    close = conn is None
    conn = conn or open_connection(settings)
    try:
        return poll_mailbox(
            conn,
            settings.inbox_dir,
            settings.out_dir / "mail_archive",
            settings.imap_mailbox,
            settings.imap_done_mailbox,
        )
    finally:
        if close:
            conn.logout()
