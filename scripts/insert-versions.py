#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# ///
"""Insert version objects into an NDJSON versions file.

Reads NDJSON from stdin (one version object per line) and prepends them
to the target file, deduplicating by version string.

Usage:
    echo '{"version":"1.0.0","date":"...","artifacts":[...]}' | insert-versions.py --name uv
    uv run generate-version-metadata.py | insert-versions.py --name python-build-standalone
"""

import argparse
import json
import sys
from pathlib import Path

REQUIRED_ARTIFACT_KEYS = {"platform", "variant", "url", "archive_format", "sha256"}
VALID_ARCHIVE_FORMATS = {"tar.gz", "tar.zst", "zip"}


def validate_version(entry: dict) -> list[str]:
    """Validate a version entry against the expected schema.

    Returns a list of error messages (empty if valid).
    """
    errors = []

    if not isinstance(entry.get("version"), str) or not entry["version"]:
        errors.append("missing or empty 'version'")

    if not isinstance(entry.get("date"), str) or not entry["date"]:
        errors.append("missing or empty 'date'")

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

    # Deduplicate: remove existing entries that match incoming version strings
    incoming_version_strings = {v["version"] for v in new_versions}
    existing = [v for v in existing if v["version"] not in incoming_version_strings]

    # Prepend new versions
    versions = new_versions + existing

    # Write compact NDJSON
    with open(versions_path, "w") as f:
        for version in versions:
            f.write(json.dumps(version, separators=(",", ":")) + "\n")

    if len(new_versions) == 1:
        print(f"Inserted version {new_versions[0]['version']} into {versions_path}", file=sys.stderr)
    else:
        print(f"Inserted {len(new_versions)} versions into {versions_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
