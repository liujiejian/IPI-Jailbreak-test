"""Simulated email inboxes — worm payload propagates between LLM-integrated apps."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class EmailMessage:
    email_id: str
    from_addr: str
    to_addr: str
    subject: str
    body: str


@dataclass
class EmailStore:
    """Per-recipient inboxes shared across hop agents within one test case."""

    _inboxes: dict[str, list[EmailMessage]] = field(default_factory=dict)

    def deliver(self, *, to_addr: str, from_addr: str, subject: str, body: str) -> EmailMessage:
        msg = EmailMessage(
            email_id=f"em_{uuid4().hex[:8]}",
            from_addr=from_addr,
            to_addr=to_addr,
            subject=subject,
            body=body,
        )
        self._inboxes.setdefault(to_addr, []).append(msg)
        return msg

    def seed(self, msg: EmailMessage) -> None:
        self._inboxes.setdefault(msg.to_addr, []).append(msg)

    def read_latest(self, inbox: str) -> EmailMessage | None:
        messages = self._inboxes.get(inbox) or []
        return messages[-1] if messages else None

    def snapshot(self) -> dict[str, list[dict[str, str]]]:
        out: dict[str, list[dict[str, str]]] = {}
        for inbox, messages in self._inboxes.items():
            out[inbox] = [
                {
                    "email_id": m.email_id,
                    "from": m.from_addr,
                    "to": m.to_addr,
                    "subject": m.subject,
                    "body": m.body,
                }
                for m in messages
            ]
        return out
