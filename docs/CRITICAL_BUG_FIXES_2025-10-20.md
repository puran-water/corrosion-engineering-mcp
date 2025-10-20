# Critical Bug Fixes: Temperature Units and Galvanic Solver

**Date**: 2025-10-20
**Severity**: CRITICAL
**Impact**: All NRL electrochemical kinetics calculations
**Status**: FIXED ✅

---

## Executive Summary

Two critical bugs were discovered and fixed with assistance from Codex AI:

1. **Temperature Unit Bug**: NRL polynomials expected Kelvin, code passed Celsius
2. **Galvanic Solver Bug**: Current ratio used net current instead of anodic current

Both bugs caused incorrect corrosion predictions for all materials (HY80, HY100, SS316, Ti, I625, CuNi).

---

## Bug #1: Temperature Unit Conversion (Celsius → Kelvin)

### Root Cause

NRL Butler-Volmer polynomial coefficients expect temperature in **Kelvin**, but the Python implementation was passing **Celsius** directly.

**File**: `utils/nrl_materials.py`
**Function**: `_apply_polynomial_response_surface()`
**Line**: 287 (pre-fix)

### Impact

**HY80 Material** (exposed the bug due to large negative p00 term):
- **Before fix**: ΔG_ORR = -4.50×10⁵ J/mol (INVALID - negative activation energy)
- **After fix**: ΔG_ORR = +1.19×10⁵ J/mol (VALID - positive barrier)

**All Other Materials** (masked the bug due to positive p00 terms):
- Still had incorrect activation energies (off by ~273 K scaling)
- Did not fail validation, but produced physically incorrect kinetics
- Corrosion rates were quantitatively wrong

### Evidence from NRL MATLAB Reference

From `external/nrl_matlab_reference/ElectrochemicalReductionReaction.m`:
```matlab
obj.Temperature = T  % Stored in Kelvin
% Used directly in Boltzmann prefactors (lines 69-108)
```

From `external/nrl_matlab_reference/PolarizationCurveModel.m`:
```matlab
title(..., sprintf('T = %g K', T))  % Temperature labeled in Kelvin
```

### Fix

```python
def _apply_polynomial_response_surface(
    self,
    coeffs: np.ndarray,
    chloride_M: float,
    temperature_C: float
) -> float:
    """Apply quadratic response surface polynomial.

    CRITICAL: NRL polynomials expect temperature in KELVIN, not Celsius.
    """
    p00, p10, p01, p20, p11, p02 = coeffs

    # Convert Celsius to Kelvin (CRITICAL FIX)
    temperature_K = temperature_C + 273.15

    delta_g_no_pH = (
        p00 +
        p10 * chloride_M +
        p01 * temperature_K +      # Use Kelvin
        p20 * chloride_M**2 +
        p11 * chloride_M * temperature_K +  # Use Kelvin
        p02 * temperature_K**2     # Use Kelvin
    )

    return delta_g_no_pH
```

### Validation

Test at various chloride concentrations (T=25°C = 298.15 K):

| Chloride (M) | dG_ORR Before (J/mol) | dG_ORR After (J/mol) | Status |
|--------------|----------------------|---------------------|---------|
| 0.001        | -4.54×10⁵           | +1.21×10⁵          | ✅ FIXED |
| 0.010        | -4.53×10⁵           | +1.21×10⁵          | ✅ FIXED |
| 0.100        | -4.50×10⁵           | +1.21×10⁵          | ✅ FIXED |
| 0.540        | -4.50×10⁵           | +1.19×10⁵          | ✅ FIXED |

**All activation energies now positive and physically reasonable (100-120 kJ/mol).**

---

## Bug #2: Galvanic Solver Current Definition

### Root Cause

The galvanic corrosion solver was using **net current density** (anodic + cathodic) instead of **anodic current density** for:
- Current ratio calculation
- Galvanic current density output
- Mass loss calculations

This caused incorrect `current_ratio < 1.0` (protective behavior) when it should be `> 1.0` (accelerated corrosion).

**File**: `tools/mechanistic/predict_galvanic_corrosion.py`
**Functions**: Multiple locations (lines 289-305, 321-342, 365-370)

### Impact

**HY80/SS316 Galvanic Couple at Seawater**:
- **Before fix**:
  - `current_ratio = 0.0375` (appears protective - WRONG)
  - `galvanic_current_density = 8.60×10⁻⁷ A/cm²` (net current - WRONG)
  - `anode_CR = 0.0001 mm/yr` (underestimated - WRONG)

- **After fix**:
  - `current_ratio = 1.0379` (accelerated corrosion - CORRECT)
  - `galvanic_current_density = 2.38×10⁻⁵ A/cm²` (anodic current - CORRECT)
  - `anode_CR = 0.00287 mm/yr` (realistic - CORRECT)

### Root Cause Explanation (from Codex)

> "The earlier 'current_ratio < 1' came from mixing net current (anodic + cathodic on HY80) with the pure anodic branch in the denominator. With positive ΔG values the net current stays small because local ORR/HER still occur on HY80, so the ratio falsely looked protective."

The solver was comparing:
- **Numerator**: Net current at mixed potential (anodic - cathodic ≈ 0)
- **Denominator**: Pure anodic current at isolated E_corr

This gave artificially low ratios.

### Fix

**Changes by Codex**:
1. Interpolate **anodic branch only** at mixed potential
2. Use anodic current for all mass loss calculations
3. Add new field `galvanic_net_current_density_A_cm2` for diagnostics
4. Update identical-material shortcut to use anodic current

**Key Code Change**:
```python
# Before: Used net current (WRONG)
i_galvanic = i_net_at_mixed_potential

# After: Interpolate anodic current (CORRECT)
i_galvanic_anodic = np.interp(
    E_mix,
    result_anode.potential_VSCE,
    result_anode.current_density_anodic_A_cm2
)
```

### Validation

**Area Ratio Sweep** (HY80/SS316 at seawater, T=25°C):

| Area Ratio | Current Ratio (Before) | Current Ratio (After) | Expected Behavior |
|------------|------------------------|----------------------|-------------------|
| 1:1        | 0.0375                | 1.038               | ✅ Modest amplification |
| 10:1       | 0.0410                | 1.215               | ✅ Increased amplification |
| 50:1       | 0.0450                | 1.440               | ✅ Severe amplification |

**Now correctly shows increasing galvanic attack with larger cathode area.**

---

## Test Results

### Phase 2 Galvanic Tests

**Before Fixes**: 21 failed, 4 errors, 19 passed
**After Fixes**: **44 passed, 0 failed** ✅

### Key Tests Validated

1. ✅ `test_all_materials_instantiate` - All 6 NRL materials work (including HY80)
2. ✅ `test_hy80_properties` - HY80 activation energies positive
3. ✅ `test_hy80_ss316_couple_seawater` - Current ratio > 1.0 (accelerated)
4. ✅ `test_area_ratio_effect` - Larger cathode → higher galvanic attack
5. ✅ `test_temperature_effect_on_galvanic` - Temperature dependence correct
6. ✅ `test_chloride_effect_on_galvanic` - Chloride effect realistic

### Materials Validated

All NRL materials now produce correct kinetics:
- ✅ **HY80** (high-strength low-alloy steel)
- ✅ **HY100** (high-strength low-alloy steel)
- ✅ **SS316** (austenitic stainless steel)
- ✅ **Ti** (commercially pure titanium)
- ✅ **I625** (Inconel 625 superalloy)
- ✅ **CuNi** (90/10 copper-nickel)

---

## Lessons Learned

### 1. Unit Conversions Are Critical

Always verify units when porting code between languages/frameworks:
- MATLAB reference used Kelvin
- Python port assumed Celsius
- No unit tests caught this initially

**Recommendation**: Add explicit unit checks in validation layer.

### 2. Large Negative Constants Can Expose Bugs

HY80's large negative p00 term (-5.8×10⁵ J/mol) made the bug immediately visible:
- Other materials had positive p00, so passed validation with wrong units
- HY80 failed catastrophically, exposing the root cause

**Recommendation**: Test edge cases and materials with extreme coefficients.

### 3. Codex AI Code Review is Valuable

Both bugs were discovered through systematic investigation with Codex:
1. First request: Investigate why HY80 fails at all conditions
2. Codex response: Temperature unit mismatch (checked MATLAB reference)
3. Second request: Investigate unexpected galvanic behavior after fix
4. Codex response: Current definition mismatch (net vs anodic)

**Recommendation**: Use AI-assisted code review for critical physics calculations.

---

## Migration Guide for Users

### Impact on Existing Results

**All previous NRL-based calculations were incorrect** due to temperature unit bug.

If you have existing results from this codebase before 2025-10-20, you should:

1. **Re-run all NRL calculations** with fixed code
2. **Compare old vs new results**:
   - Activation energies will be different (~273 K offset effect)
   - Galvanic current ratios will change (net → anodic)
   - Corrosion rates may differ quantitatively

3. **Update documentation/reports** with corrected values

### API Compatibility

**100% backward compatible** - no API changes:
- Same function signatures
- Same input parameters
- Same output structure

**New field added** (optional):
- `galvanic_net_current_density_A_cm2` - for diagnostics only
- Existing field `galvanic_current_density_A_cm2` now reports anodic current (was net)

### Example Comparison

**Before Fix** (INCORRECT):
```python
result = predict_galvanic_corrosion("HY80", "SS316", 25.0, 8.0, 19000.0, 1.0)
# current_ratio: 0.0375 (falsely protective)
# anode_CR: 0.0001 mm/yr (underestimated)
```

**After Fix** (CORRECT):
```python
result = predict_galvanic_corrosion("HY80", "SS316", 25.0, 8.0, 19000.0, 1.0)
# current_ratio: 1.0379 (correctly shows acceleration)
# anode_CR: 0.00287 mm/yr (realistic)
```

---

## Files Modified

### Core Fixes
- `utils/nrl_materials.py` - Temperature conversion fix (line 289)
- `tools/mechanistic/predict_galvanic_corrosion.py` - Galvanic solver fix (multiple locations)

### Tests Updated
- `tests/test_phase2_galvanic.py` - Test expectations updated for correct kinetics

### Documentation
- `docs/CRITICAL_BUG_FIXES_2025-10-20.md` - This document
- `README.md` - To be updated with fix summary

---

## Acknowledgments

**Bug Discovery and Fixes**: Codex AI (Anthropic)
**Investigation Sessions**:
- Session 1: Temperature unit bug (2025-10-20, 08:04 UTC)
- Session 2: Galvanic solver bug (2025-10-20, follow-up)

**Codex provided**:
1. Root cause analysis (checked NRL MATLAB reference)
2. Physical interpretation (mixed potential theory)
3. Code fixes (temperature conversion, current definition)
4. Validation strategy (area ratio sweep)

---

## References

1. NRL MATLAB Reference: `external/nrl_matlab_reference/`
2. NRL Coefficients: `external/nrl_coefficients/`
3. Codex Session Logs: `/home/hvksh/.codex/sessions/2025/10/20/`
4. Phase 2 Test Suite: `tests/test_phase2_galvanic.py`

---

**Status**: ✅ RESOLVED
**Production Ready**: YES
**All Tests Passing**: 44/44 Phase 2 tests
