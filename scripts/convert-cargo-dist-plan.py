#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx"]
# ///
"""Convert cargo-dist plan JSON to a version NDJSON line.

Reads `cargo dist plan --output-format=json` from stdin and outputs
one NDJSON line to stdout.

The output `date` is normalized to UTC RFC3339 and comes from the
GitHub release's `published_at` timestamp.

Usage:
    cargo dist plan --output-format=json | convert-cargo-dist-plan.py
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any

import httpx


def get_archive_format(filename: str) -> str:
    """Determine archive format from filename."""
    if filename.endswith(".tar.gz"):
        return "tar.gz"
    elif filename.endswith(".tar.zst"):
        return "tar.zst"
    elif filename.endswith(".zip"):
        return "zip"
    else:
        return "unknown"


def build_github_headers() -> dict[str, str]:
    """Build GitHub API headers, using GITHUB_TOKEN when available."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    return headers


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO 8601 timestamp and normalize it to UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include a timezone offset: {value!r}")
    return parsed.astimezone(timezone.utc)


def format_timestamp(value: datetime) -> str:
    """Format a datetime in canonical UTC RFC3339 form."""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_timestamp(value: str) -> str:
    """Normalize a timestamp string to canonical UTC RFC3339 form."""
    return format_timestamp(parse_timestamp(value))


def fetch_sha256(client: httpx.Client, url: str) -> str | None:
    """Fetch SHA256 checksum from a .sha256 URL."""
    for attempt in range(1, 4):
        try:
            response = client.get(url)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            content = response.text.strip()
            return content.split()[0]
        except httpx.HTTPStatusError as e:
            if e.response.status_code in {502, 503, 504} and attempt < 3:
                time.sleep(2**attempt)
                continue
            return None
    return None


def fetch_release_published_at(
    client: httpx.Client, org: str, repo: str, tag: str
) -> str:
    """Fetch and normalize the GitHub release published_at timestamp."""
    response = client.get(
        f"https://api.github.com/repos/{org}/{repo}/releases/tags/{tag}",
        headers=build_github_headers(),
    )
    response.raise_for_status()

    published_at = response.json().get("published_at")
    if not isinstance(published_at, str) or not published_at:
        raise ValueError(
            f"GitHub release {org}/{repo}@{tag} did not include a published_at timestamp"
        )

    return normalize_timestamp(published_at)


def extract_github_info(manifest: dict[str, Any]) -> tuple[str, str, str]:
    """Extract GitHub org, repo, and app name from manifest.

    Returns:
        Tuple of (github_org, github_repo, app_name)
    """
    app_name = None

    for release in manifest.get("releases", []):
        app_name = release["app_name"]
        if "announcement_github_body" in manifest:
            match = re.search(
                r"https://github\.com/([^/]+)/([^/]+)/releases/download/",
                manifest["announcement_github_body"],
            )
            if match:
                return match.group(1), match.group(2), app_name
        break

    if app_name is None:
        raise ValueError("No releases found in manifest")

    return "astral-sh", app_name, app_name


def extract_version_info(
    manifest: dict[str, Any], client: httpx.Client
) -> dict[str, Any]:
    """Extract version information from cargo-dist manifest."""
    version = manifest["announcement_tag"]
    github_org, github_repo, app_name = extract_github_info(manifest)
    published_at = fetch_release_published_at(client, github_org, github_repo, version)
    artifacts_data = []

    for release in manifest.get("releases", []):
        if release["app_name"] == app_name:
            for artifact_name in release.get("artifacts", []):
                if (
                    not artifact_name.startswith(f"{app_name}-")
                    or artifact_name.endswith(".sha256")
                    or artifact_name == "source.tar.gz"
                    or artifact_name == "source.tar.gz.sha256"
                    or artifact_name == "sha256.sum"
                    or artifact_name.endswith(".sh")
                    or artifact_name.endswith(".ps1")
                ):
                    continue

                prefix_len = len(app_name) + 1
                if artifact_name.endswith(".tar.gz"):
                    platform = artifact_name[prefix_len:-7]
                elif artifact_name.endswith(".zip"):
                    platform = artifact_name[prefix_len:-4]
                else:
                    continue

                sha256_url = f"https://github.com/{github_org}/{github_repo}/releases/download/{version}/{artifact_name}.sha256"
                sha256 = fetch_sha256(client, sha256_url)
                if not sha256:
                    print(
                        f"Warning: Could not fetch SHA256 for {artifact_name}",
                        file=sys.stderr,
                    )
                    continue

                artifacts_data.append({
                    "platform": platform,
                    "variant": "default",
                    "url": f"https://github.com/{github_org}/{github_repo}/releases/download/{version}/{artifact_name}",
                    "archive_format": get_archive_format(artifact_name),
                    "sha256": sha256,
                })
            break

    artifacts_data.sort(key=lambda x: (x["platform"], x["variant"]))

    return {
        "version": version,
        "date": published_at,
        "artifacts": artifacts_data,
    }


def main() -> None:
    if sys.stdin.isatty():
        print("Error: expected cargo-dist plan JSON on stdin", file=sys.stderr)
        sys.exit(1)

    try:
        manifest = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON from stdin: {e}", file=sys.stderr)
        sys.exit(1)

    print("Extracting version information...", file=sys.stderr)
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        version_info = extract_version_info(manifest, client)

    print(
        f"Found version: {version_info['version']} with {len(version_info['artifacts'])} artifacts",
        file=sys.stderr,
    )
    print(json.dumps(version_info, separators=(",", ":")))


if __name__ == "__main__":
    main()
