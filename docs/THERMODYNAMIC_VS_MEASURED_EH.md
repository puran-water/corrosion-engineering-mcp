# Thermodynamic vs. Measured Eh: Important Distinction

## Summary

The `RedoxState` module calculates **thermodynamic Eh** (equilibrium redox potential) using the Nernst equation, which is the **correct value for Pourbaix diagram applications**. However, measured ORP (Oxidation-Reduction Potential) from field electrodes is typically 150-300 mV **lower** than thermodynamic Eh due to kinetic limitations.

---

## Thermodynamic Eh (Calculated by RedoxState)

**What it represents**: The redox potential at **thermodynamic equilibrium** for the Oxygen Reduction Reaction (ORR):

```
O₂ + 2H₂O + 4e⁻ → 4OH⁻
E⁰ = +1.229 V vs SHE at pH 0
```

**Nernst Equation**:
```
Eh = E⁰ - (0.059 V/pH) × pH + (RT/4F) × ln(p_O₂)
```

**Example**: Aerated water at pH 8.1, DO = 8 mg/L, T = 25°C
- **Calculated thermodynamic Eh**: +740 mV vs SHE
- **Pourbaix diagram O₂/H₂O line**: +740 mV ✅ (correct)

**Use case**:
- Pourbaix diagram construction (immunity/passivation/corrosion regions)
- Thermodynamic stability assessment
- Material selection based on equilibrium stability

---

## Measured Eh (Field ORP Electrodes)

**What it represents**: The **mixed potential** established by multiple redox couples at the electrode surface, influenced by:
1. **Slow ORR kinetics** (activation overpotential)
2. **Mass transfer limitations** (oxygen diffusion)
3. **Competing redox couples** (Fe²⁺/Fe³⁺, organics, sulfides, etc.)
4. **Biological activity** (O₂ consumption, biofilms)

**Typical values**: 150-300 mV **lower** than thermodynamic Eh

**Example**: Aerated wastewater at pH 7.5, DO = 7 mg/L
- **Calculated thermodynamic Eh**: ~750 mV vs SHE
- **Measured field ORP**: 400-600 mV vs SHE (Revie 2011)
- **Difference**: -150 to -350 mV (kinetic depression)

---

## Why the Difference?

### 1. Slow ORR Kinetics

The oxygen reduction reaction is **kinetically slow** on most electrode surfaces (Pt, Ag/AgCl, etc.). The reaction requires:
- Breaking O=O double bond (high activation energy)
- 4-electron transfer process
- Water coordination

**Result**: The electrode potential is depressed below thermodynamic equilibrium due to activation overpotential.

### 2. Mixed Potential

Real systems have **multiple redox couples**:
- O₂/H₂O (oxidizing)
- Fe²⁺/Fe³⁺ (intermediate)
- Organic matter oxidation (reducing)
- Sulfide/sulfate (reducing)
- Biological electron transfer

The measured ORP is a **weighted average** of all active couples, not just ORR.

### 3. Mass Transfer Limitations

In quiescent or low-flow systems:
- Oxygen diffusion to electrode is slow
- Depletion zone forms at electrode surface
- Local O₂ concentration < bulk concentration

**Result**: Measured potential reflects local (lower) O₂, not bulk DO.

---

## Codex Validation (2025-10-19)

From Codex MCP review:

> "The current `do_to_eh` implementation is solving the ORR Nernst equation at equilibrium, so the ~0.8 V result you see for aerobic water is the **thermodynamic Eh that Pourbaix diagrams expect**. Field probes read a **mixed-potential ORP that is depressed by activation overpotentials and mass-transfer limits**, so measured Eh routinely sits 150–300 mV lower."

**Recommendation**:
- Keep thermodynamic calculation for Pourbaix use case
- Document the gap clearly
- Optionally expose a helper that converts to "expected ORP" by subtracting a configurable kinetic offset

---

## When to Use Each Value

### Use Thermodynamic Eh (RedoxState output):

✅ **Pourbaix diagram construction**
- Determining immunity/passivation/corrosion regions
- Comparing materials at equilibrium
- Theoretical stability assessment

✅ **Material selection based on thermodynamics**
- "Can this material corrode?" (yes/no at equilibrium)
- Galvanic series position
- Anodic/cathodic behavior prediction

### Use Measured ORP (field electrodes):

✅ **Process control and monitoring**
- Wastewater treatment plant redox control
- Anaerobic digester optimization
- Biofilm detection

✅ **Empirical corrosion rate correlations**
- Some empirical models correlate measured ORP to corrosion rate
- Site-specific calibrations

✅ **Comparison to field data**
- Validating sensor readings
- Troubleshooting sensor drift

---

## Conversion: Thermodynamic → Expected ORP

If you need to estimate what an ORP electrode **might** read given thermodynamic conditions:

```python
def estimate_expected_orp(
    thermodynamic_eh_VSHE: float,
    kinetic_offset_mV: float = -200,  # Typical depression
    reference_electrode: ReferenceElectrode = ReferenceElectrode.SCE,
) -> float:
    """
    Estimate expected ORP reading from thermodynamic Eh.

    Args:
        thermodynamic_eh_VSHE: Calculated thermodynamic Eh (V vs SHE)
        kinetic_offset_mV: Depression due to kinetics (default -200 mV)
        reference_electrode: ORP electrode reference

    Returns:
        Expected ORP reading (mV vs reference)
    """
    # Apply kinetic depression
    expected_eh_VSHE = thermodynamic_eh_VSHE + (kinetic_offset_mV / 1000)

    # Convert to ORP vs reference electrode
    expected_orp_mV = eh_to_orp(expected_eh_VSHE, reference_electrode)

    return expected_orp_mV
```

**Note**: The kinetic offset varies widely (±100 mV) depending on:
- Electrode material (Pt vs Ag/AgCl)
- Electrode age and fouling
- Flow velocity
- Water chemistry
- Temperature

---

## Literature Values

### Aerated Systems

| System | pH | DO (mg/L) | Thermodynamic Eh | Measured ORP | Reference |
|--------|-----|-----------|------------------|--------------|-----------|
| Seawater | 8.1 | 8.0 | +740 mV | +400-500 mV | Morris & Stumm (1967) |
| Aerated WW | 7.5 | 6-8 | +750 mV | +400-600 mV | Revie (2011) |
| Freshwater stream | 7.0 | 10 | +800 mV | +500-650 mV | Hem (1989) |

### Anaerobic Systems

| System | pH | DO (mg/L) | Thermodynamic Eh | Measured ORP | Reference |
|--------|-----|-----------|------------------|--------------|-----------|
| Anaerobic digester | 7.2 | <0.01 | N/A (HER-controlled) | -200 to -400 mV | Revie (2011) |
| Sulfate-reducing | 7.0 | <0.01 | N/A (S²⁻/SO₄²⁻) | -250 to -350 mV | Postgate (1984) |

**Note**: In truly anaerobic systems (DO < 0.1 mg/L), ORR is **not the controlling reaction**. Hydrogen evolution (HER) or sulfate reduction dominates, so thermodynamic ORR Eh is not meaningful.

---

## RedoxState Tool Warnings

The `do_to_eh()` function automatically warns when assumptions may not hold:

```python
Eh, warnings = do_to_eh(0.01, pH=7.2, temperature_C=35.0)

# warnings[0]:
"DO < 0.01 mg/L (anaerobic conditions). Eh calculation assumes ORR
equilibrium, which may not apply in anaerobic environments where
hydrogen evolution reaction (HER) or sulfate reduction may dominate."
```

---

## References

1. **Codex AI Review** (2025-10-19). Corrosion Engineering MCP Phase 2 validation.

2. **Pourbaix, M.** (1974). *Atlas of Electrochemical Equilibria in Aqueous Solutions* (2nd ed.). NACE.

3. **Revie, R. W.** (2011). *Uhlig's Corrosion Handbook* (3rd ed.). Wiley. Chapter 6: "Electrochemical Techniques in Corrosion Science and Engineering."

4. **Hem, J. D.** (1989). "Study and interpretation of the chemical characteristics of natural water" (USGS Water Supply Paper 2254).

5. **Morris, J. C., & Stumm, W.** (1967). "Redox equilibria and measurements of potentials in the aquatic environment". *Advances in Chemistry Series*, 67, 270-285.

6. **Postgate, J. R.** (1984). *The Sulphate-Reducing Bacteria* (2nd ed.). Cambridge University Press.

---

## Conclusion

**The RedoxState module correctly calculates thermodynamic Eh for Pourbaix diagram applications.** The ~740 mV value for aerated water at pH 8 is the **equilibrium ORR potential**, not an error. Measured ORP from field electrodes will typically be 150-300 mV lower due to kinetic limitations, which is expected and well-documented in the literature.

For Pourbaix diagram integration, **always use the thermodynamic Eh** from `do_to_eh()`. For comparison to field ORP sensors, apply an empirical kinetic offset as needed.
