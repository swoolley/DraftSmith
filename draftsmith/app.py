from __future__ import annotations

import threading
import time

from openai import OpenAI
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QSpinBox, QTextEdit, QVBoxLayout, QWidget,
)

from .config import AppConfig, DB_PATH
from .engine import DraftEngine
from .gmail import GmailClient
from .secrets import get_secret, set_secret
from .state import StateStore


class Bridge(QObject):
    log = Signal(str)
    status = Signal(str)
    gmail = Signal(str)


class DraftSmithApp:
    def __init__(self):
        self.qt = QApplication.instance() or QApplication([])
        self.window = QMainWindow()
        self.window.setWindowTitle("DraftSmith")
        self.window.resize(720, 610)
        self.config = AppConfig.load()
        self.state = StateStore(DB_PATH)
        self.gmail = GmailClient(self.config.client_secrets_path)
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.bridge = Bridge()
        self.bridge.log.connect(self._append_log)
        self.bridge.status.connect(self._set_status)
        self.bridge.gmail.connect(lambda email: self.gmail_status.setText(f"Gmail: connected as {email}"))
        self._build()

    def _build(self) -> None:
        root, layout = QWidget(), QVBoxLayout()
        root.setLayout(layout)
        title = QLabel("DraftSmith")
        title.setStyleSheet("font-size: 26px; font-weight: 650")
        layout.addWidget(title)
        subtitle = QLabel("Private, draft-only email assistance")
        subtitle.setStyleSheet("color: #666; margin-bottom: 12px")
        layout.addWidget(subtitle)

        connections = QFrame()
        connections.setFrameShape(QFrame.Shape.StyledPanel)
        form = QFormLayout(connections)
        gmail_row, gmail_buttons = QWidget(), QHBoxLayout()
        gmail_buttons.setContentsMargins(0, 0, 0, 0)
        self.gmail_status = QLabel("Gmail: not connected")
        choose = QPushButton("Choose OAuth JSON…")
        choose.clicked.connect(self._choose_oauth)
        connect = QPushButton("Connect Gmail")
        connect.clicked.connect(self._connect_gmail)
        for widget in (self.gmail_status, choose, connect):
            gmail_buttons.addWidget(widget)
        gmail_row.setLayout(gmail_buttons)
        form.addRow("Gmail", gmail_row)
        key_row, key_layout = QWidget(), QHBoxLayout()
        key_layout.setContentsMargins(0, 0, 0, 0)
        self.api_key = QLineEdit("" if not get_secret("openai_api_key") else "••••••••••••")
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        save_key = QPushButton("Save & test")
        save_key.clicked.connect(self._save_api_key)
        key_layout.addWidget(self.api_key)
        key_layout.addWidget(save_key)
        key_row.setLayout(key_layout)
        form.addRow("OpenAI API key", key_row)
        layout.addWidget(connections)

        settings = QFrame()
        settings.setFrameShape(QFrame.Shape.StyledPanel)
        scan_form = QFormLayout(settings)
        self.labels = QLineEdit(", ".join(self.config.labels))
        scan_form.addRow("Gmail labels", self.labels)
        self.refresh = QSpinBox()
        self.refresh.setRange(1, 1440)
        self.refresh.setValue(self.config.refresh_minutes)
        self.refresh.setSuffix(" minutes")
        scan_form.addRow("Refresh every", self.refresh)
        self.model = QLineEdit(self.config.model)
        scan_form.addRow("OpenAI model", self.model)
        self.drafting_prompt = QTextEdit()
        self.drafting_prompt.setPlainText(self.config.drafting_prompt)
        self.drafting_prompt.setFixedHeight(72)
        self.drafting_prompt.setPlaceholderText("Draft the most likely reply.")
        scan_form.addRow("Drafting prompt", self.drafting_prompt)
        layout.addWidget(settings)

        controls, control_layout = QWidget(), QHBoxLayout()
        control_layout.setContentsMargins(0, 0, 0, 0)
        self.start_button = QPushButton("Start background scanning")
        self.start_button.clicked.connect(self._toggle)
        scan = QPushButton("Scan now")
        scan.clicked.connect(self._scan_now)
        self.run_status = QLabel("Stopped")
        control_layout.addWidget(self.start_button)
        control_layout.addWidget(scan)
        control_layout.addStretch()
        control_layout.addWidget(self.run_status)
        controls.setLayout(control_layout)
        layout.addWidget(controls)
        layout.addWidget(QLabel("Activity"))
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        layout.addWidget(self.log_box, 1)
        safety = QLabel("Safety: DraftSmith only creates Gmail drafts. It contains no send operation.")
        safety.setStyleSheet("color: #666")
        layout.addWidget(safety)
        self.window.setCentralWidget(root)

    def _append_log(self, message: str) -> None:
        self.log_box.append(message)

    def _log(self, message: str) -> None:
        self.bridge.log.emit(f"{time.strftime('%H:%M:%S')}  {message}")

    def _set_status(self, status: str) -> None:
        self.run_status.setText(status)
        if status == "Stopped":
            self.start_button.setText("Start background scanning")

    def _choose_oauth(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self.window, "Google OAuth desktop client JSON", "", "JSON files (*.json)")
        if path:
            self.config.client_secrets_path = path
            self.config.save()
            self.gmail = GmailClient(path)
            self._log("Google OAuth client file selected.")

    def _connect_gmail(self) -> None:
        def connect():
            try:
                email = self.gmail.authenticate(interactive=True)
                self.bridge.gmail.emit(email)
                self._log(f"Connected to Gmail as {email}.")
            except Exception as exc:
                self._log(f"Gmail connection failed: {exc}")
        threading.Thread(target=connect, daemon=True).start()

    def _save_api_key(self) -> None:
        value = self.api_key.text().strip()
        if not value or value == "••••••••••••":
            QMessageBox.information(self.window, "OpenAI", "Enter a new API key to test it.")
            return
        try:
            OpenAI(api_key=value).models.list()
            set_secret("openai_api_key", value)
            self.api_key.setText("••••••••••••")
            self._log("OpenAI API key saved securely and verified.")
        except Exception as exc:
            QMessageBox.critical(self.window, "OpenAI connection failed", str(exc))

    def _prepare_engine(self) -> DraftEngine:
        labels = [x.strip() for x in self.labels.text().split(",") if x.strip()]
        if not labels:
            raise ValueError("Enter at least one Gmail label")
        self.config.labels = labels
        self.config.refresh_minutes = self.refresh.value()
        self.config.model = self.model.text().strip() or "gpt-5-mini"
        self.config.drafting_prompt = self.drafting_prompt.toPlainText().strip() or "Draft the most likely reply."
        self.config.save()
        if not get_secret("openai_api_key"):
            raise RuntimeError("Save an OpenAI API key first")
        if self.gmail.service is None:
            email = self.gmail.authenticate(interactive=False)
            self.bridge.gmail.emit(email)
        return DraftEngine(self.config, self.gmail, self.state, self._log)

    def _do_scan(self, engine: DraftEngine) -> None:
        try:
            self.bridge.status.emit("Scanning…")
            count = engine.scan_once()
            self._log(f"Scan complete: {count} draft(s) created.")
        except Exception as exc:
            self._log(f"Scan failed: {exc}")

    def _scan_now(self) -> None:
        if self.worker and self.worker.is_alive():
            self._log("A scan is already running.")
            return
        try:
            engine = self._prepare_engine()
        except Exception as exc:
            QMessageBox.critical(self.window, "Cannot scan", str(exc))
            return
        def once():
            self._do_scan(engine)
            self.bridge.status.emit("Stopped")
        self.worker = threading.Thread(target=once, daemon=True)
        self.worker.start()

    def _loop(self, engine: DraftEngine) -> None:
        while not self.stop_event.is_set():
            self._do_scan(engine)
            self.bridge.status.emit("Running")
            if self.stop_event.wait(self.config.refresh_minutes * 60):
                break
        self.bridge.status.emit("Stopped")

    def _toggle(self) -> None:
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self.start_button.setText("Start background scanning")
            self.run_status.setText("Stopping…")
            return
        try:
            engine = self._prepare_engine()
        except Exception as exc:
            QMessageBox.critical(self.window, "Cannot start", str(exc))
            return
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._loop, args=(engine,), daemon=True)
        self.worker.start()
        self.start_button.setText("Stop scanning")

    def run(self) -> None:
        self.window.show()
        self.qt.aboutToQuit.connect(self.stop_event.set)
        self.qt.exec()
