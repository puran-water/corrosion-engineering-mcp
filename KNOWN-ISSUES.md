# Known issues

## Restore data-backed tools using cleared redistributable data

Material, handbook and thermodynamic tables without fully cleared redistribution provenance are omitted. The public material catalog is empty. Tools requiring these tables cannot provide material-specific results; this release supplies no invented replacement values.

This is an acknowledged v0.1.0 limitation. See README.md and TEST-RESULTS.md for scope and evidence.

## Validate suppressed and experimental corrosion models

Several historical rate and suitability outputs are withdrawn or suppressed. Broader regression failures, untested correlations and incomplete numerical qualification remain; do not interpret a screening result as a validated service-life or material-selection guarantee.

This is an acknowledged v0.1.0 limitation. See README.md and TEST-RESULTS.md for scope and evidence.

## Complete native runtime and competitor qualification

Native PHREEQC platforms and alternative public implementations need further qualification.

This is an acknowledged v0.1.0 limitation. See README.md and TEST-RESULTS.md for scope and evidence.

## Resolve remaining installed-artifact qualification findings

The v0.1.0 source/wheel qualification completed MCP startup but retained these failed checks:

- `native-library-status` on `corrosion_get_phreeqc_status`.

See TEST-RESULTS.md. These failures are disclosed for early feedback; they are not claimed as passing validation.

The default phreeqc.dat chemistry call passes. The status probe reports that optional Amm.dat is not bundled. Pourbaix tables and the material catalog are omitted; table-backed functions are unavailable.
