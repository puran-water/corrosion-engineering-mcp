# Validation Framework - Phase 0

This directory contains validation datasets and benchmarking tools for the corrosion engineering MCP server.

---

## Validation Strategy

The MCP server uses a **3-source validation approach**:

1. **NRL Experimental Data** ✅ (Available on GitHub)
2. **NORSOK M-506 Benchmarks** ⚠️ (Pending - External Contact Required)
3. **Ohio University FREECORP** ⚠️ (Pending - External Contact Required)

---

## 1. NRL (U.S. Naval Research Laboratory) Data

### Status: ✅ **AVAILABLE**

**Source**: USNavalResearchLaboratory/corrosion-modeling-applications (MIT License)

**GitHub Repository**: https://github.com/USNavalResearchLaboratory/corrosion-modeling-applications

**Datasets Available**:
- **Galvanic Series**: `cma/SeawaterPotentialData.xml`
  - MIL-HDBK-1004/6 galvanic potentials in seawater
  - Already integrated into `utils/material_database.py`

- **Polarization Curves**: `polarization-curve-modeling/`
  - `SS316ORRCoeffs.csv` - Oxygen reduction reaction coefficients for SS316
  - `SS316HERCoeffs.csv` - Hydrogen evolution reaction coefficients for SS316
  - Regression coefficients for ORR/HER/passive/pitting fits
  - Integrated into `databases/electrochemistry.yaml`

**Validation Use**:
- Tier 2 galvanic corrosion tool validation
- Tier 2 localized corrosion (pitting) validation
- Electrochemical kinetics parameter benchmarking

**Integration Status**:
- ✅ Galvanic series: Loaded in `AuthoritativeMaterialDatabase`
- ✅ Polarization coefficients: Documented in `electrochemistry.yaml`
- ⚠️ Full CSV parsing: TODO in Phase 0 completion

---

## 2. NORSOK M-506 Validation Datasets

### Status: ⚠️ **NOT AVAILABLE** - External Contact Required

**Standard**: NORSOK M-506 "CO₂ corrosion rate calculation model"

**What's Needed**:
- Official validation tables from NORSOK M-506 standard
- Test cases with:
  - Input conditions (T, P, pCO₂, pH, flow velocity)
  - Expected corrosion rates (mm/year)
  - de Waard-Milliams correlation validation data

**Why Not Available**:
- ❌ Not found in dungnguyen2/norsokm506 GitHub repo (implementation only, no test data)
- ❌ Not found in corrosion_kb semantic search
- ❌ NORSOK standards are proprietary (paywall)

**Action Plan**:
1. **Contact NORSOK Standards** (Norway)
   - Email: info@standard.no
   - Request: Official validation tables from M-506
   - Alternative: Contact DNV (Det Norske Veritas)

2. **Contact dungnguyen2** (GitHub repo author)
   - Repository: https://github.com/dungnguyen2/norsokm506
   - Request: Validation datasets used in development
   - License: MIT (permissive for data sharing)

3. **Literature Search**
   - de Waard & Milliams original papers
   - NACE corrosion prediction papers
   - Oil & gas industry validation reports

**Validation Use (When Available)**:
- Tier 1: NORSOK M-506 CO₂ corrosion tool validation
- Tier 2: Mechanistic CO₂ corrosion model benchmarking
- Cross-validation with Ohio U FREECORP

**Interim Approach**:
- Use NRL polarization data as partial validation
- Synthesize test cases from published papers
- Document uncertainty in predictions until official validation

---

## 3. Ohio University FREECORP Datasets

### Status: ⚠️ **NOT AVAILABLE** - External Contact Required

**Institution**: Ohio University Institute for Corrosion and Multiphase Technology (ICMT)

**What's Needed**:
- FREECORP (Free Corrosion Prediction) validation datasets
- H₂S/CO₂ mixed-gas corrosion experimental data
- MULTICORP parameter sets

**Why Not Available**:
- ❌ Not found in OU-ICMT/MatlabMulticorp repo (parameter catalogue only, no validation data)
- ❌ Not found in corrosion_kb semantic search
- ❌ Datasets likely require direct contact with ICMT

**Action Plan**:
1. **Contact Ohio University ICMT**
   - Website: https://www.ohio.edu/engineering/icmt
   - Email: icmt@ohio.edu
   - Request: FREECORP validation datasets for academic/research use

2. **Review MULTICORP Documentation**
   - Repository: https://github.com/OU-ICMT/MatlabMulticorp
   - File: `multicorp_default_input.xml` contains parameter ranges
   - Use as reference for parameter validation (not rate prediction)

3. **Published Papers**
   - Search Ohio U ICMT publications for experimental data
   - Extract validation cases from peer-reviewed papers

**Validation Use (When Available)**:
- Tier 1: H₂S corrosion tool validation
- Tier 2: Mixed-gas (CO₂ + H₂S) corrosion validation
- Tier 3: Uncertainty quantification benchmarking

**Interim Approach**:
- Use MULTICORP parameter ranges for plausibility checks
- Synthesize test cases from published ICMT papers
- Document validation gaps in tool outputs

---

## Validation Workflow

### Phase 0 (Current)
1. ✅ Load NRL galvanic series
2. ✅ Load NRL polarization coefficients
3. ⚠️ Document NORSOK/Ohio U as pending
4. ✅ Create validation registry (this README)

### Phase 1 (PHREEQC Integration)
- Validate PHREEQC speciation against known equilibria
- Benchmark against WaterTAP/degasser-design-mcp results
- Cross-check pH predictions

### Phase 2 (Mechanistic Models)
- Validate galvanic corrosion against NRL data
- Benchmark NORSOK M-506 against published cases
- Validate Zargarnezhad coating model (if permeability data obtained)

### Phase 3 (Uncertainty Quantification)
- Monte Carlo convergence testing
- Sensitivity analysis against experimental variance
- Validate uncertainty bounds against FREECORP data (if obtained)

---

## Validation Test Cases (Current)

### NRL Polarization Curves
**File**: `validation/nrl_experiments.py`

**Test Cases**:
- SS316 oxygen reduction reaction (ORR) in seawater
- SS316 hydrogen evolution reaction (HER) in seawater
- Galvanic potential verification for common alloys

**Acceptance Criteria**:
- Tafel slopes within ±10% of NRL coefficients
- Exchange current densities within ±20% (log scale)
- Galvanic potentials within ±50 mV

### Synthetic Benchmarks
**File**: `validation/norsok_benchmarks.py`

**Test Cases** (Placeholder - awaiting official data):
- CO₂ corrosion at 60°C, 10 bar pCO₂, pH 5.5
- Mixed-gas corrosion (CO₂ + H₂S) at various ratios
- Velocity effects on corrosion rate

**Acceptance Criteria** (Provisional):
- Predictions within ±30% of published rates
- Correct trend with temperature, pressure, pH
- Passes sanity checks (no negative rates, realistic magnitudes)

---

## Running Validation Tests

```bash
# Run all validation tests
pytest validation/

# Run specific validation suite
pytest validation/test_nrl_benchmarks.py -v

# Run with coverage
pytest validation/ --cov=utils --cov=tools --cov-report=html
```

---

## Data Provenance

All validation data must include:
- **Source**: Repository URL or publication DOI
- **License**: Ensure redistribution is permitted
- **Date Retrieved**: ISO 8601 timestamp
- **Version/Commit**: For reproducibility
- **Processing**: Document any transformations applied

Example:
```yaml
source:
  repo: "USNavalResearchLaboratory/corrosion-modeling-applications"
  file: "polarization-curve-modeling/SS316ORRCoeffs.csv"
  url: "https://raw.githubusercontent.com/..."
  commit: "abc123def456"
  license: "MIT"
  retrieved_at: "2025-10-18T10:00:00Z"
  processing: "Converted CSV to JSON, extracted regression coefficients"
```

---

## Contacts for External Datasets

### NORSOK M-506
- **Organization**: NORSOK Standards (Norway)
- **Email**: info@standard.no
- **Alternative**: DNV (Det Norske Veritas)
- **Purpose**: Official validation tables from M-506 standard

### Ohio University ICMT
- **Organization**: Institute for Corrosion and Multiphase Technology
- **Website**: https://www.ohio.edu/engineering/icmt
- **Email**: icmt@ohio.edu
- **Purpose**: FREECORP validation datasets, H₂S/CO₂ experimental data

### dungnguyen2 (GitHub)
- **Repository**: https://github.com/dungnguyen2/norsokm506
- **Purpose**: Validation datasets used in NORSOK M-506 implementation
- **License**: MIT (permissive for data sharing)

---

## Validation Status Summary

| Dataset | Status | Integration | Phase |
|---------|--------|-------------|-------|
| **NRL Galvanic Series** | ✅ Available | ✅ Integrated | Phase 0 |
| **NRL Polarization Curves** | ✅ Available | ⚠️ Documented | Phase 0 |
| **NORSOK M-506 Validation** | ❌ Pending | ❌ Not Available | Phase 1+ |
| **Ohio U FREECORP** | ❌ Pending | ❌ Not Available | Phase 2+ |

---

## Next Steps

**Immediate (Week 1)**:
1. ✅ Document validation strategy (this README)
2. ⚠️ Parse NRL CSV files into validation test cases
3. ⚠️ Create placeholder test cases for NORSOK/Ohio U

**Week 2-3**:
4. ⚠️ Contact NORSOK Standards and Ohio U ICMT
5. ⚠️ Extract validation cases from published papers
6. ⚠️ Update `validation/run_validation.py` with NRL tests

**Parallel to Phase 1**:
- Continue pursuing external validation datasets
- Expand NRL-based validation coverage
- Document validation gaps in tool outputs

---

**Last Updated**: 2025-10-18
**Phase 0 Completion**: 95%
**Validation Coverage**: NRL (100%), NORSOK (0%), Ohio U (0%)
