import httpx
from pathlib import Path


class APIError(RuntimeError): ...


class APIClient:
    def __init__(self, *, base_url: str, token: str, admin_token: str | None = None):
        self.base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._admin = admin_token

    def _get(self, path: str, **params) -> dict:
        r = httpx.get(f"{self.base}{path}", headers=self._headers,
                      params=params, timeout=30)
        if r.status_code == 404:
            raise LookupError(path)
        if r.status_code >= 400:
            raise APIError(f"{r.status_code}: {r.text}")
        return r.json()

    def get_job(self, job_id: str) -> dict:
        return self._get(f"/jobs/{job_id}")

    def list_jobs(self, status: str | None = None) -> list[dict]:
        return self._get("/jobs", **({"status": status} if status else {}))["jobs"]

    def get_transcript(self, job_id: str) -> dict:
        return self._get(f"/jobs/{job_id}/transcript")

    def get_draft(self, job_id: str) -> dict:
        return self._get(f"/jobs/{job_id}/draft")

    def delete_job(self, job_id: str) -> None:
        r = httpx.delete(f"{self.base}/jobs/{job_id}",
                         headers=self._headers, timeout=30)
        if r.status_code >= 400:
            raise APIError(f"{r.status_code}: {r.text}")

    # ── recordings (CLI/agent-launched Telemost) ────────────────────────────

    def create_recording(self, url: str, *, num_speakers: int | None = None,
                         quick: bool = False) -> dict:
        data = {"url": url}
        if num_speakers is not None:
            data["num_speakers"] = str(num_speakers)
        if quick:
            data["quick"] = "true"
        r = httpx.post(f"{self.base}/recordings", headers=self._headers,
                       data=data, timeout=30)
        if r.status_code >= 400:
            raise APIError(f"{r.status_code}: {r.text}")
        return r.json()

    def list_recordings(self, status: str | None = None) -> list[dict]:
        return self._get("/recordings", **({"status": status} if status else {}))["recordings"]

    def stop_recording(self, rec_id: str) -> dict:
        r = httpx.post(f"{self.base}/recordings/{rec_id}/stop",
                       headers=self._headers, timeout=30)
        if r.status_code >= 400:
            raise APIError(f"{r.status_code}: {r.text}")
        return r.json()

    def get_recording_quick(self, rec_id: str) -> dict:
        return self._get(f"/recordings/{rec_id}/quick")

    def upload(self, file_path: Path, *, mode: str, user_id: str,
               dedupe_key: str | None = None,
               num_speakers: int | None = None,
               origin: str | None = None,
               fallback_recorded_at: str | None = None) -> dict:
        with file_path.open("rb") as fh:
            files = {"file": (file_path.name, fh, "application/octet-stream")}
            data = {"mode": mode, "user_id": user_id}
            if dedupe_key:
                data["dedupe_key"] = dedupe_key
            if num_speakers is not None:
                data["num_speakers"] = str(num_speakers)
            if origin:
                data["origin"] = origin
            if fallback_recorded_at:
                data["fallback_recorded_at"] = fallback_recorded_at
            r = httpx.post(f"{self.base}/jobs", headers=self._headers,
                           files=files, data=data, timeout=600)
        if r.status_code == 413:
            raise APIError("file exceeds server limit")
        if r.status_code >= 400:
            raise APIError(f"{r.status_code}: {r.text}")
        return r.json()

    def health(self) -> dict:
        r = httpx.get(f"{self.base}/health", timeout=10)
        return r.json()

    def admin_disk_status(self) -> dict:
        r = httpx.get(f"{self.base}/admin/disk-status",
                      headers={"X-Admin-Token": self._admin or ""},
                      timeout=10)
        if r.status_code >= 400:
            raise APIError(f"{r.status_code}: {r.text}")
        return r.json()
