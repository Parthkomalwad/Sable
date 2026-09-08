"""SkillIndex manages skills_index.json.

Created automatically on first use (no separate init step needed).
Tracks: name, file, keywords, auto_generated, confidence, use_count,
        last_used, needs_update, created_at.

Confidence nudges: +0.05 on success (max 1.0), -0.1 on failure (min 0.0).
Initial: 0.5 auto-generated, 1.0 manual.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class SkillIndex:
    def __init__(self, index_path: str | None = None) -> None:
        if index_path is None:
            index_path = str(Path.home() / "skills" / "skills_index.json")
        self._path = Path(index_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text("[]")
        self._data: list[dict] = json.loads(self._path.read_text())

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._data, indent=2))

    def add(self, name: str, file: str, keywords: list[str],
            auto_generated: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        confidence = 0.5 if auto_generated else 1.0
        entry = {
            "name": name,
            "file": file,
            "keywords": keywords,
            "auto_generated": auto_generated,
            "confidence": confidence,
            "use_count": 0,
            "last_used": None,
            "needs_update": False,
            "created_at": now,
        }
        for i, e in enumerate(self._data):
            if e["name"] == name:
                self._data[i] = entry
                self._save()
                return
        self._data.append(entry)
        self._save()

    def list_all(self) -> list[dict]:
        return list(self._data)

    def record_use(self, name: str, success: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for entry in self._data:
            if entry["name"] == name:
                delta = 0.05 if success else -0.1
                entry["confidence"] = max(0.0, min(1.0, entry["confidence"] + delta))
                entry["use_count"] += 1
                entry["last_used"] = now
                self._save()
                return

    def get_ranked(self, keywords: list[str]) -> list[dict]:
        """Keyword-match then sort by confidence desc, use_count desc."""
        kw_set = {k.lower() for k in keywords}
        matched = [
            e for e in self._data
            if any(k in [kw.lower() for kw in e.get("keywords", [])] for k in kw_set)
        ]
        return sorted(matched, key=lambda e: (-e["confidence"], -e["use_count"]))

    def mark_needs_update(self, name: str) -> None:
        for entry in self._data:
            if entry["name"] == name:
                entry["needs_update"] = True
                self._save()
                return
