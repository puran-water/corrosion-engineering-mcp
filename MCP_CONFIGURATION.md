# MCP Server Configuration

## Corrosion Engineering MCP Server (v0.3.0)

This guide explains how to configure and use the Corrosion Engineering MCP server with Claude Desktop or other MCP clients.

---

## Quick Start

### 1. Clone and Install

```bash
git clone https://github.com/puran-water/corrosion-engineering-mcp.git
cd corrosion-engineering-mcp
pip install -r requirements.txt
```

### 2. Verify Installation

```bash
python server.py
```

### 3. Configure Claude Desktop

Copy the example configuration and customize it:

```bash
cp .mcp.json.example .mcp.json
# Edit .mcp.json with your paths
```

Add the server to your Claude Desktop configuration file:

**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
**Mac**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Linux**: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "corrosion-engineering-mcp": {
      "command": "/path/to/python",
      "args": [
        "/path/to/corrosion-engineering-mcp/server.py"
      ]
    }
  }
}
```

> **Note**: Replace `/path/to/python` with your Python interpreter path and `/path/to/corrosion-engineering-mcp` with your installation directory.

### 4. Restart Claude Desktop

Close and reopen Claude Desktop for the configuration to take effect.

---

## Available Tools (14 Total)

All tools have the `corrosion_` prefix.

### Tier 0: Handbook Lookup

| Tool | Description |
|------|-------------|
| `corrosion_screen_materials` | Material compatibility screening via semantic search |
| `corrosion_query_typical_rates` | Handbook corrosion rate lookup |
| `corrosion_identify_mechanism` | Mechanism identification + mitigation guidance |

### Tier 1: Chemistry

| Tool | Description |
|------|-------------|
| `corrosion_langelier_index` | Langelier Saturation Index for scaling |
| `corrosion_predict_scaling` | Mineral scale prediction |

### Tier 2: Mechanistic Physics

| Tool | Description |
|------|-------------|
| `corrosion_predict_co2_h2s` | NORSOK M-506 sweet/sour service |
| `corrosion_predict_aerated_chloride` | O₂ mass transfer limited corrosion |
| `corrosion_assess_galvanic` | NRL Butler-Volmer galvanic corrosion |
| `corrosion_generate_pourbaix` | E-pH stability diagrams |
| `corrosion_get_material_properties` | Alloy database (18+ materials) |
| `corrosion_estimate_service_life` | Remaining life prediction |

### Tier 3: Localized Corrosion

| Tool | Description |
|------|-------------|
| `corrosion_assess_localized` | Dual-tier pitting + crevice assessment |
| `corrosion_calculate_pren` | PREN calculation for stainless steels |

### Informational

| Tool | Description |
|------|-------------|
| `corrosion_get_server_info` | Server version and capabilities |

---

## Example Usage

### Example 1: Galvanic Corrosion Assessment

**Scenario**: HY-80 steel bolts in SS316 flange, wastewater conditions

```
Claude Prompt:
I have a SS316 stainless steel flange with HY-80 carbon steel bolts.
The wastewater conditions are: pH 7.5, 25°C, 800 mg/L chloride, aerated.
The flange area is 50x larger than bolt area. Will I have galvanic issues?
```

Claude will call:
```python
corrosion_assess_galvanic(
    anode_material="HY80",
    cathode_material="SS316",
    temperature_C=25.0,
    pH=7.5,
    chloride_mg_L=800.0,
    area_ratio_cathode_to_anode=50.0
)
```

### Example 2: Material Selection Screening

```
Claude Prompt:
Which material should I use for a CO₂-rich brine at 60°C?
Consider carbon steel, 316L, and duplex 2205.
```

Claude will call:
```python
corrosion_screen_materials(
    environment="CO2-rich brine, 60°C, pCO2=0.5 bar",
    candidates=["CS", "316L", "2205"],
    application="piping"
)
```

### Example 3: Pitting Risk Assessment

```
Claude Prompt:
Is 316L safe in seawater at 25°C?
```

Claude will call:
```python
corrosion_assess_localized(
    material="316L",
    temperature_C=25.0,
    Cl_mg_L=19000.0,
    pH=8.0,
    dissolved_oxygen_mg_L=8.0
)
```

---

## Supported Materials

### NRL Database (Electrochemical Kinetics)
HY80, HY100, SS316, Ti, I625, CuNi

### CSV Database (PREN/CPT Data)
304, 304L, 316, 316L, 317L, 904L, 2205, 2507, 254SMO, AL-6XN, Carbon Steel, Inconel 625, Incoloy 825, Copper, 90-10 CuNi, Aluminum, Titanium Grade 2, Zinc

---

## Troubleshooting

### Server Won't Start
1. Check Python environment: `python --version` (should be 3.12+)
2. Check dependencies: `pip install -r requirements.txt`
3. Test import: `python -c "import server"`

### Claude Desktop Can't Connect
1. Verify JSON syntax in config file
2. Check Python path is correct
3. Restart Claude Desktop (full restart required)

### Tools Return Errors
1. Check material names (case-sensitive for NRL: "HY80", "SS316")
2. Check parameter ranges:
   - Temperature: 5-80°C (NRL data)
   - pH: 1-13
   - Chloride: 0-60000 mg/L

---

**Version**: 0.3.0
**Tests**: 348 passing
**Last Updated**: 2025-12-15
