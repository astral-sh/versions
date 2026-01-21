# Astral versions

Tracks release metadata for Astral products.


## Format

Release metadata is stored in versioned ndjson files:

- `v1/` - Version
  - `<name>.ndjson` - Release metadata

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

## Usage

### Publishing a new version

The publish script supports multiple input formats.

For `cargo-dist` projects:

```bash
cargo dist plan --output-format=json | uv run scripts/publish-version.py --format cargo-dist
```

For projects that emit a custom JSON payload (default format):

```bash
cat payload.json | uv run scripts/publish-version.py --name <project-name>
```

Payloads can include a top-level `versions` list to publish multiple versions at once.

### Backfilling historical versions

There's a backfill utility which pulls releases and artifacts from GitHub:

```bash
uv run scripts/backfill-versions.py <name>
```
