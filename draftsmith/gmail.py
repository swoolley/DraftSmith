from __future__ import annotations

import base64
import html
import json
import re
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .secrets import get_secret, set_secret

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.compose"]
TOKEN_KEY = "gmail_oauth_token"


@dataclass
class Mail:
    id: str
    thread_id: str
    subject: str
    sender: str
    to: str
    message_id: str
    references: str
    body: str


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _plain_body(payload: dict) -> str:
    candidates: list[tuple[str, str]] = []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime in {"text/plain", "text/html"}:
            candidates.append((mime, _decode(data)))
        for child in part.get("parts", []):
            walk(child)

    walk(payload)
    for wanted in ("text/plain", "text/html"):
        for mime, text in candidates:
            if mime == wanted:
                if mime == "text/html":
                    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
                return re.sub(r"\s+", " ", text).strip()
    return ""


class GmailClient:
    def __init__(self, client_secrets_path: str):
        self.client_secrets_path = client_secrets_path
        self.credentials: Credentials | None = None
        self.service = None

    def authenticate(self, interactive: bool = True) -> str:
        token = get_secret(TOKEN_KEY)
        if token:
            self.credentials = Credentials.from_authorized_user_info(json.loads(token), SCOPES)
        if self.credentials and self.credentials.expired and self.credentials.refresh_token:
            self.credentials.refresh(Request())
        if not self.credentials or not self.credentials.valid:
            if not interactive:
                raise RuntimeError("Gmail authentication is required")
            path = Path(self.client_secrets_path).expanduser()
            if not path.is_file():
                raise FileNotFoundError("Select a Google OAuth desktop client JSON file first")
            flow = InstalledAppFlow.from_client_secrets_file(str(path), SCOPES)
            self.credentials = flow.run_local_server(port=0)
        set_secret(TOKEN_KEY, self.credentials.to_json())
        self.service = build("gmail", "v1", credentials=self.credentials, cache_discovery=False)
        return self.profile_email()

    def profile_email(self) -> str:
        return self.service.users().getProfile(userId="me").execute()["emailAddress"]

    def labels(self) -> list[tuple[str, str]]:
        result = self.service.users().labels().list(userId="me").execute()
        return sorted(((x["name"], x["id"]) for x in result.get("labels", [])), key=lambda x: x[0].lower())

    def _message(self, message_id: str) -> Mail:
        raw = self.service.users().messages().get(userId="me", id=message_id, format="full").execute()
        headers = {h["name"].lower(): h["value"] for h in raw["payload"].get("headers", [])}
        return Mail(raw["id"], raw["threadId"], headers.get("subject", "(no subject)"),
                    headers.get("from", ""), headers.get("to", ""), headers.get("message-id", ""),
                    headers.get("references", ""), _plain_body(raw["payload"]))

    def new_messages(self, label_ids: list[str], after_epoch: int) -> list[Mail]:
        found: dict[str, Mail] = {}
        for label_id in label_ids:
            token = None
            while True:
                page = self.service.users().messages().list(
                    userId="me", labelIds=[label_id], q=f"after:{after_epoch} -in:sent -in:drafts",
                    pageToken=token, maxResults=100,
                ).execute()
                for item in page.get("messages", []):
                    found.setdefault(item["id"], self._message(item["id"]))
                token = page.get("nextPageToken")
                if not token:
                    break
        return list(found.values())

    def sent_context(self, mail: Mail, limit: int) -> list[Mail]:
        address = parseaddr(mail.sender)[1]
        query = f'in:sent to:"{address}"' if address else "in:sent"
        result = self.service.users().messages().list(userId="me", q=query, maxResults=limit).execute()
        return [self._message(x["id"]) for x in result.get("messages", [])]

    def create_reply_draft(self, original: Mail, body: str) -> str:
        recipient = parseaddr(original.sender)[1]
        if not recipient:
            raise ValueError("Incoming message has no valid From address")
        msg = EmailMessage()
        msg["To"] = recipient
        msg["Subject"] = original.subject if original.subject.lower().startswith("re:") else f"Re: {original.subject}"
        if original.message_id:
            msg["In-Reply-To"] = original.message_id
            msg["References"] = f"{original.references} {original.message_id}".strip()
        msg.set_content(body.strip())
        encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        result = self.service.users().drafts().create(
            userId="me", body={"message": {"raw": encoded, "threadId": original.thread_id}}
        ).execute()
        return result["id"]


def resolve_label_ids(available: list[tuple[str, str]], requested: list[str]) -> list[str]:
    by_name = {name.casefold(): ident for name, ident in available}
    missing = [name for name in requested if name.casefold() not in by_name]
    if missing:
        raise ValueError(f"Unknown Gmail label(s): {', '.join(missing)}")
    return [by_name[name.casefold()] for name in requested]
