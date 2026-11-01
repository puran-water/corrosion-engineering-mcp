# corrosion-engineering-mcp

Corrosion chemistry and explicit screening calculations, with unsupported predictions identified in results.

Experimental v0.1.0. These tools return calculations and structured evidence; the calling LLM supplies explanations and reporting. Engineering review remains necessary.

## Quick start

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and run with Python 3.12:

```sh
uvx --python 3.12 --with 'puran-mcp-support @ git+https://github.com/puran-water/fluids-mcp.git@v0.1.0#subdirectory=support/puran-mcp-support' --from 'git+https://github.com/puran-water/corrosion-engineering-mcp.git@v0.1.0' corrosion-engineering-mcp
```

The server uses local MCP stdio. It requires no Puran account or private package. uv shares its dependency cache between isolated tool environments. PyPI publishing has not been configured; the pinned public Git release is the installation path.

## MCP client configuration

Use this configuration in a client supporting local stdio MCP servers:

```json
{
  "mcpServers": {
    "corrosion-engineering-mcp": {
      "command": "uvx",
      "args": [
        "--python",
        "3.12",
        "--with",
        "puran-mcp-support @ git+https://github.com/puran-water/fluids-mcp.git@v0.1.0#subdirectory=support/puran-mcp-support",
        "--from",
        "git+https://github.com/puran-water/corrosion-engineering-mcp.git@v0.1.0",
        "corrosion-engineering-mcp"
      ]
    }
  }
}
```

Run the server in a user-owned working directory. Local input and result files belong to that user. Hosted HTTP authentication and managed employee access are outside this release.

## Known issues

- **Restore data-backed tools using cleared redistributable data.** Material, handbook and thermodynamic tables without fully cleared redistribution provenance are omitted. The public material catalog is empty. Tools requiring these tables cannot provide material-specific results; this release supplies no invented replacement values.
- **Validate suppressed and experimental corrosion models.** Several historical rate and suitability outputs are withdrawn or suppressed. Broader regression failures, untested correlations and incomplete numerical qualification remain; do not interpret a screening result as a validated service-life or material-selection guarantee.
- **Complete native runtime and competitor qualification.** Native PHREEQC platforms and alternative public implementations need further qualification.
- **Resolve remaining installed-artifact qualification findings.** The v0.1.0 source/wheel qualification completed MCP startup but retained these failed checks:

See [known issue details](KNOWN-ISSUES.md), [test results](TEST-RESULTS.md), [content scan](SCAN-REPORT.md), and [licensing inventory](LICENSE-TABLE.md). These limitations are disclosed for early feedback.

## License and attribution

See LICENSE, NOTICE and THIRD-PARTY-NOTICES for the wrapper and retained upstream material. External dependencies retain their own licenses.

Built by [Puran Water](https://puranwater.com).

Historical source-suite snapshot: 1551 passed. These counts predate the cleaned standalone export; they are not its qualification result. Later repairs covered selected subsets only. See [historical quality scope](HISTORICAL-QUALITY.md).
