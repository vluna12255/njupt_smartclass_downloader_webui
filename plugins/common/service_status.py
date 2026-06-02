"""Shared service status and startup reporting helpers for plugins."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path


class PluginServiceStatus:
    def __init__(self, device: str, **details):
        self.lock = threading.Lock()
        self.phase = "initializing"
        self.progress = 0.0
        self.message = ""
        self.error = ""
        self.timestamp = None
        self.device = device
        self.details = details
        self.status_file = os.environ.get("PLUGIN_STATUS_FILE", "").strip()
        self._write_snapshot_unlocked()

    def update(self, phase: str = None, message: str = "", progress: float = None, error: str = "", **details):
        with self.lock:
            if phase:
                self.phase = phase
            if message:
                self.message = message
            if progress is not None:
                self.progress = progress
            if error:
                self.error = error
            self.details.update(details)
            self.timestamp = time.time()
            self._write_snapshot_unlocked()

    def to_dict(self):
        with self.lock:
            return self._serialize_unlocked()

    def _serialize_unlocked(self):
        return {
            "phase": self.phase,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "timestamp": self.timestamp,
            "device": self.device,
            **self.details,
        }

    def _write_snapshot_unlocked(self):
        if not self.status_file:
            return
        try:
            target = Path(self.status_file)
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(f"{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            temp.write_text(json.dumps(self._serialize_unlocked(), ensure_ascii=False), encoding="utf-8")
            temp.replace(target)
        except OSError:
            pass


def create_startup_reporter(main_server_url: str, plugin_name: str):
    def report(phase: str, message: str, progress: float = -1, success: bool = True, **details):
        if not main_server_url:
            return
        try:
            import requests

            payload = {"phase": phase, "message": message, "success": success}
            if progress >= 0:
                payload["progress"] = progress
            payload.update(details)
            requests.post(
                f"{main_server_url.rstrip('/')}/api/plugins/{plugin_name}/startup_report",
                json=payload,
                timeout=5,
            )
        except Exception:
            pass

    return report
