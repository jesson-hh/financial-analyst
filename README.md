# Financial Analyst

A-share single-stock deep-dive multi-agent research workstation.

Three-tier trust isolation: data fetchers → analysts → decision makers. Only `report-writer` has write permission. Untrusted news/F10 sources go through schema-validated readers.

**Status:** v0.1 in development. See [docs/superpowers/specs/](docs/superpowers/specs/) for the design spec.

## Install

```bash
pip install -e .[dev]
```

## License

Apache 2.0
