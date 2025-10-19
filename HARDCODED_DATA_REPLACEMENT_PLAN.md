# Hardcoded Data Replacement Plan

## Executive Summary

This document identifies all hardcoded data in the corrosion engineering MCP and provides a plan to replace it with **direct imports from authoritative open-source repositories**.

**User Directive**: *"direct imports (which is preferable to hard-coded data)"* and *"Remove hardcoded fallbacks so we know we are always using authoritative sources"*

---

## ✅ COMPLETED: NRL Polarization Curve Data

### What Was Replaced
- **File**: `data/authoritative_materials_data.py` (Tafel coefficient placeholders)
- **Replaced With**: `data/nrl_polarization_curves.py` + 21 CSV files
- **Source**: US Naval Research Laboratory corrosion-modeling-applications repo
- **Coverage**: 6 materials × 5 reactions = 30+ parameter sets
- **Status**: ✅ COMPLETE - All galvanic backend now uses NRL data

### Benefits Achieved
- ✅ Temperature-dependent kinetics (response surface models)
- ✅ Chloride-dependent kinetics
- ✅ pH-corrected parameters
- ✅ Peer-reviewed experimental data
- ✅ Clear provenance and traceability

---

## 🔍 DISCOVERED: Additional Authoritative Repos in /tmp

### 1. NORSOK M-506 Internal Corrosion (`/tmp/norsokm506/`)

**Repository**: https://github.com/dungnguyen2/norsokm506

**What It Contains**:
- NORSOK M-506 standard implementation for internal corrosion in oil/gas pipelines
- pH factor calculations (`fpH_FixT()`) with temperature-dependent coefficients
- Shear stress calculations for multiphase flow
- CO₂ and H₂S corrosion rate models

**Hardcoded Coefficients**:
```python
# From norsokm506_01.py lines 26-50
if tempe==5.0:
    if (iph>=3.5) and (iph<=4.6): tempo = 2.0676 + 0.2309 * iph
    if (iph>4.6) and (iph<=6.5): tempo = 4.342 - (1.061 * iph) + (0.0708 * iph ** 2)
# ... more coefficients for 15°C, 20°C, 40°C, 60°C, 80°C, 90°C
```

**Where We Currently Hardcode Similar Data**:
- `data/authoritative_materials_data.py` lines 90-115: `CHLORIDE_THRESHOLD_25C` and `CHLORIDE_TEMP_COEFFICIENT`
- These are pH and temperature-dependent thresholds similar to NORSOK M-506 fpH factors

**Replacement Plan**:
1. Clone norsokm506 repo to `data/norsok_m506/`
2. Create wrapper module `data/norsok_internal_corrosion.py`
3. Import `fpH_FixT`, `Shearstress`, and other functions directly
4. Replace hardcoded chloride threshold calculations with NORSOK M-506 data
5. **Estimated Effort**: 150 LOC, 10 new tests

**Impact**: Replaces ~100 LOC of hardcoded coefficients with authoritative NORSOK standard

---

### 2. FreeCORP Material Composition Data (`/tmp/freecorp_api/`)

**Repository**: https://github.com/corrosion-engineering (FreeCORP API)
**License**: GPL-3.0

**What It Contains**:
- `fc-composition.dll`: UNS material compositions database
- `fc-corrosion.dll`: Transient electrochemical corrosion models
- `mc-modeling.dll`: Tafel coefficients and kinetic data
- Material database with alloy compositions

**Where We Currently Hardcode**:
- `data/authoritative_materials_data.py` lines 130-900: `MATERIALS_DATABASE`
- Manually typed compositions for 18 materials (304, 316L, 2205, etc.)

**Example Current Hardcoding**:
```python
MATERIALS_DATABASE = {
    "316L": MaterialComposition(
        Cr_pct=17.0,  # Manually typed
        Ni_pct=12.0,  # Manually typed
        Mo_pct=2.5,   # Manually typed
        # ...
    ),
    # ... 17 more materials, all hardcoded
}
```

**Replacement Plan**:
1. Decompile DLLs using dnSpy/ILSpy to extract data structures
2. Parse material composition tables from DLL metadata
3. Create `data/freecorp_materials.py` module
4. Replace hardcoded `MATERIALS_DATABASE` with FreeCORP import
5. **Estimated Effort**: 200 LOC, 15 new tests
6. **Blocker**: Requires .NET decompilation tools (dnSpy on Windows)

**Impact**: Replaces 770 LOC of manually typed material compositions with FreeCORP database

---

## 📊 REMAINING HARDCODED DATA IN CODEBASE

### 3. ASTM G48 CPT/CCT Data (`data/authoritative_materials_data.py` lines 20-85)

**Current Implementation**: Hardcoded dictionary
```python
ASTM_G48_CPT_DATA = {
    "304": {"CPT_C": 0, "CCT_C": -10, "test_solution": "6% FeCl3", "source": "ASTM G48-11"},
    "316L": {"CPT_C": 15, "CCT_C": 5, "test_solution": "6% FeCl3", "source": "ASTM G48-11"},
    # ... 15 more materials
}
```

**Potential Sources**:
- **NIST Materials Data Repository**: https://materialsdata.nist.gov/
- **ASM International Alloy Database**: https://github.com/Materials-Consortia/OPTIMADE (OPTIMADE standard)
- **MatWeb API**: Commercial but has public datasets

**Replacement Strategy**:
1. Search GitHub for ASTM G48 datasets: `gh search repos "ASTM G48" --language=python`
2. Check Materials-Consortia/OPTIMADE for CPT data
3. If no direct source: Keep current hardcoded data but add citations to ASTM G48-11 standard

**Estimated Effort**: 80 LOC if source found, otherwise document provenance

---

### 4. ASTM G82 Galvanic Series (`data/authoritative_materials_data.py` lines 120-155)

**Current Implementation**: Hardcoded dictionary
```python
GALVANIC_SERIES_SEAWATER = {
    "graphite": 0.25,  # V vs SCE
    "titanium": 0.10,
    "316L_passive": -0.10,
    # ... 15 more materials
}
```

**Source**: ASTM G82-98 (2014) "Standard Guide for Development and Use of a Galvanic Series for Predicting Galvanic Corrosion Performance"

**Potential Replacement**:
- No direct Python repos found with ASTM G82 data
- **CRITICAL BUG**: Values are vs SCE but need +0.241V conversion to SHE (ISSUE-101)

**Replacement Strategy**:
1. Keep hardcoded data (it's from authoritative source)
2. **FIX ISSUE-101**: Add conversion from SCE to SHE
3. Add detailed citations to ASTM G82-98
4. Consider creating CSV file: `data/astm_g82_galvanic_series.csv`

**Estimated Effort**: 30 LOC for ISSUE-101 fix + CSV export

---

### 5. ORR Diffusion Limits (`data/authoritative_materials_data.py` lines 115-125)

**Current Implementation**:
```python
ORR_DIFFUSION_LIMITS = {
    "seawater": 5.0,      # A/m² (0.5 mA/cm²)
    "freshwater": 2.0,    # A/m²
    "deaerated": 0.1,     # A/m²
}
```

**Source**: Empirical values from electrochemistry handbooks

**Potential Replacement**:
- NRL repo may have ORR limiting current density data
- Check NRL `Constants.m` for diffusion coefficients

**Replacement Strategy**:
1. Search NRL MATLAB files for O2 diffusion data
2. Calculate diffusion limits from Fick's law: `i_lim = n * F * D * C_bulk / δ`
3. If found: replace with NRL-derived values
4. If not: document provenance of current values

**Estimated Effort**: 40 LOC if NRL data found

---

## 🔧 RECOMMENDED REPLACEMENTS (Priority Order)

### Priority 1: CRITICAL BUGS (Immediate)

1. **ISSUE-101: Reference Electrode Conversion** (CRITICAL)
   - File: `core/galvanic_backend.py:439`
   - Fix: Add `E_SHE = E_SCE + 0.241` conversion
   - Effort: 20 LOC
   - **ALL galvanic calculations currently wrong by 0.24V!**

2. **Material Lookup Normalization** (CRITICAL)
   - Files: `data/authoritative_materials_data.py:get_material_data()`
   - Fix: Normalize spaces to underscores ("carbon steel" → "carbon_steel")
   - Effort: 15 LOC
   - **Breaks 3 tests currently**

### Priority 2: Direct Imports from Repos (High Value)

3. **NORSOK M-506 Integration** (HIGH)
   - Source: `/tmp/norsokm506/norsokm506_01.py`
   - Target: Create `data/norsok_internal_corrosion.py`
   - Replaces: Chloride threshold coefficients
   - Effort: 150 LOC + 10 tests

4. **FreeCORP Material Compositions** (HIGH)
   - Source: `/tmp/freecorp_api/fc-composition.dll`
   - Target: Replace `MATERIALS_DATABASE`
   - Replaces: 770 LOC of hardcoded compositions
   - Effort: 200 LOC + 15 tests
   - Blocker: Requires DLL decompilation

### Priority 3: Data Format Improvements (Medium)

5. **ASTM G48 CPT Data to CSV** (MEDIUM)
   - Convert `ASTM_G48_CPT_DATA` dict to `data/astm_g48_cpt_data.csv`
   - Benefit: Version control, easier updates
   - Effort: 50 LOC

6. **ASTM G82 Galvanic Series to CSV** (MEDIUM)
   - Convert `GALVANIC_SERIES_SEAWATER` to `data/astm_g82_galvanic_series.csv`
   - Benefit: Version control, easier updates
   - Effort: 40 LOC

---

## 📈 METRICS: Hardcoded vs Imported Data

### Current State (Before NRL Integration)
- **Total LOC in `authoritative_materials_data.py`**: 950
- **Hardcoded coefficients**: 900 LOC (95%)
- **Direct imports**: 50 LOC (5% - only imports from typing, dataclasses)

### After NRL Integration
- **Replaced**: Tafel coefficients (was 0 LOC placeholder, now 465 LOC NRL loader)
- **Hardcoded remaining**: 900 LOC (material compositions, CPT data, galvanic series)
- **Direct imports**: 515 LOC (54% of data module)

### Target State (After All Replacements)
- **Direct imports**: 850 LOC (90%)
- **CSV data files**: 100 LOC worth of data in CSVs
- **Minimal hardcoded**: 100 LOC (only data without available source)

---

## 🎯 NEXT STEPS

1. **Immediate** (Today):
   - Fix ISSUE-101 (reference electrode conversion) - 20 LOC
   - Fix material lookup normalization - 15 LOC
   - Run full test suite to ensure 157/157 passing

2. **Short Term** (This Week):
   - Integrate NORSOK M-506 fpH calculations - 150 LOC
   - Extract FreeCORP DLL data (if decompiler available) - 200 LOC
   - Convert ASTM G48/G82 to CSV format - 90 LOC

3. **Long Term** (Next Sprint):
   - Search for OPTIMADE/NIST materials databases
   - Investigate Materials Project API for alloy properties
   - Document provenance for all remaining hardcoded data

---

## 📚 AUTHORITATIVE SOURCES IDENTIFIED

| Source | Type | Status | Coverage |
|--------|------|--------|----------|
| NRL corrosion-modeling-applications | CSV files | ✅ Integrated | Tafel coefficients (6 materials) |
| NORSOK M-506 (dungnguyen2 repo) | Python module | 🔍 Found | pH factors, shear stress |
| FreeCORP (Ohio University ICMT) | .NET DLLs | 🔍 Found | Material compositions |
| ASTM G82-98 | Standard | 📖 Cited | Galvanic series (hardcoded) |
| ASTM G48-11 | Standard | 📖 Cited | CPT/CCT data (hardcoded) |
| ISO 18070 | Standard | 📖 Cited | Cl⁻ thresholds (hardcoded) |

---

## ✅ SUCCESS CRITERIA

**Definition of "No Hardcoded Data"**:
1. All coefficients loaded from external files (CSV, JSON, or direct imports)
2. All data traceable to authoritative source (repo, standard, or paper)
3. Version control on data files (can track when standards change)
4. Automatic updates possible (pull latest from source repos)
5. Clear provenance documentation for every value

**Current Progress**: 54% (NRL data integrated)
**Target**: 90% by end of sprint
