# Installed-artifact qualification

Python 3.12 on Linux x86-64. Each source distribution and wheel was installed in an isolated uv environment with shared cached dependencies. No private package or database credential was supplied. The stdio client initialized the installed entrypoint and called the tools below on synthetic inputs.

These are targeted packaging and representative domain checks, not a full numerical or cross-platform qualification. Historical broader test counts are in [HISTORICAL-QUALITY.md](HISTORICAL-QUALITY.md).

## Source installation

MCP discovery: PASS; 23 tools.

| Check | Tool | Result |
|---|---|---|
| langelier-chemistry | `corrosion_langelier_index` | PASS |
| negative-analysis-rejection | `corrosion_langelier_index` | PASS |
| native-library-status | `corrosion_get_phreeqc_status` | KNOWN ISSUE |

## Wheel installation

MCP discovery: PASS; 23 tools.

| Check | Tool | Result |
|---|---|---|
| langelier-chemistry | `corrosion_langelier_index` | PASS |
| negative-analysis-rejection | `corrosion_langelier_index` | PASS |
| native-library-status | `corrosion_get_phreeqc_status` | KNOWN ISSUE |
