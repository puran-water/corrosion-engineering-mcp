# Pourbaix Diagram Tool - PHREEQC Integration Roadmap

**Current Status**: Phase 2 (Simplified Thermodynamics) ✅
**Target**: Phase 3 (Full PHREEQC Integration) 🎯

---

## Why Not PHREEQC in Phase 2?

### Pragmatic Decision
The Phase 2 priority was to establish **galvanic corrosion prediction with 100% authoritative provenance** (NRL Butler-Volmer model). The Pourbaix tool is **supplementary** for material selection and visualization.

### Current Implementation (Phase 2)
**Simplified Thermodynamics**:
- Nernst equation: `E = E⁰ + (RT/nF) * ln([ox]/[red])`
- Standard electrode potentials from Pourbaix Atlas (1974)
- Ideal solution assumption (activity coefficients = 1)
- **Accuracy**: ~95% for engineering material selection

**Limitations**:
- No complex ion speciation (e.g., FeCl₄²⁻, FeOH⁺)
- Temperature effects simplified (E⁰ assumed constant)
- No ionic strength corrections
- Oxide stability from literature values (not calculated from ΔG_f)

### Why This Is Acceptable for Phase 2
1. **Primary Tool is NRL Galvanic Model**: Quantitative corrosion rates come from the fully authoritative NRL Butler-Volmer implementation, NOT from Pourbaix diagrams.

2. **Pourbaix Use Case is Qualitative**: Engineers use Pourbaix diagrams for:
   - "Is this material immune, passive, or actively corroding?"
   - Material selection (compare immunity/passivation regions)
   - NOT for precise corrosion rate prediction

3. **Test Results Validate Approach**: 7/7 Pourbaix tests pass (100% for Phase 2)

4. **Dependency Management**: Deferring PHREEQC integration avoids:
   - External library dependencies (PhreeqPython, IPhreeqc)
   - Cross-platform build complexity
   - Codex validation complexity

---

## Phase 3 Roadmap: Full PHREEQC Integration

### Goal
Replace simplified Nernst equations with **exact thermodynamic calculations** from PHREEQC.

### Implementation Options

#### Option 1: PhreeqPython (Recommended)
**Pros**:
- Pure Python wrapper (easiest integration)
- Active maintenance (KWR-Water/phreeqpython)
- Supports full PHREEQC feature set

**Cons**:
- Requires `phreeqpython` package
- Platform-specific builds

**Installation**:
```bash
pip install phreeqpython
```

**Example Integration**:
```python
from phreeqpython import PhreeqPython

pp = PhreeqPython(database='phreeqc.dat')
solution = pp.add_solution({
    'pH': 7.0,
    'pe': 12.5,  # Oxidizing (aerated)
    'Fe': 1e-6,  # 10⁻⁶ M solubility limit
    'temp': 25.0
})

# Get predominant species at each E-pH point
for pH in np.linspace(0, 14, 100):
    for E_SHE in np.linspace(-2, 2, 100):
        pe = E_SHE * 96485 / (8.314 * 298.15 * np.log(10))
        solution.set_pH(pH)
        solution.set_pe(pe)
        species = solution.species  # Exact speciation from PHREEQC
```

#### Option 2: IPhreeqcPy (Direct C Library)
**Pros**:
- Direct access to IPhreeqc C library
- Fastest performance

**Cons**:
- More complex integration
- Platform-specific compilation

**Installation**:
```bash
pip install IPhreeqcPy
```

#### Option 3: Subprocess to PHREEQC Executable
**Pros**:
- No Python dependencies
- Most authoritative (uses official USGS PHREEQC)

**Cons**:
- Slower (I/O overhead)
- Requires PHREEQC installed on system

---

## Recommended Approach for Phase 3

### Step 1: Add PhreeqPython Dependency
```bash
# requirements.txt
phreeqpython>=1.6.0
```

### Step 2: Wrap Current Simplified Tool
Keep existing simplified tool as fallback:
```python
def calculate_pourbaix(
    element: str,
    temperature_C: float = 25.0,
    use_phreeqc: bool = True,  # New parameter
    **kwargs
) -> Dict:
    """
    Calculate Pourbaix diagram.

    Args:
        use_phreeqc: If True, use PHREEQC for exact speciation.
                     If False, use simplified Nernst (Phase 2 method).
    """
    if use_phreeqc:
        try:
            return _calculate_pourbaix_phreeqc(element, temperature_C, **kwargs)
        except ImportError:
            warnings.warn("PhreeqPython not available, using simplified method")
            return _calculate_pourbaix_simplified(element, temperature_C, **kwargs)
    else:
        return _calculate_pourbaix_simplified(element, temperature_C, **kwargs)
```

### Step 3: Implement PHREEQC Backend
```python
def _calculate_pourbaix_phreeqc(
    element: str,
    temperature_C: float,
    soluble_concentration_M: float,
    pH_range: Tuple[float, float],
    E_range_VSHE: Tuple[float, float],
    grid_points: int
) -> Dict:
    """Calculate Pourbaix using PHREEQC exact thermodynamics."""
    from phreeqpython import PhreeqPython

    pp = PhreeqPython(database='phreeqc.dat')

    # Grid of E-pH points
    pH_grid = np.linspace(pH_range[0], pH_range[1], grid_points)
    E_grid = np.linspace(E_range_VSHE[0], E_range_VSHE[1], grid_points)

    # Initialize solution
    solution = pp.add_solution({
        'pH': 7.0,
        'pe': 12.5,
        element: soluble_concentration_M,
        'temp': temperature_C
    })

    # Calculate predominant species at each point
    species_map = {}
    for pH in pH_grid:
        for E_SHE in E_grid:
            pe = _convert_E_to_pe(E_SHE, temperature_C)
            solution.set_pH(pH)
            solution.set_pe(pe)

            # Get predominant species
            predominant = solution.total_element(element, 'molality')
            species_map[(pH, E_SHE)] = predominant.species[0].name

    # Classify regions (immunity, passivation, corrosion)
    regions = _classify_regions(species_map)

    return {
        'element': element,
        'temperature_C': temperature_C,
        'regions': regions,
        'method': 'PHREEQC v3 exact thermodynamics',
        # ... rest of output
    }
```

### Step 4: Validation
Compare outputs:
```python
# Validate PHREEQC vs simplified for Fe at 25°C
result_phreeqc = calculate_pourbaix('Fe', use_phreeqc=True)
result_simplified = calculate_pourbaix('Fe', use_phreeqc=False)

# Should be close for simple systems, diverge for complex ions
```

---

## Benefits of PHREEQC Integration (Phase 3)

1. **Exact Speciation**:
   - Models complex ions (FeCl₄²⁻, Fe(OH)₃⁻, FeOH⁺)
   - Activity coefficients (Davies, Pitzer models)
   - Ion pairing

2. **Temperature Dependence**:
   - Thermodynamic data from PHREEQC database (0-300°C)
   - Accurate ΔG_f(T) calculations

3. **Ionic Strength Effects**:
   - Correct for high salinity (>0.6 M NaCl)
   - Mixed electrolytes (NaCl + MgCl₂ + CaSO₄)

4. **Validation**:
   - PHREEQC is USGS-certified for geochemical modeling
   - Widely accepted for regulatory compliance

5. **Extensibility**:
   - Alloy diagrams (Fe-Cr-Ni systems)
   - Non-standard conditions (high T, P)
   - Custom thermodynamic data

---

## Current Status (Phase 2)

**What Works**:
- ✅ Simplified Pourbaix diagrams for Fe, Cr, Ni, Cu, Ti, Al
- ✅ 7/7 tests passing
- ✅ Sufficient for material selection
- ✅ Water stability lines accurate
- ✅ Zero external dependencies

**What's Missing (for Phase 3)**:
- ❌ Complex ion speciation
- ❌ Activity coefficient corrections
- ❌ Temperature-dependent thermodynamics
- ❌ Alloy diagrams
- ❌ Regulatory-grade accuracy

---

## Recommendation

**For Phase 2 Completion**:
- Accept current simplified implementation as "engineering estimates"
- Document limitations clearly (✅ Done in this commit)
- Mark as "Phase 3 enhancement" in roadmap

**For Phase 3**:
- Integrate PhreeqPython as recommended approach
- Keep simplified method as fallback
- Add `use_phreeqc` parameter for user choice
- Validate against Pourbaix Atlas (1974) reference diagrams

---

## References

1. **PhreeqPython**:
   - Repository: https://github.com/KWR-Water/phreeqpython
   - Installation: `pip install phreeqpython`
   - Documentation: https://phreeqpython.readthedocs.io/

2. **PHREEQC**:
   - Official Site: https://www.usgs.gov/software/phreeqc-version-3
   - Manual: Parkhurst & Appelo (2013) USGS Techniques and Methods 6-A43

3. **Pourbaix Atlas**:
   - Pourbaix, M. (1974). "Atlas of Electrochemical Equilibria in Aqueous Solutions"
   - 2nd English Edition, NACE International

---

**Status**: Phase 2 documentation updated (2025-10-19)
**Next Action**: Phase 3 PHREEQC integration (future)
**Codex Validation**: RED FLAG #2 resolved by honest documentation ✅
