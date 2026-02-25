# Astral versions

Tracks release metadata for Astral products.

## Format

Release metadata is stored in versioned ndjson files:

- `v1/` - The version of the schema
  - `<project>.ndjson` - The release metadata for a given project

Each line in the NDJSON files represents one release. Releases are ordered newest-first, and
artifacts within each release are sorted by `(platform, variant)`.

### Schema

Each line is a JSON object with the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `version` | `string` | **Required.** Version identifier. For most projects this is a semver string (e.g. `"0.10.6"`). For python-build-standalone this is `"<python_version>+<build_date>"` (e.g. `"3.15.0a6+20260211"`). |
| `date` | `string` | **Required.** ISO 8601 timestamp of the release (e.g. `"2026-02-25T00:30:35.281018+00:00"`). |
| `artifacts` | `array` | **Required.** Non-empty list of artifact objects. |

Each artifact object has the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `platform` | `string` | **Required.** Target triple (e.g. `"aarch64-apple-darwin"`, `"x86_64-pc-windows-msvc"`, `"x86_64-unknown-linux-gnu"`). |
| `variant` | `string` | **Required.** Build variant. `"default"` for standard builds (uv, ruff). For python-build-standalone, describes the build configuration (e.g. `"install_only"`, `"install_only_stripped"`, `"pgo+lto+full"`, `"freethreaded+debug+full"`). |
| `url` | `string` | **Required.** Direct download URL for the artifact. |
| `archive_format` | `string` | **Required.** One of `"tar.gz"`, `"tar.zst"`, or `"zip"`. |
| `sha256` | `string` | **Required.** SHA-256 checksum of the artifact. |

### Example

```json
{
  "version": "0.8.3",
  "date": "2025-07-29T16:45:46Z",
  "artifacts": [
    {
      "platform": "aarch64-apple-darwin",
      "variant": "default",
      "url": "https://github.com/astral-sh/uv/releases/download/0.8.3/uv-aarch64-apple-darwin.tar.gz",
      "archive_format": "tar.gz",
      "sha256": "fcf0a9ea6599c6ae..."
    }
  ]
}
```

## Adding versions

Use `insert-versions.py` to add versions. It reads NDJSON in the above format from stdin and merges
them into the target file, deduplicating by version string, normalizing timestamps, and keeping the
file sorted newest-first.

```bash
echo '{"version":"1.0.0","date":"...","artifacts":[...]}' | uv run scripts/insert-versions.py --name uv
```

For convenience, there's support for converting `cargo-dist` plans into the NDJSON format. The
SHA256 checksums are fetched from GitHub.

```bash
cargo dist plan --output-format=json | uv run scripts/convert-cargo-dist-plan.py | uv run scripts/insert-versions.py --name uv
```

There's also backfill utility which pulls releases and artifacts from GitHub and adds them to the
registry.

```bash
uv run scripts/backfill-versions.py <name>
```
