# JSON Response Schema Documentation

## Overview

All corrosion engineering MCP tools return standardized JSON responses with:
- **Central estimates** (median or nominal values)
- **Uncertainty bounds** (p05, p95 percentiles)
- **Provenance metadata** (model, validation dataset, sources, confidence)

This document provides the complete JSON schema for all 15 planned tools across 4 tiers.

---

## Design Philosophy

From Codex review:
> "Document JSON response schema early (median/p05/p95, metadata, provenance) so downstream agent frameworks can introspect uncertainty and move between tiers cleanly."

### Key Principles

1. **Uncertainty-first**: Always return intervals, never point values
2. **Provenance tracking**: Every result includes source model and validation basis
3. **Confidence levels**: Explicit HIGH/MEDIUM/LOW confidence ratings
4. **Source citations**: Handbook page references and literature sources
5. **Composability**: Results can be chained across tiers

---

## Common Schemas

### ProvenanceMetadata

All results include this provenance block:

```json
{
  "provenance": {
    "model": "string",              // e.g., "NORSOK_M506", "kb.material_screening"
    "version": "string | null",     // Model version or git commit
    "validation_dataset": "string | null",  // e.g., "OhioU_FREECORP"
    "confidence": "high | medium | low | unknown",
    "sources": ["string"],          // Literature citations or handbook pages
    "assumptions": ["string"],      // Key modeling assumptions
    "warnings": ["string"]          // Extrapolation warnings or cautions
  }
}
```

### Confidence Levels

- **HIGH**: Validated against >10 benchmarks, error <±30%
- **MEDIUM**: Validated against <10 benchmarks, error <±factor of 2
- **LOW**: Extrapolated beyond validation range, error >±factor of 2
- **UNKNOWN**: No validation data available

---

## Tier 0: Handbook Lookup

### MaterialCompatibility

**Tool**: `screen_materials`

**Purpose**: Material-environment compatibility screening

```json
{
  "material": "string",           // e.g., "316L", "CS"
  "environment": "string",         // Environment description
  "compatibility": "acceptable | marginal | not_recommended",
  "typical_rate_range": [float, float] | null,  // [min, max] in mm/y
  "notes": "string",               // Detailed notes from handbook
  "provenance": { ...ProvenanceMetadata }
}
```

**Example**:
```json
{
  "material": "316L",
  "environment": "seawater, 35 g/L Cl, 60°C",
  "compatibility": "marginal",
  "typical_rate_range": [0.01, 0.05],
  "notes": "At elevated temperatures (>50°C), pitting may occur...",
  "provenance": {
    "model": "kb.material_screening",
    "version": "1.0.0",
    "validation_dataset": null,
    "confidence": "high",
    "sources": ["Handbook of Corrosion Engineering, p. 234"],
    "assumptions": ["Handbook data represents typical service conditions"],
    "warnings": ["Temperature >60°C increases pitting risk"]
  }
}
```

### TypicalRateResult

**Tool**: `query_typical_rates`

**Purpose**: Empirical corrosion rate ranges from handbooks

```json
{
  "material": "string",
  "environment": "string",
  "rate_min_mm_per_y": float,      // Minimum reported rate
  "rate_max_mm_per_y": float,      // Maximum reported rate
  "rate_typical_mm_per_y": float,  // Typical/median rate
  "conditions": "string",           // Conditions for reported rates
  "provenance": { ...ProvenanceMetadata }
}
```

**Example**:
```json
{
  "material": "CS",
  "environment": "CO2-rich brine, 60°C, pH 6.8",
  "rate_min_mm_per_y": 0.12,
  "rate_max_mm_per_y": 0.25,
  "rate_typical_mm_per_y": 0.18,
  "conditions": "Temperature: 60°C; pH: 6.8; Velocity: <2 m/s",
  "provenance": {
    "model": "kb.typical_rates",
    "confidence": "high",
    "sources": ["The Corrosion Handbook, offset 2345"]
  }
}
```

### MechanismGuidance

**Tool**: `identify_mechanism`

**Purpose**: Mechanism identification and mitigation guidance

```json
{
  "probable_mechanisms": ["string"],     // e.g., ["pitting", "crevice"]
  "symptoms": ["string"],                 // Expected symptoms
  "recommendations": ["string"],          // Mitigation recommendations
  "tests_recommended": ["string"],        // Diagnostic tests
  "provenance": { ...ProvenanceMetadata }
}
```

**Example**:
```json
{
  "probable_mechanisms": ["pitting", "crevice"],
  "symptoms": ["localized pits", "undermined edges", "crevice attack"],
  "recommendations": [
    "Upgrade to duplex stainless steel",
    "Reduce chloride concentration",
    "Increase flow velocity to >1.5 m/s"
  ],
  "tests_recommended": [
    "Potentiodynamic polarization",
    "CPT determination",
    "Metallography of failed samples"
  ],
  "provenance": {
    "model": "kb.mechanism_guidance",
    "confidence": "high",
    "sources": ["Handbook of Corrosion Engineering, offset 3456"]
  }
}
```

---

## Tier 1: Chemistry

### SpeciationResult

**Tool**: `run_phreeqc_speciation` (Phase 1)

**Purpose**: Aqueous chemistry speciation via PHREEQC

```json
{
  "pH": float,
  "temperature_C": float,
  "ionic_strength": float,           // mol/L
  "activities": {                     // Species activities
    "H+": float,
    "HCO3-": float,
    "CO3-2": float,
    // ... more species
  },
  "concentrations_mg_L": {            // Species concentrations
    "H+": float,
    // ... more species
  },
  "saturation_indices": {             // Mineral saturation
    "FeCO3": float,
    "FeS": float,
    "CaCO3": float
  },
  "pe": float | null,
  "Eh_V": float | null,              // Redox potential vs SHE
  "provenance": { ...ProvenanceMetadata }
}
```

### PourbaixResult

**Tool**: `calculate_pourbaix` (Phase 2)

**Purpose**: Potential-pH stability assessment

```json
{
  "material": "string",
  "pH": float,
  "potential_V_SHE": float,
  "stable_phase": "string",          // e.g., "Fe2O3", "Fe3O4"
  "corrosion_region": "immunity | passivation | corrosion",
  "risk_assessment": "string",
  "provenance": { ...ProvenanceMetadata }
}
```

---

## Tier 2: Mechanistic Physics

### CorrosionResult

**Standard schema** for all mechanistic corrosion rate predictions:

```json
{
  "material": "string",
  "mechanism": "string",              // e.g., "uniform_CO2", "galvanic", "pitting"

  // Central estimate + uncertainty
  "rate_mm_per_y": float,            // Median rate (mm/y)
  "rate_p05_mm_per_y": float,        // 5th percentile (conservative)
  "rate_p95_mm_per_y": float,        // 95th percentile (optimistic)

  // Alternative units
  "rate_mpy": float | null,          // Rate in mils per year

  // Component contributions (if applicable)
  "rate_components": {
    "CO2": float,
    "HAc": float,
    "H2S": float,
    "O2": float
  } | null,

  // Process conditions
  "temperature_C": float,
  "environment_summary": "string",

  // Provenance
  "provenance": { ...ProvenanceMetadata }
}
```

**Example (CO2 corrosion)**:
```json
{
  "material": "CS",
  "mechanism": "uniform_CO2",
  "rate_mm_per_y": 0.15,
  "rate_p05_mm_per_y": 0.08,
  "rate_p95_mm_per_y": 0.25,
  "rate_mpy": 5.9,
  "rate_components": {
    "CO2": 0.12,
    "HAc": 0.0,
    "H2S": 0.02,
    "O2": 0.01
  },
  "temperature_C": 60,
  "environment_summary": "CO2-rich brine, pCO2=0.5 bar, pH 6.8",
  "provenance": {
    "model": "NORSOK_M506",
    "validation_dataset": "NORSOK_validation",
    "confidence": "high",
    "sources": ["NORSOK M-506 (2005)"]
  }
}
```

### GalvanicResult

**Tool**: `predict_galvanic_corrosion` (Phase 2)

```json
{
  "anode_material": "string",
  "cathode_material": "string",
  "mixed_potential_V_SCE": float,
  "galvanic_current_A_m2": float,

  // Anode corrosion rate
  "anode_rate_mm_per_y": float,
  "anode_rate_p05_mm_per_y": float,
  "anode_rate_p95_mm_per_y": float,

  // Geometry
  "area_ratio": float,              // Cathode/anode
  "sensitivity": {
    "area_ratio": "string"          // e.g., "10x cathode → 2.5x higher rate"
  },

  "provenance": { ...ProvenanceMetadata }
}
```

### CoatingBarrierResult

**Tool**: `calculate_coating_throughput` (Phase 2)

```json
{
  "coating_type": "string",          // e.g., "FBE", "epoxy"
  "thickness_um": float,

  // Oxygen flux
  "J_O2_mol_m2_s": float,
  "wet_blocking_factor": float,      // 0-1

  // Effective current
  "effective_i_lim_A_m2": float,

  // Environmental conditions
  "temperature_C": float,
  "relative_humidity_pct": float,

  "notes": "string",                 // e.g., "Plasticization effects considered"
  "provenance": { ...ProvenanceMetadata }
}
```

### CUIResult

**Tool**: `predict_cui_risk` (Phase 3)

```json
{
  "probability_of_failure_class": "High | Medium | Low | Very Low",

  // Rate estimate
  "rate_estimate_mm_per_y": float,
  "rate_p05_mm_per_y": float,
  "rate_p95_mm_per_y": float,

  // Drivers
  "wetness_fraction": float,         // 0-1
  "salt_load_mg_m2": float,

  // Barrier assessment
  "barrier_scores": {
    "coating": "good | medium | poor",
    "water_wetting": "good | medium | poor",
    "design": "good | medium | poor",
    "insulation": "good | medium | poor"
  },

  "method": "string",                // Methodology description
  "provenance": { ...ProvenanceMetadata }
}
```

### PittingScreenResult

**Tool**: `screen_stainless_pitting` (Phase 3)

```json
{
  "material": "string",
  "PREN": float,                     // Pitting Resistance Equivalent Number
  "CPT_estimate_C": float,           // Critical Pitting Temperature

  // Environment
  "chloride_g_L": float,
  "temperature_C": float,

  // Risk assessment
  "risk_band": "LOW | MEDIUM | HIGH | CRITICAL",
  "risk_notes": "string",

  // Recommendations
  "recommendations": ["string"],

  "provenance": { ...ProvenanceMetadata }
}
```

---

## Tier 3: Uncertainty Quantification

### MonteCarloResult

**Tool**: `propagate_uncertainty_monte_carlo` (Phase 4)

```json
{
  "model_called": "string",          // Tool that was wrapped
  "n_samples": int,

  // Output statistics
  "output_median": float,
  "output_p05": float,
  "output_p95": float,
  "output_mean": float,
  "output_std": float,

  // Sensitivity analysis
  "tornado": {                       // Sensitivity ranking
    "parameter_name": float          // Variance contribution
  },

  // Input distributions used
  "input_distributions": {
    "T_C": {
      "type": "normal",
      "mean": 60,
      "std": 5
    },
    "v_m_s": {
      "type": "uniform",
      "min": 1.5,
      "max": 2.5
    }
  },

  // Optional: full sample array
  "samples": [float] | null,

  "provenance": { ...ProvenanceMetadata }
}
```

**Example**:
```json
{
  "model_called": "corrosion.uniform.co2_h2s.rate",
  "n_samples": 1000,
  "output_median": 0.15,
  "output_p05": 0.08,
  "output_p95": 0.25,
  "output_mean": 0.16,
  "output_std": 0.05,
  "tornado": {
    "T_C": 0.12,
    "pCO2_bar": 0.08,
    "v_m_s": 0.03
  },
  "input_distributions": {
    "T_C": {"type": "normal", "mean": 60, "std": 5},
    "v_m_s": {"type": "uniform", "min": 1.5, "max": 2.5}
  },
  "samples": null,
  "provenance": {
    "model": "uq.monte_carlo",
    "confidence": "high",
    "sources": ["Latin Hypercube sampling via SALib"]
  }
}
```

---

## Validation

All schemas are implemented as Pydantic models in `core/schemas.py` with:
- Type validation
- Range checking (e.g., rate > 0, confidence in enum)
- Cross-field validation (p05 ≤ median ≤ p95)

See `core/schemas.py` for complete Pydantic definitions.

---

## Usage Example (Tool Chaining)

```python
# Step 1: Tier 0 - Quick screening
screen = screen_materials(
    environment="CO2-rich brine, 60°C, 35 g/L Cl",
    candidates=["CS", "316L"]
)

# Step 2: Tier 1 - Chemistry
spec = run_phreeqc_speciation(
    temperature_C=60,
    pressure_bar=10,
    water={"pH": 7.2},
    gases={"pCO2_bar": 0.5},
    ions={"Cl_mg_L": 35000}
)

# Step 3: Tier 2 - Physics
rate = predict_co2_h2s_corrosion(
    speciation_ref=spec,  # Reuse chemistry
    material="CS",
    T_C=60,
    v_m_s=2.0
)

# Step 4: Tier 3 - Uncertainty
uq = propagate_uncertainty_monte_carlo(
    model_call="corrosion.uniform.co2_h2s.rate",
    distributions={
        "T_C": {"type": "normal", "mean": 60, "std": 5}
    }
)

print(f"Rate: {uq.output_median:.3f} [{uq.output_p05:.3f}, {uq.output_p95:.3f}] mm/y")
```

---

**Last Updated**: Phase 0 implementation
**Next Review**: Phase 1 (after PHREEQC integration)
