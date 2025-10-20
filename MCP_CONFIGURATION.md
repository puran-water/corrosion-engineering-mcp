# MCP Server Configuration

## Phase 2 - Corrosion Engineering MCP Server

This guide explains how to configure and use the Corrosion Engineering MCP server with Claude Desktop or other MCP clients.

---

## Quick Start

### 1. Verify Installation

```bash
cd /mnt/c/Users/hvksh/mcp-servers/corrosion-engineering-mcp
python server.py
```

You should see:
```
======================================================================
Corrosion Engineering MCP Server - Phase 2
======================================================================
Implemented Tools:
  [Tier 0] screen_materials - Material compatibility screening
  [Tier 0] query_typical_rates - Handbook rate lookup
  [Tier 0] identify_mechanism - Mechanism identification

  [Phase 2] assess_galvanic_corrosion - NRL mixed-potential model
  [Phase 2] generate_pourbaix_diagram - E-pH stability diagrams
  [Phase 2] get_material_properties - Alloy database (6 materials)

  [Info] get_server_info - Server information
======================================================================
```

### 2. Configure Claude Desktop

Add this to your Claude Desktop configuration file:

**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
**Mac**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Linux**: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "corrosion-engineering": {
      "command": "python",
      "args": [
        "/mnt/c/Users/hvksh/mcp-servers/corrosion-engineering-mcp/server.py"
      ],
      "env": {
        "PYTHONPATH": "/mnt/c/Users/hvksh/mcp-servers/corrosion-engineering-mcp"
      }
    }
  }
}
```

**For WSL/Linux** (using venv312):
```json
{
  "mcpServers": {
    "corrosion-engineering": {
      "command": "/mnt/c/Users/hvksh/mcp-servers/venv312/bin/python",
      "args": [
        "/mnt/c/Users/hvksh/mcp-servers/corrosion-engineering-mcp/server.py"
      ],
      "env": {
        "PYTHONPATH": "/mnt/c/Users/hvksh/mcp-servers/corrosion-engineering-mcp"
      }
    }
  }
}
```

### 3. Restart Claude Desktop

Close and reopen Claude Desktop for the configuration to take effect.

---

## Available Tools (Phase 2)

### Tier 0: Handbook Lookup

1. **screen_materials** - Material compatibility screening
   - Uses semantic search on 2,980 corrosion handbook chunks
   - Fast screening (<0.5 sec)

2. **query_typical_rates** - Handbook rate lookup
   - Empirical corrosion rate data from literature

3. **identify_mechanism** - Mechanism identification
   - Match symptoms to corrosion mechanisms

### Phase 2: Galvanic & Pourbaix

4. **assess_galvanic_corrosion** - NRL Butler-Volmer mixed-potential model
   - **Inputs**: anode_material, cathode_material, temperature_C, pH, chloride_mg_L, area_ratio, velocity, DO
   - **Materials**: HY80, HY100, SS316, Ti, I625, CuNi
   - **Outputs**: mixed_potential_VSCE, galvanic_current_density, corrosion_rate_mm_year, current_ratio, warnings
   - **Use Case**: "Will HY-80 steel bolts corrode in a SS316 flange in wastewater?"

5. **generate_pourbaix_diagram** - E-pH stability diagrams
   - **Inputs**: element, temperature_C, soluble_concentration_M, pH_range, E_range, grid_points
   - **Elements**: Fe, Cr, Ni, Cu, Ti, Al
   - **Outputs**: regions (immunity/passivation/corrosion), boundaries, water_lines
   - **Use Case**: "Is carbon steel stable in anaerobic digester (pH 7.2, reducing)?"

6. **get_material_properties** - Alloy database
   - **Inputs**: material (HY80, HY100, SS316, Ti, I625, CuNi)
   - **Outputs**: composition, UNS, passivation behavior, galvanic series, wastewater notes
   - **Use Case**: "What are the corrosion characteristics of SS316 in wastewater?"

7. **get_server_info** - Server information
   - Returns version, capabilities, tool count

---

## Example Usage (via Claude Desktop)

### Example 1: Assess Galvanic Corrosion in Wastewater Plant

**Scenario**: HY-80 steel bolts (M20) in SS316 stainless flange, municipal wastewater (pH 7.5, 25°C, 800 mg/L Cl⁻)

**Claude Prompt**:
```
I have a SS316 stainless steel flange with HY-80 carbon steel bolts (M20).
The wastewater conditions are:
- pH: 7.5
- Temperature: 25°C
- Chloride: 800 mg/L
- Aerated (DO ≈ 2 mg/L)

The flange area is about 50x larger than the total bolt area. Will I have
galvanic corrosion issues?
```

**Claude will call**:
```
assess_galvanic_corrosion(
    anode_material="HY80",
    cathode_material="SS316",
    temperature_C=25.0,
    pH=7.5,
    chloride_mg_L=800.0,
    area_ratio_cathode_to_anode=50.0
)
```

**Expected Result**:
- current_ratio > 10 → **Severe galvanic attack warning!**
- Corrosion rate: ~5-20 mm/year (bolts will fail quickly)
- **Recommendation**: Use SS316 bolts or electrically isolate

---

### Example 2: Material Selection for Anaerobic Digester

**Scenario**: Selecting material for anaerobic digester (pH 7.2, 35°C, reducing conditions)

**Claude Prompt**:
```
I'm designing an anaerobic digester for wastewater treatment. Conditions:
- pH: 7.2
- Temperature: 35°C (mesophilic)
- Reducing environment (E ≈ -0.3 V_SHE)
- Low chloride (<200 mg/L)

Should I use carbon steel or upgrade to stainless? What does the Pourbaix
diagram say about Fe stability?
```

**Claude will call**:
```
generate_pourbaix_diagram(
    element="Fe",
    temperature_C=35.0,
    pH_range_min=6.5,
    pH_range_max=7.5
)

get_material_properties("SS316")
```

**Expected Result**:
- Pourbaix shows Fe in **corrosion region** at pH 7.2, E = -0.3 V (active dissolution)
- **Recommendation**: Carbon steel will corrode. Use SS316 with molybdenum for pitting resistance, OR use epoxy-coated carbon steel.

---

### Example 3: Troubleshoot Titanium Heat Exchanger Failure

**Scenario**: Carbon steel piping connected to titanium heat exchanger showing rapid failure

**Claude Prompt**:
```
We have a titanium plate heat exchanger in our industrial wastewater system
(high chloride, pH 6.8, 45°C). The carbon steel inlet/outlet piping is
corroding rapidly near the connections. What's happening?
```

**Claude will call**:
```
assess_galvanic_corrosion(
    anode_material="HY80",  # Proxy for carbon steel
    cathode_material="Ti",
    temperature_C=45.0,
    pH=6.8,
    chloride_mg_L=5000.0,  # Estimated high chloride
    area_ratio_cathode_to_anode=100.0  # Large HX area
)

get_material_properties("Ti")
```

**Expected Result**:
- **Extreme galvanic attack** (current_ratio >> 100)
- Ti is VERY noble (highly cathodic)
- **Root cause**: Titanium/carbon steel galvanic couple
- **Warning**: "NEVER couple Ti with carbon steel"
- **Solution**:
  1. Use Ti piping (expensive but compatible)
  2. Use SS316 piping (compatible, less expensive)
  3. Install dielectric union (electrical isolation)

---

## Testing the Server

### Test 1: Server Info
```bash
python
>>> from tools import get_server_info
>>> info = get_server_info()
>>> print(info)
```

Expected: Version 0.2.0, Phase 2, 7 tools

### Test 2: Galvanic Corrosion
```python
from tools.mechanistic.predict_galvanic_corrosion import predict_galvanic_corrosion

result = predict_galvanic_corrosion(
    anode_material="HY80",
    cathode_material="SS316",
    temperature_C=25.0,
    pH=8.0,
    chloride_mg_L=19000.0,  # Seawater
    area_ratio_cathode_to_anode=10.0
)

print(f"Corrosion rate: {result['anode_corrosion_rate_mm_year']:.2f} mm/year")
print(f"Current ratio: {result['current_ratio']:.1f}x")
print(f"Warnings: {result['warnings']}")
```

Expected: CR > 1 mm/year, warnings for severe attack

### Test 3: Pourbaix Diagram
```python
from tools.chemistry.calculate_pourbaix import calculate_pourbaix

diagram = calculate_pourbaix(
    element="Fe",
    temperature_C=25.0,
    pH_range=(6.0, 8.0),
    E_range_VSHE=(-1.0, 0.5)
)

print(f"Boundaries: {len(diagram['boundaries'])}")
print(f"Regions: {diagram['regions'].keys()}")
```

Expected: 3 regions (immunity, passivation, corrosion), multiple boundaries

---

## Troubleshooting

### Server Won't Start
1. Check Python environment: `python --version` (should be 3.10+)
2. Check dependencies: `pip install fastmcp numpy scipy`
3. Check imports: `python -c "from tools.mechanistic.predict_galvanic_corrosion import predict_galvanic_corrosion"`

### Claude Desktop Can't Connect
1. Verify config file path (see section 2 above)
2. Check JSON syntax (use jsonlint.com)
3. Verify Python path: `which python` or `where python`
4. Restart Claude Desktop (full restart required)

### Tools Return Errors
1. Check material names (case-sensitive: "HY80", "SS316", "Ti", etc.)
2. Check parameter ranges:
   - Temperature: 5-80°C (NRL data validity)
   - pH: 1-13
   - Chloride: 0-60000 mg/L (0-1.7 M)
3. Check logs: Server outputs detailed logging

---

## Next Steps

### Phase 3 Roadmap
- Full PHREEQC integration (exact speciation)
- CUI (Corrosion Under Insulation) risk assessment
- MIC (Microbiologically Influenced Corrosion) prediction
- FAC (Flow-Accelerated Corrosion) modeling
- Stainless steel pitting resistance (PREN/CPT)

### Phase 4 Roadmap
- MULTICORP integration (CO2/H2S corrosion)
- Monte Carlo uncertainty quantification
- Probabilistic remaining life assessment

---

## Support

- **GitHub**: https://github.com/puran-water/corrosion-engineering-mcp
- **Documentation**: See `docs/PHASE2_COMPLETE.md`
- **Tests**: 41/41 passing (100%)
- **Provenance**: 100% authoritative NRL sources

**Version**: 0.2.0
**Status**: Production-ready ✅
**Last Updated**: 2025-10-19
