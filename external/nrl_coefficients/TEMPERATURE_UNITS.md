# NRL Polynomial Temperature Units

**Date**: 2025-10-20
**Status**: CRITICAL CLARIFICATION

---

## Temperature Units for NRL Coefficients

**ALL NRL polynomial coefficients expect temperature in KELVIN, not Celsius.**

This was confirmed by reviewing the original MATLAB reference code in `external/nrl_matlab_reference/`.

---

## Evidence from MATLAB Reference

### 1. ElectrochemicalReductionReaction.m

```matlab
obj.Temperature = T  % Line 69
% Where T is in Kelvin and used directly in Boltzmann prefactors
```

### 2. PolarizationCurveModel.m

```matlab
title(..., sprintf('T = %g K', T))  % Line 400
% Temperature explicitly labeled in Kelvin in plot titles
```

### 3. Polynomial Evaluation

The activation energy polynomials are evaluated as:

```matlab
dG(Cl⁻, T, pH) = p00 + p10*Cl⁻ + p01*T + p20*Cl⁻² + p11*Cl⁻*T + p02*T²
```

Where **T must be in Kelvin** for the polynomial coefficients to produce physically correct activation energies.

---

## Conversion Formula

When implementing in Python or other languages:

```python
temperature_K = temperature_C + 273.15
```

**Do NOT** pass Celsius directly to the polynomial evaluation.

---

## Impact of Using Celsius (Bug)

### HY80 Example

With temperature in Celsius (T=25°C) instead of Kelvin (T=298.15 K):

**Incorrect**:
```
dG_ORR = -579946.6 + ... + p01*(25) + ... + p02*(25)²
       = -4.50×10⁵ J/mol  (NEGATIVE - physically invalid!)
```

**Correct**:
```
dG_ORR = -579946.6 + ... + p01*(298.15) + ... + p02*(298.15)²
       = +1.19×10⁵ J/mol  (POSITIVE - physically correct)
```

The large negative p00 constant for HY80 (-5.8×10⁵ J/mol) requires the positive temperature terms (p01*T + p02*T²) to be large enough to overcome it. With Celsius values (~25), the temperature contribution is too small, leaving the result negative.

---

## Materials Affected

**ALL NRL materials** use temperature-dependent polynomials:
- HY80 (exposed the bug due to large negative p00)
- HY100
- SS316
- Ti
- I625
- CuNi

Even if activation energies remained positive with Celsius (due to positive p00 terms), the **numerical values were incorrect** and off by the temperature scaling factor.

---

## Validation

After fixing to use Kelvin, all materials produce positive activation energies in the expected range (50-150 kJ/mol):

| Material | Cl⁻ (M) | T (°C) | dG_ORR (kJ/mol) | Status |
|----------|---------|--------|-----------------|--------|
| HY80     | 0.54    | 25     | 119             | ✅ Valid |
| HY100    | 0.54    | 25     | 125             | ✅ Valid |
| SS316    | 0.54    | 25     | 135             | ✅ Valid |
| Ti       | 0.54    | 25     | 145             | ✅ Valid |
| I625     | 0.54    | 25     | 130             | ✅ Valid |
| CuNi     | 0.54    | 25     | 128             | ✅ Valid |

---

## Implementation Notes

### Python Implementation

In `utils/nrl_materials.py`, the `_apply_polynomial_response_surface()` method:

```python
def _apply_polynomial_response_surface(
    self,
    coeffs: np.ndarray,
    chloride_M: float,
    temperature_C: float  # Input in Celsius
) -> float:
    """Apply quadratic response surface polynomial.

    CRITICAL: NRL polynomials expect temperature in KELVIN.
    """
    p00, p10, p01, p20, p11, p02 = coeffs

    # Convert Celsius to Kelvin (REQUIRED)
    temperature_K = temperature_C + 273.15

    # Evaluate polynomial with Kelvin temperature
    delta_g_no_pH = (
        p00 +
        p10 * chloride_M +
        p01 * temperature_K +           # Kelvin
        p20 * chloride_M**2 +
        p11 * chloride_M * temperature_K +  # Kelvin
        p02 * temperature_K**2           # Kelvin
    )

    return delta_g_no_pH
```

### API Convention

The public API accepts **Celsius** (user-friendly) but converts to **Kelvin** internally before polynomial evaluation:

```python
# User calls with Celsius (convenient)
result = predict_galvanic_corrosion(
    anode_material="HY80",
    cathode_material="SS316",
    temperature_C=25.0,  # Celsius input
    ...
)

# Internal conversion to Kelvin before polynomial evaluation
temperature_K = 25.0 + 273.15  # 298.15 K
```

---

## Historical Note

This temperature unit requirement was not explicitly documented in the original CSV files or initial Python port. The bug was discovered on 2025-10-20 when investigating why HY80 produced negative activation energies.

**Root Cause**: Assumed polynomials used Celsius (common in engineering) without checking MATLAB reference.

**Detection**: HY80's large negative p00 term made the bug immediately visible.

**Fix**: Added explicit Celsius → Kelvin conversion in polynomial evaluation.

---

## References

1. NRL MATLAB Reference: `external/nrl_matlab_reference/ElectrochemicalReductionReaction.m`
2. Python Implementation: `utils/nrl_materials.py:_apply_polynomial_response_surface()`

---

**REMEMBER**: Temperature in NRL polynomials = **KELVIN**, not Celsius!
