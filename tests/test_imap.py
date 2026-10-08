from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

import pytest
from conftest import ROOT, make_settings

from docpipe.ingest.fake_imap import FakeIMAPServer
from docpipe.ingest.imap import poll_from_settings, poll_mailbox


def _mail(subject: str, body: str, attachments: dict[str, bytes] | None = None) -> bytes:
    msg = EmailMessage()
    msg["From"] = "vendor@example.com"
    msg["To"] = "ap@example.com"
    msg["Subject"] = subject
    msg.set_content(body)
    for name, data in (attachments or {}).items():
        suffix = Path(name).suffix.lstrip(".")
        msg.add_attachment(data, maintype="application", subtype=suffix, filename=name)
    return bytes(msg)


@pytest.fixture
def mailbox() -> FakeIMAPServer:
    pdf = (ROOT / "inbox" / "inv_1001_sparkleclean.pdf").read_bytes()
    return FakeIMAPServer(
        {
            "AP/Incoming": [
                _mail("Invoice INV-1001", "See attached.", {"inv_1001.pdf": pdf}),
                _mail("Invoice INV-1009 (overdue)", "Please pay 360.00 USD for INV-1009."),
                _mail("Newsletter", "Deals!", {"banner.png": b"\x89PNG"}),
            ]
        }
    )


def test_poll_saves_attachments_and_bodies_and_moves_mail(tmp_path: Path, mailbox):
    inbox, archive = tmp_path / "inbox", tmp_path / "archive"
    result = poll_mailbox(mailbox, inbox, archive, "AP/Incoming", "AP/Processed")

    assert result.fetched == 3 and result.moved == 3 and result.errors == []
    names = sorted(p.name for p in inbox.iterdir())
    assert len(names) == 3
    assert any(n.endswith("__inv_1001.pdf") for n in names)  # attachment saved as a PDF
    assert sum(n.endswith(".eml") for n in names) == 2  # body-only mails saved as .eml
    assert not any(n.endswith(".png") for n in names)  # unsupported attachment ignored
    assert len(list(archive.iterdir())) == 3  # raw copies for audit
    assert mailbox.messages_in("AP/Incoming") == []
    assert len(mailbox.messages_in("AP/Processed")) == 3


def test_poll_is_idempotent_on_content(tmp_path: Path, mailbox):
    inbox, archive = tmp_path / "inbox", tmp_path / "archive"
    poll_mailbox(mailbox, inbox, archive, "AP/Incoming", "AP/Processed")
    # Same mail arriving again produces the same file names (hash of raw bytes) ...
    mailbox.mailboxes["AP/Incoming"] = list(mailbox.mailboxes["AP/Processed"])
    before = sorted(p.name for p in inbox.iterdir())
    poll_mailbox(mailbox, inbox, archive, "AP/Incoming", "AP/Processed")
    assert sorted(p.name for p in inbox.iterdir()) == before


def test_failed_message_is_left_in_place(tmp_path: Path, mailbox):
    mailbox.fail_fetch_of = {2}
    result = poll_mailbox(mailbox, tmp_path / "in", tmp_path / "ar", "AP/Incoming", "AP/Done")
    assert result.fetched == 2 and result.moved == 2
    assert len(result.errors) == 1 and "fetch 2" in result.errors[0]
    assert len(mailbox.messages_in("AP/Incoming")) == 1  # retried next poll


def test_missing_mailbox_raises(tmp_path: Path):
    with pytest.raises(RuntimeError, match="select"):
        poll_mailbox(FakeIMAPServer(), tmp_path / "in", tmp_path / "ar", "Nope", "Done")


def test_poll_from_settings_uses_configured_folders(tmp_path: Path, mailbox):
    settings = make_settings(
        tmp_path,
        ingest_mode="imap",
        imap_host="h",
        imap_user="u",
        imap_password="p",
        imap_mailbox="AP/Incoming",
        imap_done_mailbox="AP/Processed",
        inbox_dir=tmp_path / "inbox",
    )
    result = poll_from_settings(settings, conn=mailbox)
    assert result.moved == 3
    assert (settings.out_dir / "mail_archive").exists()
    assert not mailbox.logged_out  # caller-supplied connections are not closed


def test_polled_mail_flows_through_the_pipeline(tmp_path: Path, mailbox, extractor):
    from docpipe import pipeline
    from docpipe.extract import SourceDocument
    from docpipe.models import LLMExtraction

    inbox = tmp_path / "inbox"
    poll_mailbox(mailbox, inbox, tmp_path / "archive", "AP/Incoming", "AP/Processed")

    class ByContent:
        def extract(self, doc: SourceDocument):
            if "INV-1001" in doc.text:
                return extractor.fixtures["inv_1001_sparkleclean.pdf"], "text"
            if "INV-1009" in doc.text:
                return extractor.fixtures["email_brightway_inv_1009.txt"], "text"
            return LLMExtraction(doc_type="other", confidence=0.9), "text"

    result = pipeline.run(inbox, ROOT / "data", tmp_path / "out", ByContent())
    statuses = {d.reconciliation.status.value for d in result.processed if d.reconciliation}
    assert len(result.processed) == 3 and statuses == {"paid", "overdue"}
