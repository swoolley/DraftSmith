# DraftSmith

DraftSmith is a standalone Qt desktop utility built with PySide6. It watches selected Gmail labels and creates AI-assisted reply **drafts**. It never sends mail and has no send code path.

## Setup

1. In Google Cloud Console, create a project, enable the Gmail API, configure the OAuth consent screen, and create an **OAuth client ID → Desktop app**. Download its JSON file.
2. In PyCharm, select the project's virtual environment and run `pip install -r requirements.txt`.
3. Run `main.py`, choose the downloaded OAuth JSON, and click **Connect Gmail**.
4. Paste a project-scoped OpenAI API key and click **Save & test**.
5. Enter Gmail label names (for example `INBOX, Clients`), choose a refresh interval, and customize the drafting prompt if desired.

On the first scan, DraftSmith establishes a time baseline and does not process existing mail. Later scans create at most one draft per incoming Gmail message.

## Privacy and safety

- OAuth tokens and the OpenAI API key are stored in the operating system credential vault through `keyring`.
- The local SQLite database stores Gmail message IDs, draft IDs, and timestamps only.
- Relevant sent messages are fetched just-in-time and are not cached locally.
- OpenAI Responses requests use `store=False`.
- Requested Gmail scopes are read-only mailbox access plus Gmail's composition scope. Google bundles draft creation and sending into that composition scope, but this app has no send implementation and never calls Gmail's send endpoint.

Email content is necessarily transmitted to the OpenAI API to generate a reply. Review your organization's data requirements before use.
