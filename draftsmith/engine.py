from __future__ import annotations

import time
from typing import Callable

from openai import OpenAI

from .config import AppConfig
from .gmail import GmailClient, Mail, resolve_label_ids
from .secrets import get_secret
from .state import StateStore

def _truncate_utf8(text: str, byte_limit: int) -> str:
    """Truncate without splitting a multi-byte UTF-8 character."""
    return text.encode("utf-8")[:byte_limit].decode("utf-8", errors="ignore")


class DraftEngine:
    def __init__(self, config: AppConfig, gmail: GmailClient, state: StateStore, log: Callable[[str], None]):
        self.config, self.gmail, self.state, self.log = config, gmail, state, log

    def _draft_text(self, incoming: Mail, history: list[Mail]) -> str:
        key = get_secret("openai_api_key")
        if not key:
            raise RuntimeError("OpenAI API key is not configured")
        context = "\n\n".join(
            f"SENT EXAMPLE {i + 1}\nTo: {m.to}\nSubject: {m.subject}\n"
            f"Body: {_truncate_utf8(m.body, self.config.context_body_bytes)}"
            for i, m in enumerate(history)
        ) or "No relevant sent examples were found."
        incoming_text = f"From: {incoming.sender}\nSubject: {incoming.subject}\nBody: {incoming.body[:12000]}"
        response = OpenAI(api_key=key).responses.create(
            model=self.config.model, store=False, instructions=self.config.system_prompt,
            input=(f"OWNER'S RELEVANT SENT MAIL\n{context}\n\n"
                   f"INCOMING EMAIL\n{incoming_text}\n\n"
                   f"DRAFTING REQUEST\n{self.config.drafting_prompt}"),
        )
        text = response.output_text.strip()
        if not text:
            raise RuntimeError("OpenAI returned an empty draft")
        return text

    def scan_once(self) -> int:
        now = int(time.time())
        last = self.state.get("last_scan_epoch")
        if last is None:
            self.state.set("last_scan_epoch", str(now))
            self.log("Baseline established; only mail arriving after now will be drafted.")
            return 0
        available_labels = self.gmail.labels()
        labels = resolve_label_ids(available_labels, self.config.labels)
        supplemental_label_id = None
        if self.config.supplemental_sent_label:
            supplemental_label_id = resolve_label_ids(
                available_labels, [self.config.supplemental_sent_label]
            )[0]
        messages = self.gmail.new_messages(labels, int(last))
        created = 0
        had_errors = False
        for mail in messages:
            if self.state.is_processed(mail.id):
                continue
            try:
                history = self.gmail.sent_context(mail, self.config.sent_context_count)
                if supplemental_label_id:
                    supplemental = self.gmail.labeled_sent_context(
                        supplemental_label_id, self.config.supplemental_sent_label_count
                    )
                    seen = {item.id for item in history}
                    history.extend(item for item in supplemental if item.id not in seen)
                draft_id = self.gmail.create_reply_draft(mail, self._draft_text(mail, history))
                self.state.record(mail.id, draft_id)
                created += 1
                self.log(f'Draft created: "{mail.subject}"')
            except Exception as exc:
                had_errors = True
                self.log(f'Could not draft "{mail.subject}": {exc}')
        if not had_errors:
            self.state.set("last_scan_epoch", str(now))
        return created
