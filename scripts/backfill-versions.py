#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx"]
# ///
"""Backfill historical versions from GitHub releases to NDJSON format.

Usage:
    # Backfill versions for a project (default: writes to ../v1/)
    backfill-versions.py <project-name>

    # Specify custom GitHub org/repo
    backfill-versions.py <project-name> --github astral-sh/uv

    # Specify custom output directory
    backfill-versions.py <project-name> --output <path>
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
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


PBS_FILENAME_RE = re.compile(
    r"""(?x)
    ^
        cpython-
        (?P<ver>\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?)(?:\+\d+)?\+
        (?P<date>\d+)-
        (?P<triple>[a-z\d_]+-[a-z\d]+(?>-[a-z\d]+)?-(?!debug(?:-|$))[a-z\d_]+)-
        (?:(?P<build_options>.+)-)?
        (?P<flavor>[a-z_]+)?
        \.tar\.(?:gz|zst)
    $
    """
)


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


def extract_platform_from_filename(filename: str, project_name: str) -> str | None:
    """Extract platform target triple from filename."""
    # Pattern: {project}-{platform}.{ext}
    pattern = rf"^{re.escape(project_name)}-(.+?)\.(tar\.gz|zip)$"
    match = re.match(pattern, filename)
    if match:
        return match.group(1)
    return None


def parse_github_datetime(value: str) -> datetime | None:
    """Parse GitHub ISO timestamps."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_sha256sums(text: str) -> dict[str, str]:
    """Parse SHA256SUMS content into a filename map."""
    checksums: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        checksum, filename = parts
        filename = filename.lstrip("*")
        checksums[filename] = checksum
    return checksums


def fetch_sha256_file(client: httpx.Client, url: str) -> str | None:
    """Fetch a single SHA256 checksum from a .sha256 URL."""
    for attempt in range(1, 4):
        try:
            response = client.get(url)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            # SHA256 file contains just the hash (possibly with filename)
            content = response.text.strip()
            return content.split()[0]
        except httpx.HTTPStatusError as e:
            if e.response.status_code in {502, 503, 504} and attempt < 3:
                time.sleep(2**attempt)
                continue
            return None
    return None


def fetch_release_checksums(
    release: dict[str, Any], client: httpx.Client
) -> dict[str, str]:
    """Fetch all SHA256 checksums for a release.

    Tries SHA256SUMS file first, then individual .sha256 files.
    """
    checksums: dict[str, str] = {}
    assets = release.get("assets", [])

    # Try SHA256SUMS file first (used by PBS)
    for asset in assets:
        if asset.get("name") == "SHA256SUMS":
            url = asset.get("browser_download_url", "")
            if url:
                response = client.get(url)
                if response.status_code == 200:
                    checksums = parse_sha256sums(response.text)
                    if checksums:
                        return checksums

    # Fall back to individual .sha256 files
    for asset in assets:
        name = asset.get("name", "")
        if not name.endswith(".sha256"):
            continue
        base_name = name[:-7]  # Remove .sha256
        url = asset.get("browser_download_url", "")
        if not url:
            continue
        sha256 = fetch_sha256_file(client, url)
        if sha256:
            checksums[base_name] = sha256

    return checksums


def parse_pbs_asset_filename(filename: str) -> tuple[str, str, str] | None:
    """Parse python-build-standalone asset filename."""
    match = PBS_FILENAME_RE.match(filename)
    if match is None:
        return None
    triple = match.group("triple")
    build_options = match.group("build_options")
    flavor = match.group("flavor")
    python_version = match.group("ver")
    build_version = match.group("date")
    variant_parts: list[str] = []
    if build_options:
        variant_parts.extend(build_options.split("+"))
    if flavor:
        variant_parts.append(flavor)
    variant = "+".join(variant_parts) if variant_parts else ""
    version = f"{python_version}+{build_version}"
    return triple, variant, version


def fetch_github_releases(
    org: str,
    repo: str,
    per_page: int = 100,
    cutoff: datetime | None = None,
) -> list[dict[str, Any]]:
    """Fetch all releases from GitHub API."""
    releases = []
    page = 1

    # Build headers with GitHub token if available
    headers = {"Accept": "application/vnd.github.v3+json"}
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
        print("Using GITHUB_TOKEN for authentication", file=sys.stderr)
    else:
        print(
            "No GITHUB_TOKEN found, using unauthenticated requests (may hit rate limits)",
            file=sys.stderr,
        )

    with httpx.Client(timeout=30.0) as client:
        while True:
            print(f"Fetching page {page}...", file=sys.stderr)
            response = None
            for attempt in range(1, 4):
                response = client.get(
                    f"https://api.github.com/repos/{org}/{repo}/releases",
                    params={"per_page": per_page, "page": page},
                    headers=headers,
                )
                if response.status_code in {502, 503, 504} and attempt < 3:
                    time.sleep(2**attempt)
                    continue
                response.raise_for_status()
                break

            if response is None:
                raise RuntimeError("Failed to fetch releases from GitHub")
            data = response.json()
            if not data:
                break

            if cutoff is None:
                releases.extend(data)
                page += 1
                continue

            cutoff_reached = False
            for release in data:
                published_at = release.get("published_at", "")
                published_datetime = parse_github_datetime(published_at)
                if published_datetime and published_datetime < cutoff:
                    cutoff_reached = True
                    continue
                releases.append(release)

            if cutoff_reached:
                break

            page += 1

    return releases


def process_pbs_release(
    release: dict[str, Any], published_at: str, client: httpx.Client
) -> list[Version]:
    """Process python-build-standalone releases into our version format."""
    assets = release.get("assets", [])
    if not assets:
        return []

    checksums = fetch_release_checksums(release, client)
    artifacts_by_version: dict[str, list[Artifact]] = {}

    for asset in assets:
        name = asset.get("name", "")
        if name == "SHA256SUMS":
            continue
        if not name.startswith("cpython-") or not (
            name.endswith(".tar.gz") or name.endswith(".tar.zst")
        ):
            continue

        parsed = parse_pbs_asset_filename(name)
        if parsed is None:
            continue
        platform, variant, version = parsed

        browser_download_url = asset.get("browser_download_url", "")
        if not browser_download_url:
            continue

        sha256 = checksums.get(name)
        if not sha256:
            # Skip artifacts without checksum
            continue

        artifact: Artifact = {
            "platform": platform,
            "variant": variant,
            "url": browser_download_url,
            "archive_format": get_archive_format(name),
            "sha256": sha256,
        }
        artifacts_by_version.setdefault(version, []).append(artifact)

    if not artifacts_by_version:
        return []

    versions: list[Version] = []
    for version, artifacts in artifacts_by_version.items():
        artifacts.sort(key=lambda x: (x["platform"], x.get("variant", "")))
        versions.append(
            {
                "version": version,
                "date": published_at,
                "artifacts": artifacts,
            }
        )

    versions.sort(key=lambda v: v["version"], reverse=True)
    return versions


def process_release(
    release: dict[str, Any],
    project_name: str,
    org: str,
    repo: str,
    client: httpx.Client,
    cutoff: datetime | None,
) -> list[Version]:
    """Process a GitHub release into our version format."""
    # Skip pre-releases and drafts
    if release.get("prerelease") or release.get("draft"):
        return []

    tag_name = release.get("tag_name", "")
    published_at = release.get("published_at", "")
    assets = release.get("assets", [])

    # Skip if no tag or date
    if not tag_name or not published_at:
        return []

    published_datetime = parse_github_datetime(published_at)
    if cutoff and published_datetime and published_datetime < cutoff:
        return []

    if project_name == "python-build-standalone":
        return process_pbs_release(release, published_at, client)

    # Fetch all checksums for this release
    checksums = fetch_release_checksums(release, client)

    artifacts: list[Artifact] = []

    for asset in assets:
        name = asset.get("name", "")
        browser_download_url = asset.get("browser_download_url", "")

        # Skip non-binary assets
        if not name.startswith(f"{project_name}-") or not (
            name.endswith(".tar.gz") or name.endswith(".zip")
        ):
            continue

        # Skip checksum files
        if name.endswith(".sha256"):
            continue

        platform = extract_platform_from_filename(name, project_name)
        if not platform:
            continue

        sha256 = checksums.get(name)
        if not sha256:
            # Skip artifacts without checksum
            continue

        artifact: Artifact = {
            "platform": platform,
            "variant": "default",
            "url": browser_download_url,
            "archive_format": get_archive_format(name),
            "sha256": sha256,
        }
        artifacts.append(artifact)

    # Skip releases without artifacts
    if not artifacts:
        return []

    # Sort artifacts by platform and variant for consistency
    artifacts.sort(key=lambda x: (x["platform"], x["variant"]))

    return [
        {
            "version": tag_name,
            "date": published_at,
            "artifacts": artifacts,
        }
    ]


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Backfill historical versions from GitHub releases"
    )
    parser.add_argument("project_name", help="Project name (e.g., 'uv', 'ruff')")
    parser.add_argument(
        "--github",
        help="GitHub org/repo (default: astral-sh/{project_name})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output directory (default: ../v1/ relative to this script)",
    )
    args = parser.parse_args()

    project_name = args.project_name

    # Calculate the output directory
    if args.output:
        versions_repo = args.output
    else:
        # Default: script is in versions/scripts/, output to versions/v1/
        script_dir = Path(__file__).parent
        versions_repo = script_dir.parent / "v1"

    # Ensure versions directory exists
    versions_repo.mkdir(parents=True, exist_ok=True)

    # Parse GitHub org/repo
    if args.github:
        org_repo = args.github
        if "/" not in org_repo:
            print("Error: --github must be in format 'org/repo'", file=sys.stderr)
            sys.exit(1)
        org, repo = org_repo.split("/", 1)
    else:
        # Default to astral-sh/{project_name}
        org = "astral-sh"
        repo = project_name

    versions_file = versions_repo / f"{project_name}.ndjson"

    cutoff: datetime | None = None
    per_page = 100
    if project_name == "python-build-standalone":
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        per_page = 20

    # Fetch all releases
    print(f"Fetching releases from GitHub {org}/{repo}...", file=sys.stderr)
    releases = fetch_github_releases(org, repo, per_page=per_page, cutoff=cutoff)
    print(f"Found {len(releases)} releases", file=sys.stderr)

    # Process releases
    versions: list[Version] = []
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for release in releases:
            release_versions = process_release(
                release, project_name, org, repo, client, cutoff
            )
            for version in release_versions:
                print(f"Processed version: {version['version']}", file=sys.stderr)
                versions.append(version)

    # Sort by date (newest first)
    versions.sort(key=lambda v: v["date"], reverse=True)

    print(f"Processed {len(versions)} valid versions", file=sys.stderr)

    # Ensure parent directory exists
    versions_file.parent.mkdir(parents=True, exist_ok=True)

    # Write to file in NDJSON format
    print(f"Writing to {versions_file}...", file=sys.stderr)
    with open(versions_file, "w") as f:
        # Write each version as a separate line
        for version in versions:
            f.write(json.dumps(version, separators=(",", ":")) + "\n")

    print("Done!", file=sys.stderr)


if __name__ == "__main__":
    main()
