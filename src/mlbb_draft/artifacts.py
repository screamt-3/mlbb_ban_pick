from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import cv2
import numpy as np

from .models import ArtifactRef


class ArtifactNotFoundError(FileNotFoundError):
    pass


class LocalArtifactStore:
    def __init__(self, root: Path, default_retention_days: int = 30):
        self.root = root.resolve()
        self.default_retention_days = default_retention_days
        self.root.mkdir(parents=True, exist_ok=True)

    def _job_dir(self, job_id: UUID | str) -> Path:
        value = str(job_id)
        UUID(value)
        path = (self.root / value).resolve()
        if path.parent != self.root:
            raise ValueError("invalid job identifier")
        return path

    @staticmethod
    def _manifest_path(job_dir: Path) -> Path:
        return job_dir / "manifest.json"

    def _read_manifest(self, job_dir: Path) -> dict:
        path = self._manifest_path(job_dir)
        if not path.exists():
            return {"artifacts": {}}
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_manifest(self, job_dir: Path, manifest: dict) -> None:
        temporary = job_dir / "manifest.json.tmp"
        temporary.write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8")
        temporary.replace(self._manifest_path(job_dir))

    def put_bytes(
        self,
        job_id: UUID | str,
        content: bytes,
        *,
        suffix: str,
        mime_type: str,
        retention_days: int | None = None,
    ) -> ArtifactRef:
        job_dir = self._job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        artifact_id = uuid4().hex
        filename = f"{artifact_id}{suffix}"
        path = job_dir / filename
        path.write_bytes(content)
        created = datetime.now(timezone.utc)
        retained_until = created + timedelta(days=retention_days or self.default_retention_days)
        ref = ArtifactRef(
            artifact_id=artifact_id,
            uri=f"/analyses/{job_id}/artifacts/{artifact_id}",
            mime_type=mime_type,
            sha256=hashlib.sha256(content).hexdigest(),
            created_at=created,
            retained_until=retained_until,
        )
        manifest = self._read_manifest(job_dir)
        manifest["artifacts"][artifact_id] = {
            "filename": filename,
            "ref": ref.model_dump(mode="json"),
        }
        self._write_manifest(job_dir, manifest)
        return ref

    def put_image(self, job_id: UUID | str, pixels: np.ndarray, retention_days: int | None = None) -> ArtifactRef:
        succeeded, encoded = cv2.imencode(".png", pixels)
        if not succeeded:
            raise ValueError("could not encode image artifact")
        return self.put_bytes(
            job_id,
            encoded.tobytes(),
            suffix=".png",
            mime_type="image/png",
            retention_days=retention_days,
        )

    def resolve(self, job_id: UUID | str, artifact_id: str) -> tuple[Path, ArtifactRef]:
        job_dir = self._job_dir(job_id)
        record = self._read_manifest(job_dir).get("artifacts", {}).get(artifact_id)
        if record is None:
            raise ArtifactNotFoundError(artifact_id)
        path = (job_dir / record["filename"]).resolve()
        if path.parent != job_dir or not path.exists():
            raise ArtifactNotFoundError(artifact_id)
        return path, ArtifactRef.model_validate(record["ref"])

    def delete_job(self, job_id: UUID | str) -> None:
        job_dir = self._job_dir(job_id)
        if job_dir.exists():
            shutil.rmtree(job_dir)

    def retain_only(self, job_id: UUID | str, artifact_ids: set[str]) -> int:
        """Delete intermediate artifacts while keeping result-referenced evidence."""
        job_dir = self._job_dir(job_id)
        if not job_dir.exists():
            return 0
        manifest = self._read_manifest(job_dir)
        deleted = 0
        for artifact_id, record in list(manifest.get("artifacts", {}).items()):
            if artifact_id in artifact_ids:
                continue
            path = (job_dir / record["filename"]).resolve()
            if path.parent == job_dir and path.exists():
                path.unlink()
            del manifest["artifacts"][artifact_id]
            deleted += 1
        self._write_manifest(job_dir, manifest)
        return deleted

    def sweep_expired(self, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        deleted = 0
        for job_dir in self.root.iterdir():
            if not job_dir.is_dir():
                continue
            manifest = self._read_manifest(job_dir)
            changed = False
            for artifact_id, record in list(manifest.get("artifacts", {}).items()):
                ref = ArtifactRef.model_validate(record["ref"])
                if ref.retained_until is not None and ref.retained_until <= now:
                    path = job_dir / record["filename"]
                    if path.exists():
                        path.unlink()
                    del manifest["artifacts"][artifact_id]
                    deleted += 1
                    changed = True
            if changed:
                self._write_manifest(job_dir, manifest)
        return deleted


def mime_type_for(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"
