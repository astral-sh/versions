# Astral versions

Tracks release metadata for Astral products.

## Format

Release metadata is stored in versioned ndjson files:

- `v1/` - The version of the schema
  - `<project>.ndjson` - The release metadata for a given project

Each line in the NDJSON files represents one release, e.g.:

```json
{
  "version": "0.8.3",
  "date": "2025-07-29T16:45:46.646976+00:00",
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
