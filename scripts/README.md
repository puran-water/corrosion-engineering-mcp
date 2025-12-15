# Scripts Directory

This directory contains **development utilities** for database maintenance and data extraction.

These scripts are **not required for normal operation** of the corrosion-engineering-mcp server.

## Scripts

### `extract_coating_data.py`
Extracts coating permeability data from semantic search on corrosion knowledge base.
- **Output**: `databases/coating_permeability.yaml`
- **Requires**: corrosion_kb MCP server running (or uses mock data)

### `extract_electrochemistry_data.py`
Extracts Tafel slopes and exchange current densities from:
1. Semantic search on corrosion knowledge base
2. NRL GitHub repository
3. Butler-Volmer calculations
- **Output**: `databases/electrochemistry.yaml`
- **Requires**: Internet connection for NRL GitHub data

### `extract_pitting_resistance_data.py`
Calculates PREN and critical pitting temperatures from standard alloy compositions.
- **Output**: `databases/pitting_resistance.json`
- **Self-contained**: No external dependencies

## Usage

These scripts are typically run manually when updating the embedded databases:

```bash
# From project root
python scripts/extract_coating_data.py
python scripts/extract_electrochemistry_data.py
python scripts/extract_pitting_resistance_data.py
```

## Notes

- Scripts use mock semantic search if the corrosion_kb server is not available
- Generated YAML/JSON files should be reviewed before committing
- Data provenance is automatically included in output files
