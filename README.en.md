# kemo-plugin-market

<p align="center">
  <a href="README.md">简体中文</a> · <strong>English</strong>
</p>

<p align="center">
  <strong>Index generation and static publishing for the Kemo gateway plugin market.</strong>
</p>

<p align="center">
  Turn a directory of plugin packages into an index the gateway can consume,<br>
  then serve it from a small read-only service.
</p>

<p align="center">
  <a href="version.json"><img src="https://img.shields.io/badge/version-1.0.0-00a98f" alt="version 1.0.0"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10%2B-3776ab" alt="python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-green.svg" alt="license"></a>
</p>

---

## The problem this solves

The Kemo gateway lets you point a "plugin source" at an index URL. The gateway fetches that JSON and renders whatever it lists as a plugin marketplace. The index itself is just a static file, but several non-obvious constraints decide whether the gateway can actually consume it:

- The gateway's HTTP client is pinned to `follow_redirects=False`, so **any 3xx marks the source as unavailable**. GitHub Release assets always 302, which rules them out as download URLs.
- Index entries use a strict schema where a single extra field invalidates the whole source (`extra="forbid"`). `sha256` must be lowercase, and `version` must parse as PEP 440.
- If the reverse proxy renders its own HTML for a 404 and returns 200, the gateway only reports "index unavailable or malformed" and points you in the wrong direction entirely.

This repository turns those constraints into code and tests: how the index is built, how the service must respond, and what the reverse proxy must not do.

## What it does

| Capability | Description |
|---|---|
| Index generation | Scans `packages/*.zip`, reads id / name / version / summary from each `plugin.yaml`, computes SHA-256, writes `index.json` |
| Schema validation | Validates every field against the gateway's strict shape — unknown fields, uppercase digests, invalid versions and duplicates are rejected locally |
| Static service | Read-only; never redirects, never lists directories, only serves the index and packages, changes take effect immediately |
| End-to-end verification | `verify` runs a published URL through four checks: index reachable, structure valid, package downloadable, digest matches |
| One-command deploy | systemd unit, install script and reverse-proxy snippet; binds to loopback only |

## Quick start

### Install

```bash
git clone https://github.com/kesepain-KE/kemo-plugin-market.git
cd kemo-plugin-market
pip install -r requirements.txt
```

### Prepare the market root

```bash
mkdir -p /srv/kemo-plugin-market/packages
cp examples/market.yaml /srv/kemo-plugin-market/market.yaml
```

Edit `market.yaml` and set `base_url` to the publicly reachable site root.

### Drop in plugin packages

Packages must follow Kemo Advanced Plugin SDK v1: `plugin.yaml` sits at the ZIP root, with no extra directory level. Packaging through the gateway's own CLI is safest:

```bash
python -m gateway.plugins check <plugin-dir>
python -m gateway.plugins pack  <plugin-dir> dsh-model-system-1.1.3.zip
cp dsh-model-system-1.1.3.zip /srv/kemo-plugin-market/packages/
```

### Build the index

```bash
python -m kemo_plugin_market build --root /srv/kemo-plugin-market
```

It lists each entry's id, version and digest prefix, then writes `index.json` atomically. Use `--dry-run` to preview, or `--check` to validate without writing.

### Run the service

```bash
python -m kemo_plugin_market serve --root /srv/kemo-plugin-market --port 8099
```

It binds to `127.0.0.1` only. For production:

```bash
sudo ./deploy/install.sh
```

### Wire up the reverse proxy

Place the `location` block from `deploy/nginx-location.conf` into the relevant server block. Three lines are mandatory:

```nginx
proxy_intercept_errors off;   # otherwise a 404 becomes an HTML 200
proxy_buffering off;          # the index must be live
# no try_files fallback, no directory index
```

### Add the source in the gateway

Enter the index URL in the admin UI:

```
https://your-domain/index.json
```

For private IPs or `localhost`, tick "allow private network" on that source.

### Verify

You can validate without opening the gateway:

```bash
python -m kemo_plugin_market verify --url https://your-domain
```

```
[1] index reachable: 635 bytes  https://your-domain/index.json
[2] structure valid, 1 entries
[3] dsh-model-system v1.1.3 34979 bytes, digest matches

OK: 1 entry usable
```

## Commands

| Command | Purpose |
|---|---|
| `build` | Scan packages and generate the index |
| `build --check` | Validate only, write nothing |
| `build --dry-run` | Print the index, write nothing |
| `serve` | Start the static index service |
| `verify --url <url>` | End-to-end check of a published source |

## Index format

```json
{
  "schema_version": 1,
  "plugins": [
    {
      "id": "dsh-model-system",
      "name": "DSH Model System",
      "version": "1.1.3",
      "url": "https://your-domain/packages/dsh-model-system-1.1.3.zip",
      "sha256": "47c57bca…c51e",
      "summary": "Runs DSH model ecosystem plugins…",
      "author": "Kemo Gateway Local",
      "category": "provider_extension",
      "capabilities": ["llm"],
      "compatibility": ">=2.0.0,<3.0.0"
    }
  ]
}
```

Field constraints mirror the gateway's `CatalogEntry`:

| Field | Required | Constraint |
|---|---|---|
| `id` | yes | `^[a-z0-9][a-z0-9_-]{0,63}$` |
| `name` | yes | 1–100 chars |
| `version` | yes | valid PEP 440, ≤60 chars |
| `url` | yes | HTTP(S) direct link, ≤2000 chars, no query / fragment / credentials |
| `sha256` | yes | 64 lowercase hex chars, the ZIP file's byte digest |
| `summary` | no | ≤1000 chars |
| `author` | no | ≤200 chars |
| `category` | no | `model_account` / `cli_provider` / `provider_extension` |
| `capabilities` | no | ≤32 items, ≤100 chars each |
| `compatibility` | no | ≤100 chars |

Two additional limits apply to the whole document: ≤2 MiB and ≤500 entries.

## Configuration

`market.yaml` only describes how the market is published, never what a plugin is:

```yaml
base_url: https://plugin-gateway.kesepain.com
packages_dir: packages

# Optional: per-plugin overrides for market metadata
overrides:
  some_plugin:
    category: model_account
    capabilities: [llm, tools]
    summary: overrides the manifest summary
```

Each package URL is assembled as `<base_url>/packages/<filename>.zip`, so **the filename is part of the download URL** — rename a package and rebuild.

## Field precedence

```
explicit overrides  >  package plugin.yaml  >  built-in defaults
```

- `capabilities` defaults to `llm` when the plugin exposes a provider, otherwise empty
- `category` comes from the manifest, falling back to `provider_extension`
- `compatibility` uses the manifest's `compatibility.gateway`, defaulting to `>=2.0.0,<3.0.0`

## Deployment shapes

| Location | Example source URL | Private network flag |
|---|---|---|
| Cloud + domain | `https://plugins.example.com/index.json` | not needed |
| Internal server | `http://192.168.1.10:8099/index.json` | required |
| Local testing | `http://127.0.0.1:8099/index.json` | required |

Self-signed certificates will not work on internal hosts: the gateway pins `verify=True`.

## On "private use only"

The gateway's plugin source configuration exposes **no authentication hooks**: no custom headers, no query strings, no credentials in the URL. Keeping a market private therefore depends on external measures:

- an IP allowlist at the reverse proxy (residential IPs change, and you get a 403)
- an unguessable path prefix
- keeping it off the public internet entirely

The `sha256` in the index guarantees that a downloaded package matches what the index promised. It does **not** protect the index from being read.

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

The tests do not depend on the gateway repository; they construct minimal SDK v1 packages locally.

## Layout

```
kemo_plugin_market/
├── schema.py      entry and document validation
├── manifest.py    read plugin.yaml from a ZIP
├── config.py      market.yaml parsing
├── builder.py     scan → generate index
├── server.py      static read-only service
└── cli.py         command line entry point
deploy/
├── install.sh            one-command install
├── kemo-market.service   systemd unit
└── nginx-location.conf   reverse proxy snippet
tests/                    schema, builder and server tests
```

## Related projects

| Project | Description |
|---|---|
| [kemo-agent](https://github.com/kesepain-KE/kemo-agent) | The agent framework itself |
| [kemo-adapter-api](https://github.com/kesepain-KE/kemo-adapter-api) | The Kemo gateway that consumes this market |
| [kemo-graph](https://github.com/kesepain-KE/kemo-graph) | Local knowledge graph infrastructure |
| [kemo-agent-app](https://github.com/kesepain-KE/kemo-agent-app) | Android client |

## License

[Apache-2.0](LICENSE)
