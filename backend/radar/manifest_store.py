"""Holds the connector manifest in use, keeping the last valid one on failure.

A load that fails validation is reported but never replaces the manifest in use.
The last valid manifest's text is persisted in the data dir, so a restart (or a
manifest file that later breaks or disappears) does not leave the radar blind.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from .manifest import MAX_MANIFEST_BYTES, Issue, Manifest, ValidationResult, validate_text

log = logging.getLogger(__name__)

STATE_FILE = "manifest-state.json"
MANIFEST_SUFFIXES = (".yaml", ".yml")


class ManifestStore:
    def __init__(self, data_dir: Path) -> None:
        self._state_path = data_dir / STATE_FILE
        self.current: Manifest | None = None
        self.current_path: str = ""
        self.loaded_at: float | None = None
        self.last_result: ValidationResult | None = None
        self.last_path: str = ""
        self.last_attempt_at: float | None = None
        self._restore()

    # -- loading ------------------------------------------------------------

    def load_path(self, path: str) -> ValidationResult:
        """Read, validate and (if valid) apply the manifest at ``path``."""
        self.last_path = path
        text, problem = self._read(path)
        if text is None:
            return self._record(ValidationResult(None, [Issue(0, "", problem)], []), path, None)
        return self._record(validate_text(text), path, text)

    @staticmethod
    def _read(path: str) -> tuple[str | None, str]:
        """Return (text, "") for a readable manifest file, else (None, problem)."""
        p = Path(path).expanduser()
        if not path or not p.is_absolute():
            return None, "manifest path must be an absolute path"
        if p.suffix.lower() not in MANIFEST_SUFFIXES:
            return None, "manifest file must end in .yaml or .yml"
        if not p.is_file():
            return None, f"no manifest file at {p}"
        try:
            if p.stat().st_size > MAX_MANIFEST_BYTES:
                return None, f"manifest exceeds {MAX_MANIFEST_BYTES} bytes"
            return p.read_text(encoding="utf-8"), ""
        except (OSError, UnicodeDecodeError) as e:
            return None, f"cannot read manifest: {e}"

    def _record(self, result: ValidationResult, path: str, text: str | None) -> ValidationResult:
        self.last_result = result
        self.last_attempt_at = time.time()
        if result.ok and text is not None:
            self.current = result.manifest
            self.current_path = path
            self.loaded_at = self.last_attempt_at
            self._persist(path, text)
        elif self.current is not None:
            log.warning("manifest at %s is invalid (%d errors); keeping the last valid manifest",
                        path, len(result.errors))
        return result

    # -- persistence --------------------------------------------------------

    def _persist(self, path: str, text: str) -> None:
        state = {"path": path, "text": text, "loadedAt": self.loaded_at}
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state), encoding="utf-8")
            os.replace(tmp, self._state_path)
        except OSError as e:
            log.error("could not persist the last valid manifest: %s", e)

    def _restore(self) -> None:
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            log.error("ignoring unreadable manifest state: %s", e)
            return
        result = validate_text(str(state.get("text", "")))
        if result.ok:
            self.current = result.manifest
            self.current_path = str(state.get("path", ""))
            self.loaded_at = state.get("loadedAt")
        else:
            log.error("persisted manifest no longer validates; starting without one")

    # -- reporting ----------------------------------------------------------

    def status(self) -> dict[str, Any]:
        last = self.last_result
        return {
            "inUse": (
                {"path": self.current_path, "loadedAt": self.loaded_at, **self.current.summary()}
                if self.current else None
            ),
            "lastAttempt": (
                {"path": self.last_path, "at": self.last_attempt_at, **last.to_dict()}
                if last else None
            ),
            # True when the latest attempt failed but an older valid manifest is still used.
            "usingFallback": bool(self.current and last and not last.ok),
        }
