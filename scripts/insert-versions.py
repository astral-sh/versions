#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# ///
"""Insert version objects into an NDJSON versions file.

Reads NDJSON from stdin (one version object per line), merges it into the target
file, deduplicates by version string, normalizes timestamps, and keeps versions
sorted newest-first.

Usage:
    echo '{"version":"1.0.0","date":"...","artifacts":[...]}' | insert-versions.py --name uv
    uv run generate-version-metadata.py | insert-versions.py --name python-build-standalone
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_ARTIFACT_KEYS = {"platform", "variant", "url", "archive_format", "sha256"}
VALID_ARCHIVE_FORMATS = {"tar.gz", "tar.zst", "zip"}


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


def normalize_versions_in_place(versions: list[dict[str, Any]]) -> None:
    """Normalize all version dates in-place."""
    for version in versions:
        raw_date = version.get("date")
        if not isinstance(raw_date, str) or not raw_date:
            raise ValueError(
                f"version {version.get('version', '<unknown>')!r} is missing a valid date"
            )
        version["date"] = normalize_timestamp(raw_date)


def sort_versions_desc(versions: list[dict[str, Any]]) -> None:
    """Sort versions newest-first using parsed timestamps."""
    versions.sort(key=lambda version: parse_timestamp(version["date"]), reverse=True)


def validate_version(entry: dict[str, Any]) -> list[str]:
    """Validate a version entry against the expected schema.

    Returns a list of error messages (empty if valid).
    """
    errors = []

    if not isinstance(entry.get("version"), str) or not entry["version"]:
        errors.append("missing or empty 'version'")

    raw_date = entry.get("date")
    if not isinstance(raw_date, str) or not raw_date:
        errors.append("missing or empty 'date'")
    else:
        try:
            normalize_timestamp(raw_date)
        except ValueError as e:
            errors.append(f"invalid 'date': {e}")

    artifacts = entry.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("missing or empty 'artifacts'")
        return errors

    for i, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            errors.append(f"artifact[{i}]: not an object")
            continue

        missing = REQUIRED_ARTIFACT_KEYS - artifact.keys()
        if missing:
            errors.append(f"artifact[{i}]: missing keys {sorted(missing)}")
            continue

        if artifact["archive_format"] not in VALID_ARCHIVE_FORMATS:
            errors.append(
                f"artifact[{i}]: invalid archive_format {artifact['archive_format']!r}"
            )

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Insert version objects into an NDJSON versions file"
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Project name (determines output file <name>.ndjson)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output directory (default: ../v1/ relative to this script)",
    )
    args = parser.parse_args()

    if sys.stdin.isatty():
        print("Error: expected NDJSON on stdin", file=sys.stderr)
        sys.exit(1)

    # Parse and validate incoming versions from stdin
    new_versions = []
    for lineno, line in enumerate(sys.stdin, 1):
        line = line.strip()
        if line:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"Error parsing line {lineno}: {e}", file=sys.stderr)
                sys.exit(1)

            errors = validate_version(entry)
            if errors:
                print(
                    f"Validation error on line {lineno}: {'; '.join(errors)}",
                    file=sys.stderr,
                )
                sys.exit(1)

            new_versions.append(entry)

    if not new_versions:
        print("No versions provided on stdin", file=sys.stderr)
        sys.exit(1)

    normalize_versions_in_place(new_versions)

    # Sort artifacts within each version by (platform, variant)
    for version in new_versions:
        version["artifacts"].sort(key=lambda a: (a["platform"], a["variant"]))

    # Determine output path
    if args.output:
        output_dir = args.output
    else:
        script_dir = Path(__file__).parent
        output_dir = script_dir.parent / "v1"

    output_dir.mkdir(parents=True, exist_ok=True)
    versions_path = output_dir / f"{args.name}.ndjson"

    # Load existing versions
    existing = []
    if versions_path.exists():
        with open(versions_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        existing.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

    try:
        normalize_versions_in_place(existing)
    except ValueError as e:
        print(
            f"Error normalizing existing versions in {versions_path}: {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Deduplicate: remove existing entries that match incoming version strings
    incoming_version_strings = {v["version"] for v in new_versions}
    existing = [v for v in existing if v["version"] not in incoming_version_strings]

    # Merge and sort newest-first
    versions = new_versions + existing
    sort_versions_desc(versions)

    # Write compact NDJSON
    with open(versions_path, "w") as f:
        for version in versions:
            f.write(json.dumps(version, separators=(",", ":")) + "\n")

    if len(new_versions) == 1:
        print(
            f"Inserted version {new_versions[0]['version']} into {versions_path}",
            file=sys.stderr,
        )
    else:
        print(
            f"Inserted {len(new_versions)} versions into {versions_path}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
