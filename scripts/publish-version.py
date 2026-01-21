#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx"]
# ///
"""Update versions NDJSON file from release metadata.

Usage:
    # Run cargo dist and update versions
    publish-versions.py --format cargo-dist

    # Pipe in cargo-dist manifest JSON
    cat manifest.json | publish-versions.py --format cargo-dist

    # Pipe in JSON payload metadata (default format)
    cat payload.json | publish-versions.py --name python-build-standalone

    # Payloads can include a list of versions
    cat payload-with-versions.json | publish-versions.py --name python-build-standalone

    # Optionally specify custom output directory
    publish-versions.py --output <path>
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NotRequired, TypedDict

import httpx


class Artifact(TypedDict):
    platform: str
    variant: str
    url: str
    archive_format: str
    sha256: str


class Version(TypedDict):
    version: str
    date: str
    artifacts: list[Artifact]


class VersionsFile(TypedDict):
    versions: list[Version]


class Payload(TypedDict, total=False):
    name: str
    version: str
    date: str
    artifacts: list[Artifact]
    versions: list[Version]


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


def run_cargo_dist_plan() -> dict[str, Any]:
    """Run cargo dist plan and return the manifest."""
    try:
        result = subprocess.run(
            ["cargo", "dist", "plan", "--output-format=json"],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error running cargo dist plan: {e}", file=sys.stderr)
        print(f"stderr: {e.stderr}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error parsing cargo dist output: {e}", file=sys.stderr)
        sys.exit(1)


def extract_github_info(manifest: dict[str, Any]) -> tuple[str, str, str]:
    """Extract GitHub org, repo, and app name from manifest.

    Returns:
        Tuple of (github_org, github_repo, app_name)
    """
    # Find the first release to get app_name and artifacts
    app_name = None

    for release in manifest.get("releases", []):
        app_name = release["app_name"]
        # Look for a download URL in the announcement body to extract org/repo
        if "announcement_github_body" in manifest:
            # Extract from download URLs in the body
            match = re.search(
                r"https://github\.com/([^/]+)/([^/]+)/releases/download/",
                manifest["announcement_github_body"],
            )
            if match:
                return match.group(1), match.group(2), app_name
        break

    if app_name is None:
        raise ValueError("No releases found in manifest")

    # Fallback: assume astral-sh org
    return "astral-sh", app_name, app_name


def extract_version_info(
    manifest: dict[str, Any], client: httpx.Client
) -> tuple[Version, str]:
    """Extract version information from cargo-dist manifest.

    Returns:
        Tuple of (Version dict, app_name)
    """
    version = manifest["announcement_tag"]
    github_org, github_repo, app_name = extract_github_info(manifest)
    artifacts_data: list[Artifact] = []

    # Get the artifacts for the release
    for release in manifest.get("releases", []):
        if release["app_name"] == app_name:
            # Process each artifact name from the list
            for artifact_name in release.get("artifacts", []):
                # Skip non-binary artifacts
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

                # Extract platform from filename (e.g., "uv-aarch64-apple-darwin.tar.gz")
                # Remove app name prefix and file extension
                prefix_len = len(app_name) + 1  # +1 for the dash
                if artifact_name.endswith(".tar.gz"):
                    platform = artifact_name[
                        prefix_len:-7
                    ]  # Remove prefix and ".tar.gz"
                elif artifact_name.endswith(".zip"):
                    platform = artifact_name[prefix_len:-4]  # Remove prefix and ".zip"
                else:
                    continue

                # Fetch SHA256 checksum
                sha256_url = f"https://github.com/{github_org}/{github_repo}/releases/download/{version}/{artifact_name}.sha256"
                sha256 = fetch_sha256(client, sha256_url)
                if not sha256:
                    print(
                        f"Warning: Could not fetch SHA256 for {artifact_name}",
                        file=sys.stderr,
                    )
                    continue

                # Build artifact entry
                artifact: Artifact = {
                    "platform": platform,
                    "variant": "default",
                    "url": f"https://github.com/{github_org}/{github_repo}/releases/download/{version}/{artifact_name}",
                    "archive_format": get_archive_format(artifact_name),
                    "sha256": sha256,
                }
                artifacts_data.append(artifact)
            break

    # Sort artifacts by platform and variant for consistency
    artifacts_data.sort(key=lambda x: (x["platform"], x["variant"]))

    version_info: Version = {
        "version": version,
        "date": datetime.now(timezone.utc).isoformat(),
        "artifacts": artifacts_data,
    }

    return version_info, app_name


def normalize_payload_version(raw: dict[str, Any]) -> Version:
    """Normalize a raw payload version entry."""
    version = raw.get("version")
    if not version:
        raise ValueError("Payload version missing required 'version'")

    raw_artifacts = raw.get("artifacts", [])
    if not raw_artifacts:
        raise ValueError("Payload version missing required 'artifacts'")

    date = raw.get("date") or datetime.now(timezone.utc).isoformat()

    # Ensure all artifacts have variant set (default to "default")
    artifacts: list[Artifact] = []
    for raw_artifact in raw_artifacts:
        artifact: Artifact = {
            "platform": raw_artifact["platform"],
            "variant": raw_artifact.get("variant", "default"),
            "url": raw_artifact["url"],
            "archive_format": raw_artifact["archive_format"],
            "sha256": raw_artifact["sha256"],
        }
        artifacts.append(artifact)

    artifacts.sort(key=lambda a: (a["platform"], a["variant"]))

    return {
        "version": version,
        "date": date,
        "artifacts": artifacts,
    }


def extract_payload_versions(
    payload: Payload, name_override: str | None
) -> tuple[list[Version], str]:
    """Extract version information from a JSON payload."""
    name = name_override or payload.get("name")
    if not name:
        raise ValueError("Payload missing required 'name'")

    if payload.get("versions"):
        versions_data = payload.get("versions") or []
        versions = [normalize_payload_version(dict(entry)) for entry in versions_data]
    else:
        versions = [normalize_payload_version(dict(payload))]

    versions.sort(key=lambda version: version["version"], reverse=True)
    return versions, name


def update_versions_file(
    versions_path: Path, new_version: Version, max_versions: int = 100
) -> None:
    """Update the versions NDJSON file with a new version."""
    update_versions_file_batch(versions_path, [new_version], max_versions=max_versions)


def update_versions_file_batch(
    versions_path: Path, new_versions: list[Version], max_versions: int = 100
) -> None:
    """Update the versions NDJSON file with new versions."""
    versions = []

    # Load existing versions if file exists
    if versions_path.exists():
        try:
            with open(versions_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        versions.append(json.loads(line))

        except (json.JSONDecodeError, KeyError) as e:
            print(
                f"Warning: Could not parse existing versions file: {e}", file=sys.stderr
            )
            versions = []

    incoming_versions = {version["version"] for version in new_versions}
    if incoming_versions:
        versions = [
            version
            for version in versions
            if version["version"] not in incoming_versions
        ]

    for version in new_versions:
        versions.insert(0, version)

    # Keep only the most recent versions
    versions = versions[:max_versions]

    # Ensure parent directory exists
    versions_path.parent.mkdir(parents=True, exist_ok=True)

    # Write back to file in NDJSON format
    with open(versions_path, "w") as f:
        # Write each version as a separate line
        for version in versions:
            f.write(json.dumps(version, separators=(",", ":")) + "\n")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Update product version files from release metadata"
    )
    parser.add_argument(
        "--format",
        choices=("cargo-dist", "json"),
        default="json",
        help="Input format (default: json)",
    )
    parser.add_argument(
        "--name",
        help="Project name when using --format json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output directory (default: ../v1/ relative to this script)",
    )
    args = parser.parse_args()

    # Calculate the output directory
    if args.output:
        versions_repo = args.output
    else:
        # Default: script is in versions/scripts/, output to versions/v1/
        script_dir = Path(__file__).parent
        versions_repo = script_dir.parent / "v1"

    # Ensure versions directory exists
    versions_repo.mkdir(parents=True, exist_ok=True)

    version_entries: list[Version]
    app_name: str

    if args.format == "cargo-dist":
        # Get cargo dist manifest
        if sys.stdin.isatty():
            # No piped input, run cargo dist
            print("Running cargo dist plan...", file=sys.stderr)
            manifest = run_cargo_dist_plan()
        else:
            # Read from stdin
            print("Reading manifest from stdin...", file=sys.stderr)
            try:
                manifest = json.load(sys.stdin)
            except json.JSONDecodeError as e:
                print(f"Error parsing JSON from stdin: {e}", file=sys.stderr)
                sys.exit(1)

        # Extract version info and app name (fetches SHA256 checksums)
        print("Extracting version information...", file=sys.stderr)
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            version_info, app_name = extract_version_info(manifest, client)
        version_entries = [version_info]
    else:
        if sys.stdin.isatty():
            print("Error: --format json expects JSON on stdin", file=sys.stderr)
            sys.exit(1)
        print("Reading payload from stdin...", file=sys.stderr)
        try:
            payload = json.load(sys.stdin)
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON from stdin: {e}", file=sys.stderr)
            sys.exit(1)
        try:
            version_entries, app_name = extract_payload_versions(payload, args.name)
        except ValueError as e:
            print(f"Error parsing payload: {e}", file=sys.stderr)
            sys.exit(1)
    print(f"Found app: {app_name}", file=sys.stderr)
    if len(version_entries) == 1:
        print(f"Found version: {version_entries[0]['version']}", file=sys.stderr)
        print(
            f"Found {len(version_entries[0]['artifacts'])} artifacts",
            file=sys.stderr,
        )
    else:
        print(f"Found {len(version_entries)} versions", file=sys.stderr)

    # Determine versions file path based on app name
    versions_file = versions_repo / f"{app_name}.ndjson"

    # Update versions file
    print(f"Updating {versions_file}...", file=sys.stderr)
    update_versions_file_batch(versions_file, version_entries)

    print("Done!", file=sys.stderr)


if __name__ == "__main__":
    main()
