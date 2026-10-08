"""In-memory IMAP server implementing the `IMAPConnection` protocol with imaplib's
response shapes. Used by the test-suite and handy for local dry runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Message:
    raw: bytes
    flags: set[str] = field(default_factory=set)


class FakeIMAPServer:
    def __init__(self, mailboxes: dict[str, list[bytes]] | None = None):
        self.mailboxes: dict[str, list[_Message]] = {
            name: [_Message(raw) for raw in msgs] for name, msgs in (mailboxes or {}).items()
        }
        self.selected: str | None = None
        self.logged_out = False
        self.fail_fetch_of: set[int] = set()  # 1-based sequence numbers that raise

    # --- helpers for tests ---------------------------------------------------------

    def messages_in(self, mailbox: str) -> list[bytes]:
        return [m.raw for m in self.mailboxes.get(mailbox, [])]

    # --- IMAPConnection -------------------------------------------------------------

    def select(self, mailbox: str = "INBOX", readonly: bool = False) -> tuple[str, list[Any]]:
        if mailbox not in self.mailboxes:
            return "NO", [b"[NONEXISTENT] Unknown Mailbox"]
        self.selected = mailbox
        return "OK", [str(len(self.mailboxes[mailbox])).encode()]

    def search(self, charset: str | None, *criteria: str) -> tuple[str, list[Any]]:
        assert self.selected is not None
        nums = [str(i + 1).encode() for i in range(len(self.mailboxes[self.selected]))]
        return "OK", [b" ".join(nums)]

    def fetch(self, message_set: str, message_parts: str) -> tuple[str, list[Any]]:
        assert self.selected is not None
        num = int(message_set)
        if num in self.fail_fetch_of:
            return "NO", [b"fetch failed"]
        raw = self.mailboxes[self.selected][num - 1].raw
        return "OK", [(f"{num} (RFC822 {{{len(raw)}}}".encode(), raw), b")"]

    def copy(self, message_set: str, new_mailbox: str) -> tuple[str, list[Any]]:
        assert self.selected is not None
        if new_mailbox not in self.mailboxes:
            return "NO", [b"[TRYCREATE] Mailbox does not exist"]
        msg = self.mailboxes[self.selected][int(message_set) - 1]
        self.mailboxes[new_mailbox].append(_Message(msg.raw))
        return "OK", [b"COPY completed"]

    def store(self, message_set: str, command: str, flags: str) -> tuple[str, list[Any]]:
        assert self.selected is not None
        msg = self.mailboxes[self.selected][int(message_set) - 1]
        for flag in flags.strip("()").split():
            if command.startswith("+"):
                msg.flags.add(flag)
            else:
                msg.flags.discard(flag)
        return "OK", [b"STORE completed"]

    def create(self, mailbox: str) -> tuple[str, list[Any]]:
        if mailbox in self.mailboxes:
            return "NO", [b"[ALREADYEXISTS] Mailbox already exists"]
        self.mailboxes[mailbox] = []
        return "OK", [b"CREATE completed"]

    def expunge(self) -> tuple[str, list[Any]]:
        assert self.selected is not None
        box = self.mailboxes[self.selected]
        self.mailboxes[self.selected] = [m for m in box if "\\Deleted" not in m.flags]
        return "OK", [b"EXPUNGE completed"]

    def logout(self) -> tuple[str, list[Any]]:
        self.logged_out = True
        return "BYE", [b"Logging out"]
