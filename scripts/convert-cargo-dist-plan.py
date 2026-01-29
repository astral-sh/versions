#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx"]
# ///
"""Convert cargo-dist plan JSON to a version NDJSON line.

Reads `cargo dist plan --output-format=json` from stdin and outputs
a single NDJSON line to stdout.

Usage:
    cargo dist plan --output-format=json | convert-cargo-dist-plan.py
"""

import json
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
        "date": datetime.now(timezone.utc).isoformat(),
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
