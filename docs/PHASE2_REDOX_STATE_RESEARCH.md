# Phase 2: RedoxState Module - Research Findings

## Objective
Create a `RedoxState` helper module to unify DO (dissolved oxygen), ORP (oxidation-reduction potential), and Eh (redox potential vs SHE) calculations across all tools.

**Goal**: Enable Pourbaix diagram tool to accept `dissolved_oxygen_mg_L` parameter and automatically convert to Eh.

---

## GitHub Search Results

### Relevant Repositories Found

| Repository | Stars | License | Relevance |
|------------|-------|---------|-----------|
| **python-gsw** (TEOS-10/python-gsw) | 46 | MIT | Seawater thermodynamics (TEOS-10), but NO redox functions |
| **pyEQL** (KingsburyLab/pyEQL) | 71 | Other | Solution chemistry with `pE` support, but NO DO→Eh conversion |
| **phreeqpython** (Vitens/phreeqpython) | 81 | - | PHREEQC wrapper - has `pe` but relies on PHREEQC equilibration |
| **water-chemistry-mcp** (puran-water) | 1 | MIT | MCP server for PHREEQC - full speciation but heavyweight |
| **reaktoro-pse** (watertap-org) | 6 | Other | Reaktoro + Pyomo integration - overkill for simple DO→Eh |

### Search Conclusion

**NO authoritative Python implementation found for direct DO→Eh conversion.**

Most packages either:
1. **Require full geochemical equilibration** (PHREEQC, Reaktoro) - too heavyweight for simple DO→Eh
2. **Provide Nernst equation infrastructure** (pyEQL, our NRL code) but not pre-built DO→Eh
3. **Focus on other domains** (gsw for seawater density/salinity, not redox)

---

## Recommended Approach: Lightweight Custom Implementation

### Rationale

1. **We already have Nernst equation** in `utils/nrl_electrochemical_reactions.py:145`
2. **DO→Eh is a SINGLE half-reaction** (oxygen reduction reaction, ORR):
   ```
   O₂ + 2H₂O + 4e⁻ → 4OH⁻
   ```
3. **Lightweight calculation** (10-20 lines) vs heavyweight dependency (PHREEQC, Reaktoro)
4. **DRY principle**: Reuse our existing Nernst infrastructure

### Implementation Plan

Create `utils/redox_state.py` with:

1. **DO saturation calculation** using Henry's law
   - Can leverage literature constants or simple correlation
   - Temperature-dependent solubility

2. **DO→Eh conversion** using ORR Nernst equation
   - Reuse `nrl_electrochemical_reactions.py` Nernst logic
   - Standard potential: E⁰(O₂/H₂O) = +1.23 V_SHE (at pH 0)
   - pH-dependent adjustment

3. **ORP→Eh reference correction**
   - ORP typically measured vs Ag/AgCl or calomel
   - Simple offset: Eh_SHE = ORP_measured + E_ref

---

## Literature Constants

### ORR Thermodynamics

| Parameter | Value | Source |
|-----------|-------|--------|
| E⁰(O₂/H₂O, pH=0) | +1.23 V_SHE | Pourbaix (1974), Revie (2011) |
| E⁰(O₂/H₂O, pH=7) | +0.82 V_SHE | Standard biochemistry tables |
| Slope | -0.059 V/pH unit | Nernst (25°C) |
| DO saturation (25°C, 1 atm) | 8.3 mg/L | USGS tables |

### Henry's Law for O₂

```
C_O2(aq) = K_H * p_O2
```

- K_H (25°C) = 1.3×10⁻³ mol/(L·atm)
- Temperature dependence: van't Hoff equation

**Reference**: Sander (2015) "Compilation of Henry's law constants"

---

## DO→Eh Conversion Equation

```python
# Nernst equation for ORR at 25°C
Eh_SHE = 1.23 - 0.059*pH + (RT/4F)*ln(p_O2)

# Where:
# - 1.23 V = standard potential at pH 0
# - -0.059*pH = pH correction (0.059 V per pH unit)
# - (RT/4F)*ln(p_O2) = oxygen activity correction
# - p_O2 = partial pressure of O2 (atm)
```

For dissolved oxygen (mg/L):
```python
# 1. Convert DO (mg/L) to partial pressure using Henry's law
p_O2 = DO_mg_L / (32000 * K_H)  # 32000 = MW_O2 * 1000

# 2. Apply Nernst equation
Eh_SHE = 1.23 - 0.059*pH + (0.059/4)*np.log10(p_O2)
```

---

## Validation Data

### Literature Eh-DO Correlations (Wastewater)

From **Revie (2011), Table 6.3** "Redox Potential in Wastewater Systems":

| Environment | DO (mg/L) | pH | Eh (mV_SHE) | Notes |
|-------------|-----------|----|-----------|----|
| Aerated wastewater | 6-8 | 7.5 | +400 to +600 | ORR-dominated |
| Facultative zone | 0.5-2 | 7.0 | 0 to +200 | Mixed redox |
| Anaerobic digester | <0.1 | 7.2 | -200 to -400 | HER-dominated |

### Test Cases for Validation

1. **Aerated seawater** (DO=8 mg/L, pH=8.1, T=25°C)
   - Expected: Eh ≈ +400 mV_SHE

2. **Anaerobic digester** (DO=0.01 mg/L, pH=7.2, T=35°C)
   - Expected: Eh ≈ -300 mV_SHE (not ORR-controlled, but HER)

3. **Freshwater stream** (DO=10 mg/L, pH=7.0, T=15°C)
   - Expected: Eh ≈ +500 mV_SHE

---

## Design Decisions

### 1. **Standalone Module** vs Tool Integration

**Decision**: Create standalone `utils/redox_state.py` module

**Rationale**:
- Reusable across multiple tools (Pourbaix, galvanic, material screening)
- Testable in isolation
- Lightweight dependency

### 2. **Full Equilibration** vs **Single Half-Reaction**

**Decision**: Use single half-reaction (ORR) Nernst equation

**Rationale**:
- DO→Eh conversion assumes ORR equilibrium (valid for aerated systems)
- Anaerobic systems (DO≈0) are NOT ORR-controlled → tool should warn user
- Full equilibration (PHREEQC) is overkill for this simple conversion

### 3. **Henry's Law Implementation**

**Decision**: Use simple temperature-dependent Henry's constant

**Rationale**:
- Salinity effects <10% for typical wastewater (TDS <5 g/L)
- Can add salinity correction later if needed
- Simple van't Hoff equation for T-dependence

---

## Implementation Checklist

- [ ] Create `utils/redox_state.py` module
- [ ] Implement `RedoxState` dataclass with fields: `DO_mg_L`, `Eh_VSHE`, `pH`, `T_C`
- [ ] Implement `do_to_eh(do_mg_L, pH, T_C)` function
- [ ] Implement `eh_to_do(eh_VSHE, pH, T_C)` (inverse)
- [ ] Implement `orp_to_eh(orp_mV, ref_electrode)` (reference correction)
- [ ] Add Henry's law helper with T-dependence
- [ ] Write unit tests against literature values
- [ ] Integrate with `generate_pourbaix_diagram` tool
- [ ] Update tool docstrings with new DO-aware capability

---

## Next Steps

1. **Implement `utils/redox_state.py`** (2-3 hours)
2. **Validate against literature** (1 hour)
3. **Integrate with Pourbaix tool** (1 hour)
4. **Test with anaerobic digester scenario** (30 min)

Total estimate: **4-5 hours**

---

## References

1. Pourbaix, M. (1974). *Atlas of Electrochemical Equilibria in Aqueous Solutions*
2. Revie, R. W. (2011). *Uhlig's Corrosion Handbook* (3rd ed.), Chapter 6
3. Sander, R. (2015). "Compilation of Henry's law constants", *Atmos. Chem. Phys.*
4. USGS (2025). "Dissolved Oxygen Solubility Tables"
5. NRL Technical Report TN-7397 (2022). "Seawater Corrosion Kinetics"

---

## Conclusion

**Recommendation**: Implement lightweight custom `RedoxState` module using existing Nernst infrastructure rather than adding heavyweight dependencies (PHREEQC, Reaktoro, pyEQL).

This aligns with DRY principles while maintaining code simplicity and avoiding unnecessary complexity for a straightforward thermodynamic calculation.
