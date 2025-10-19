# Corrosion Engineering Databases

This directory contains JSON databases for corrosion engineering calculations.

## Files

### `materials_catalog.json`
Material properties database including:
- Composition (wt%)
- Density
- Cost factors (relative to carbon steel)
- Galvanic potentials (V vs SCE)
- PREN (Pitting Resistance Equivalent Number)
- CPT (Critical Pitting Temperature)

**Phase 3 Enhancement**: Integrate with matminer for expanded alloy database.

### `coatings_catalog.json` (To be created in Phase 2)
Coating specifications including:
- Permeability data (Zargarnezhad 2022-2023)
- Thickness ranges
- Temperature limits
- Chemical compatibility

### `polarization_curves.json` (To be created in Phase 2)
Polarization curve library for galvanic corrosion calculations:
- Material-environment combinations
- Potential vs current data
- Sources (NRL, literature)

### `freecorp_parameters.json` (To be created in Phase 4)
FREECORP model parameters:
- CO2/H2S reaction kinetics
- Activation energies
- Protective scale formation parameters

## Data Sources

- **Materials**: NACE, ASM Handbook, manufacturer data sheets
- **Coatings**: Zargarnezhad et al. (2022-2023), coating manufacturer specs
- **Polarization**: NRL corrosion-modeling-applications, literature
- **FREECORP**: Ohio University ICMT publications

## Usage

```python
import json

# Load materials database
with open('databases/materials_catalog.json') as f:
    materials = json.load(f)

# Get 316L properties
ss316l = materials['materials']['316L']
print(f"PREN: {ss316l['PREN']}")  # 25
print(f"Cost factor: {ss316l['cost_factor']}")  # 3.5x carbon steel
```

## Maintenance

- Update cost factors annually (inflation, market changes)
- Add new materials as needed
- Validate PREN/CPT values against latest standards
- Expand coating database with new products

## Future Enhancements

- [ ] matminer integration for automatic property calculation
- [ ] pycalphad integration for phase diagrams
- [ ] Link to online materials databases (MatWeb, NIST)
- [ ] Uncertainty ranges for properties
