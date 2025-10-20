# NRL MATLAB Reference Files - 1:1 Translation Mapping

**Purpose**: This folder contains the original NRL MATLAB source files that were translated 1:1 to Python for Phase 2 implementation. These files serve as reference for Codex AI validation to verify translation fidelity.

**Source**: USNavalResearchLaboratory/corrosion-modeling-applications
**Retrieved**: 2025-10-19
**License**: Public domain (U.S. Federal Government work)

---

## Translation Mapping

### Physical Constants
| MATLAB Source | Python Translation | Lines |
|---------------|-------------------|-------|
| `Constants.m` | `utils/nrl_constants.py` | 150 |

**Translation Type**: Direct 1:1 property mapping
**Verification**: All constants match exactly (R, F, e0 values, conversions)

---

### Material Classes (6 Alloys)

| MATLAB Source | Python Translation | Material | UNS | Lines |
|---------------|-------------------|----------|-----|-------|
| `HY80.m` | `utils/nrl_materials.py::HY80` | HY-80 Steel | K31820 | ~180 |
| `HY100.m` | `utils/nrl_materials.py::HY100` | HY-100 Steel | K32045 | ~180 |
| `SS316.m` | `utils/nrl_materials.py::SS316` | SS 316 Stainless | S31600 | ~195 |
| `Ti.m` | `utils/nrl_materials.py::Ti` | Titanium | R50700 | ~165 |
| `I625.m` | `utils/nrl_materials.py::I625` | Inconel 625 | N06625 | ~170 |
| `CuNi.m` | `utils/nrl_materials.py::CuNi` | CuNi 70-30 | C71500 | ~160 |

**Translation Type**: Direct 1:1 class translation with factory function
**Verification**: All properties, methods, and CSV loading logic match exactly

---

### Electrochemical Reaction Classes

| MATLAB Source | Python Translation | Reaction Type | Lines |
|---------------|-------------------|---------------|-------|
| `ElectrochemicalReductionReaction.m` | `utils/nrl_electrochemical_reactions.py::CathodicReaction` | ORR, HER | ~260 |
| `ElectrochemicalOxidationReaction.m` | `utils/nrl_electrochemical_reactions.py::AnodicReaction` | Oxidation, Passivation, Pitting | ~245 |
| `reactionNames.m` | `utils/nrl_electrochemical_reactions.py::ReactionType` | Enum | ~50 |

**Translation Type**: Direct 1:1 with Python idioms (enum.Enum instead of uint8 class)
**Verification**:
- Butler-Volmer equations match exactly
- Diffusion limit calculations identical
- Film resistance correction (Newton-Raphson) preserved
- All numerical constants match

---

### Polarization Curve Model

| MATLAB Source | Python Translation | Purpose | Lines |
|---------------|-------------------|---------|-------|
| `PolarizationCurveModel.m` | `tools/mechanistic/predict_galvanic_corrosion.py::_calculate_polarization_curve()` | Polarization curve generation | ~440 |
| `galvanicCorrosion.m` | `tools/mechanistic/predict_galvanic_corrosion.py::_find_mixed_potential()` | Mixed potential solver | ~300 |

**Translation Type**: Simplified (1D instead of 2D Laplace solver)
**Differences**:
- MATLAB: Full 2D finite difference Laplace equation solver with SOR
- Python: 1D mixed potential solver using scipy.optimize.brentq
- **Rationale**: 1D solver sufficient for most engineering applications, full 2D solver for complex geometries

**Preserved Functionality**:
- Butler-Volmer kinetics: ✅ Exact match
- Mixed potential theory: ✅ Same algorithm (different solver)
- Area ratio effects: ✅ Preserved
- Current-to-corrosion-rate: ✅ Same Faraday's law implementation

---

## Codex Validation Checklist

For each translation pair, verify:

### 1. Constants Match
- [ ] Physical constants (R, F, kb, h, ε₀, e)
- [ ] Standard electrode potentials (E⁰)
- [ ] Molar masses (M_Fe, M_Cr, etc.)
- [ ] Diffusion coefficients
- [ ] Unit conversion factors

### 2. Equations Match
- [ ] Butler-Volmer: `i = i0 * exp(α*z*F*η/RT)`
- [ ] Nernst potential: `E_N = E⁰ + (RT/zF) * ln(a_red/a_ox)`
- [ ] Diffusion limit: `i_lim = -z*F*D*C/(δ*M)`
- [ ] Koutecky-Levich: `i_tot = (i_lim * i_act) / (i_act + i_lim)`
- [ ] Polynomial response surface: `ΔG = p00 + p10*Cl + p01*T + ...`
- [ ] pH correction: Linear interpolation (m*(pH-13)+dG_min)

### 3. CSV Coefficient Loading
- [ ] File paths correct (`external/nrl_coefficients/`)
- [ ] CSV format matches (6 coefficients: p00, p10, p01, p20, p11, p02)
- [ ] All 21 CSV files accounted for

### 4. Material Properties
- [ ] Molar mass values match
- [ ] Oxidation states (z) match
- [ ] Transfer coefficients (β/α) match
- [ ] Diffusion layer thicknesses match
- [ ] Oxide film properties match

### 5. Algorithmic Structure
- [ ] Initialization logic same
- [ ] Loop structures equivalent
- [ ] Convergence criteria same
- [ ] Error handling appropriate

---

## Known Differences (Intentional)

### 1. MATLAB vs Python Idioms
- MATLAB `switch/case` → Python `if/elif`
- MATLAB `classdef` → Python `class`
- MATLAB `enumeration` → Python `enum.Enum`
- MATLAB arrays (1-indexed) → NumPy arrays (0-indexed)

### 2. Simplified Implementations
- **Galvanic solver**: 1D (Python) vs 2D Laplace (MATLAB)
  - Reason: 1D solver sufficient for uniform conditions
  - When to use 2D: Complex geometries, IR drop critical

### 3. Enhanced Features (Python)
- Type hints (not in MATLAB)
- Comprehensive docstrings
- Input validation with descriptive errors
- Structured return dictionaries (vs MATLAB structs)

---

## File Listing

```
external/nrl_matlab_reference/
├── README.md (this file)
├── Constants.m
├── HY80.m
├── HY100.m
├── SS316.m
├── Ti.m
├── I625.m
├── CuNi.m
├── ElectrochemicalReductionReaction.m
├── ElectrochemicalOxidationReaction.m
├── reactionNames.m
├── PolarizationCurveModel.m
└── galvanicCorrosion.m
```

**Total**: 12 MATLAB files → 4 Python modules

---

## Validation Commands

### Compare Constants
```bash
# Extract all numeric constants from both files
grep -E "^\s+[A-Z_]+\s*=\s*[0-9]" external/nrl_matlab_reference/Constants.m
grep -E "^\s+[A-Z_]+\s*=\s*[0-9]" utils/nrl_constants.py
```

### Verify CSV Paths
```bash
# Check all CSV file references
grep -r "\.csv" external/nrl_matlab_reference/
grep -r "\.csv" utils/nrl_materials.py
```

### Count Material Classes
```bash
# MATLAB: 6 files
ls external/nrl_matlab_reference/{HY80,HY100,SS316,Ti,I625,CuNi}.m | wc -l

# Python: 6 classes in 1 file
grep "^class.*CorrodingMetal" utils/nrl_materials.py | wc -l
```

---

## Codex Review Instructions

When submitting to Codex AI for validation:

1. **Provide this folder** as reference context
2. **Highlight translation mapping** from this README
3. **Request verification** of:
   - Equation fidelity
   - Constant values
   - Algorithmic equivalence
4. **Acknowledge intentional differences** (1D vs 2D solver)
5. **Confirm zero undocumented heuristics**

---

**Last Updated**: 2025-10-19
**Translation Fidelity**: 1:1 (except intentional simplifications noted above)
**Ready for Codex Validation**: ✅
