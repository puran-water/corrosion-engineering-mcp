# NRL Polarization Curve Coefficient Files - Provenance

## Source

**Repository**: USNavalResearchLaboratory/corrosion-modeling-applications
**URL**: https://github.com/USNavalResearchLaboratory/corrosion-modeling-applications
**Directory**: `polarization-curve-modeling/`
**License**: Public domain (U.S. Federal Government work)
**Retrieved**: 2025-10-19

## Author

**Steven A. Policastro, Ph.D.**
Center for Corrosion Science and Engineering
U.S. Naval Research Laboratory
4555 Overlook Avenue SW
Washington, DC 20375

Email: steven.policastro@nrl.navy.mil

## Description

These CSV files contain polynomial response surface coefficients for calculating activation energies (ΔG) as functions of temperature (T) and chloride concentration (Cl⁻) for various electrochemical reactions on different alloys.

### Model Equation

For each reaction type (ORR, HER, metal oxidation, passivation, pitting), the activation energy is calculated using a quadratic response surface:

```
ΔG(T, Cl⁻) = p00 + p10*Cl⁻ + p01*T + p20*Cl⁻² + p11*Cl⁻*T + p02*T²
```

Where:
- `T` = Temperature (°C)
- `Cl⁻` = Chloride concentration (M)
- `p00, p10, p01, p20, p11, p02` = Fitted coefficients (from CSV files)

The coefficients were obtained by fitting experimental polarization curve data from seawater exposure experiments.

## Materials Covered

| Material | UNS Designation | CSV Files |
|----------|----------------|-----------|
| HY-80 Steel | K31820 | HY80ORRCoeffs.csv, HY80HERCoeffs.csv, HY80FeOxCoeffs.csv, HY80PitCoeffs.csv |
| HY-100 Steel | K32045 | HY100ORRCoeffs.csv, HY100HERCoeffs.csv, HY100FeOxCoeffs.csv, HY100PitCoeffs.csv |
| SS 316 Stainless | S31600 | SS316ORRCoeffs.csv, SS316HERCoeffs.csv, SS316PassCoeffs.csv, SS316PitCoeffs.csv |
| Titanium | R50700 | TiORRCoeffs.csv, TiHERCoeffs.csv, TiPassCoeffs.csv |
| Inconel 625 | N06625 | I625ORRCoeffs.csv, I625HERCoeffs.csv, I625PassCoeffs.csv |
| CuNi 70-30 | C71500 | cuniORRCoeffs.csv, cuniHERCoeffs.csv, cuniCuOxCoeffs.csv |

## Reaction Types

**ORR**: Oxygen Reduction Reaction (cathodic)
```
O₂ + 2H₂O + 4e⁻ → 4OH⁻  (alkaline)
```

**HER**: Hydrogen Evolution Reaction (cathodic)
```
2H₂O + 2e⁻ → H₂ + 2OH⁻  (alkaline)
```

**Metal Oxidation** (anodic):
- Fe_Ox: Fe → Fe²⁺ + 2e⁻
- Cu_Ox: Cu → Cu²⁺ + 2e⁻

**Passivation** (anodic):
- Formation of protective oxide films (Fe₂O₃, Cr₂O₃, TiO₂, etc.)

**Pitting** (anodic):
- Localized breakdown of passive film

## CSV File Format

Each CSV file contains a single row of 6 coefficients:

```
p00, p10, p01, p20, p11, p02
```

**Example** (`HY80ORRCoeffs.csv`):
```
-579946.613541671,6699.5967741938,4967.62500000003,2133.19458896982,-40.3225806451621,-8.75000000000004
```

## Usage in Python

```python
import pandas as pd
import numpy as np

# Load coefficients
coeffs = pd.read_csv('HY80ORRCoeffs.csv', header=None).values[0]
p00, p10, p01, p20, p11, p02 = coeffs

# Calculate ΔG for given conditions
def calculate_dG(T_celsius, Cl_molar):
    dG = (p00 + p10*Cl_molar + p01*T_celsius +
          p20*Cl_molar**2 + p11*Cl_molar*T_celsius + p02*T_celsius**2)
    return dG

# Example: 25°C, 0.6 M Cl⁻ (seawater)
dG_cathodic = calculate_dG(25.0, 0.6)
```

## pH Dependence

The polynomial coefficients give ΔG values without pH dependence. pH correction is applied using linear interpolation:

```python
# For cathodic reactions (ORR, HER)
dG_max = 1.1 * dG_nopH
dG_min = 0.9 * dG_nopH
m = (dG_min - dG_max) / (13 - 1)
dG_cathodic = m * (pH - 13) + dG_min

# For anodic reactions (metal oxidation, pitting)
# Use similar interpolation with different bounds
```

## References

1. **NRL Corrosion Modeling Applications Repository**
   https://github.com/USNavalResearchLaboratory/corrosion-modeling-applications

2. **Related Publications** (if available, cite NRL papers on this model)

## Validation

The model has been validated against experimental polarization curve data for seawater exposure at various temperatures (5-80°C) and chloride concentrations (0.02-0.6 M).

Typical accuracy: ±1 order of magnitude for current density predictions.

## Notes

- Coefficients are valid for temperatures 5-80°C
- Chloride concentration range: 0.02-0.6 M (freshwater to seawater)
- pH range: 1-13 (with linear interpolation for pH effects)
- The model uses Butler-Volmer kinetics with diffusion-limited corrections
- Exchange current densities are calculated from ΔG using transition state theory

---

**Vendored**: 2025-10-19
**For**: corrosion-engineering-mcp Phase 2 implementation
**License**: Public domain (U.S. Government work)
**Attribution**: Required - cite NRL GitHub repository and this provenance file
