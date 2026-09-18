"""Upload one private diagnostic ZIP as a GitHub release asset."""
from __future__ import annotations

import re
import socket
import time
import zipfile
from pathlib import Path

import requests


API_ROOT = "https://api.github.com"
MAX_ARCHIVE_BYTES = 2_000_000_000


def _headers(token):
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def verify_access(repository, token):
    response = requests.get(
        f"{API_ROOT}/repos/{repository}", headers=_headers(token), timeout=20
    )
    if not response.ok:
        raise RuntimeError(f"GitHub từ chối token (HTTP {response.status_code})")
    permissions = response.json().get("permissions") or {}
    if permissions and not permissions.get("push"):
        raise RuntimeError("Token chưa có quyền ghi vào repository chẩn đoán")


def _create_archive(folder, app_slug):
    folder = Path(folder)
    archive = folder.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                output.write(path, arcname=path.relative_to(folder))
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        archive.unlink(missing_ok=True)
        raise RuntimeError("Gói chẩn đoán vượt quá giới hạn tải lên GitHub")
    safe_host = re.sub(r"[^A-Za-z0-9._-]+", "-", socket.gethostname()).strip("-")
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return archive, f"{app_slug}-{safe_host}-{timestamp}-{folder.name}.zip"


def _get_or_create_release(repository, token):
    tag = "diagnostics-" + time.strftime("%Y-%m")
    headers = _headers(token)
    response = requests.get(
        f"{API_ROOT}/repos/{repository}/releases/tags/{tag}",
        headers=headers,
        timeout=20,
    )
    if response.status_code == 404:
        response = requests.post(
            f"{API_ROOT}/repos/{repository}/releases",
            headers=headers,
            json={
                "tag_name": tag,
                "name": f"TOMS SLA diagnostics {time.strftime('%Y-%m')}",
                "body": "Gói chẩn đoán tự động từ các ứng dụng TOMS SLA.",
                "prerelease": True,
            },
            timeout=20,
        )
    if not response.ok:
        raise RuntimeError(
            f"Không tạo được GitHub Release chẩn đoán (HTTP {response.status_code})"
        )
    return response.json()


def upload_diagnostic(folder, repository, token, app_slug):
    """Compress and upload exactly one diagnostic folder."""
    archive, asset_name = _create_archive(folder, app_slug)
    try:
        release = _get_or_create_release(repository, token)
        upload_url = release["upload_url"].split("{")[0]
        headers = _headers(token)
        headers["Content-Type"] = "application/zip"
        with archive.open("rb") as source:
            response = requests.post(
                upload_url,
                headers=headers,
                params={"name": asset_name},
                data=source,
                timeout=(20, 600),
            )
        if not response.ok:
            raise RuntimeError(
                f"Không tải được gói chẩn đoán lên GitHub (HTTP {response.status_code})"
            )
        return response.json()["browser_download_url"]
    finally:
        archive.unlink(missing_ok=True)
