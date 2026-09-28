"""Alerts. Two channels, as a data team runs them: a status channel for "all is well" and for
changes worth knowing about, and an alerts channel for anything a person has to act on.

Every message is written to the run's evidence (alerts.jsonl). If POPHEALTH_WEBHOOK_URL is set, the
message is also posted as JSON to that webhook (Slack- and Teams-style incoming webhooks accept
{"text": ...}); nothing is ever posted otherwise, and a failed post never fails the pipeline.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime
from pathlib import Path


class Notifier:
    def __init__(self, config: dict, run_id: str, evidence_dir: Path):
        alerts = config.get("alerts", {})
        self.status_channel = alerts.get("status_channel", "#status")
        self.error_channel = alerts.get("error_channel", "#alerts")
        self.webhook = os.environ.get(alerts.get("webhook_env", ""), "") if alerts.get("webhook_env") else ""
        self.run_id = run_id
        self.path = evidence_dir / "alerts.jsonl"

    def status(self, title: str, lines: list[str] | None = None, feed: str | None = None) -> dict:
        return self._send(self.status_channel, "info", title, lines or [], feed)

    def alert(
        self, title: str, lines: list[str] | None = None, feed: str | None = None, severity: str = "error"
    ) -> dict:
        return self._send(self.error_channel, severity, title, lines or [], feed)

    def _send(self, channel: str, severity: str, title: str, lines: list[str], feed: str | None) -> dict:
        message = {
            "run_id": self.run_id,
            "at": datetime.now().replace(microsecond=0).isoformat(),
            "channel": channel,
            "severity": severity,
            "feed": feed,
            "title": title,
            "text": "\n".join(lines),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(message) + "\n")
        if self.webhook:
            self._post(message)
        return message

    def _post(self, message: dict) -> None:
        body = json.dumps({"text": f"*{message['title']}*\n{message['text']}"}).encode()
        request = urllib.request.Request(self.webhook, data=body, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(request, timeout=10).close()
        except OSError as exc:
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                f.write(
                    json.dumps(
                        {
                            "run_id": self.run_id,
                            "channel": "-",
                            "severity": "warning",
                            "title": "webhook post failed",
                            "text": str(exc),
                        }
                    )
                    + "\n"
                )
