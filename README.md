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
| `url` | `string` | **Optional.** Direct download URL for the artifact. Exactly one of `url` or `archive_filename` must be present. |
| `archive_filename` | `string` | **Optional.** Filename of the archive (e.g. `"uv-aarch64-apple-darwin.tar.gz"`). Exactly one of `url` or `archive_filename` must be present. Clients resolve the full URL using `mirrors.json`. |
| `archive_format` | `string` | **Required.** One of `"tar.gz"`, `"tar.zst"`, or `"zip"`. |
| `sha256` | `string` | **Required.** SHA-256 checksum of the artifact. |

### Example

```json
{
  "version": "0.8.3",
  "date": "2025-07-29T16:45:46.646976+00:00",
  "artifacts": [
    {
      "platform": "aarch64-apple-darwin",
      "variant": "default",
      "archive_filename": "uv-aarch64-apple-darwin.tar.gz",
      "archive_format": "tar.gz",
      "sha256": "fcf0a9ea6599c6ae..."
    }
  ]
}
```

### Mirrors

The file `v1/mirrors.json` contains an array of mirror objects that clients use to resolve `archive_filename` values into full download URLs:

```json
[
  {"url_template": "https://github.com/astral-sh/{project}/releases/download/{version}/"}
]
```

Each mirror has a `url_template` field with `{project}` and `{version}` placeholders. To resolve a download URL, clients substitute the placeholders and append the `archive_filename`. For example, given the template above and an artifact with `archive_filename: "uv-aarch64-apple-darwin.tar.gz"` from project `uv` version `0.8.3`, the resolved URL is:

```
https://github.com/astral-sh/uv/releases/download/0.8.3/uv-aarch64-apple-darwin.tar.gz
```

Artifacts with a `url` field already contain the full download URL and do not need mirror resolution.

## Adding versions

Use `insert-versions.py` to add versions. It reads NDJSON in the above format from stdin and inserts
them into the target file, deduplicating by version string and ensuring the proper insertion order.

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
