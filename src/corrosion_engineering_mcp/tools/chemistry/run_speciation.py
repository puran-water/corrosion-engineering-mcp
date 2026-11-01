"""
Tier 1 Tool: Run PHREEQC Aqueous Speciation

Calculates equilibrium speciation of aqueous solutions including:
- species distribution at a required caller-reported pH
- Major species molalities (mol per kg of water)
- Saturation indices for minerals (calcite, gypsum, etc.)
- Ionic strength
- Alkalinity

Performance: ~1 second
The accessor does not calculate pH from charge balance.
"""

from typing import Dict, Optional
import logging

from corrosion_engineering_mcp.core.chemistry_backend import PHREEQCBackend, parse_ions_json, validate_water_chemistry

logger = logging.getLogger(__name__)


def run_phreeqc_speciation(
    ions_json: str,
    temperature_C: float = 25.0,
    pH: Optional[float] = None,
    pe: float = 4.0,
    validate_charge_balance: bool = True,
    max_imbalance: float = 5.0,
) -> Dict:
    'Run PHREEQC aqueous speciation calculation.\n\n    Args:\n        ions_json: JSON string of ion concentrations in mg/L\n                   Example: \'{"Na+": 1000.0, "Cl-": 1500.0, "Ca2+": 100.0, "HCO3-": 200.0}\'\n        temperature_C: Water temperature in degrees Celsius (default 25.0)\n        pH: Reported pH; required for this calculation\n        pe: Redox potential, dimensionless (default 4.0 for oxic conditions)\n        validate_charge_balance: Check charge balance before running (default True)\n        max_imbalance: Maximum acceptable charge imbalance in % (default 5.0)\n\n    Example:\n        >>> result = run_phreeqc_speciation(\n        ...     ions_json=\'{"Na+": 1000, "Cl-": 1500, "Ca2+": 100, "HCO3-": 200}\',\n        ...     temperature_C=25.0,\n        ...     pH=7.5\n        ... )\n        >>> print(result["pH"])\n        7.5\n        >>> print(result["saturation_indices"]["Calcite"])\n        -0.15\n\n    Raises:\n        ValueError: If charge imbalance exceeds max_imbalance\n        RuntimeError: If PHREEQC calculation fails'
    ions = parse_ions_json(ions_json)
    if pH is None:
        raise ValueError("A reported pH is required for PHREEQC speciation")

    # Validate charge balance if requested.  A failed check is retained in the
    # returned contract; logging alone is invisible to an MCP caller.
    charge_balance_warning: Optional[str] = None
    charge_balance_valid: Optional[bool] = None
    if validate_charge_balance:
        try:
            validate_water_chemistry(ions, max_imbalance=max_imbalance)
            charge_balance_valid = True
        except ValueError as e:
            charge_balance_valid = False
            charge_balance_warning = f"CHARGE_BALANCE_FAILED: {e}"
            logger.warning(f"Charge balance validation failed: {e}")

    # Run PHREEQC speciation
    backend = PHREEQCBackend()
    result = backend.run_speciation(
        ions=ions,
        temperature_C=temperature_C,
        pH=pH,
        pe=pe,
    )

    # Exact values are authoritative.  Rounded companions are explicitly named
    # displays so small species and signed saturation indices cannot disappear.
    output = {
        "pH": result.pH,
        "pH_display": round(result.pH, 3),
        "pH_status": "reported",
        "pe": result.pe,
        "pe_display": round(result.pe, 3),
        "temperature_C": temperature_C,
        "ionic_strength_M": result.ionic_strength_M,
        "ionic_strength_M_display": round(result.ionic_strength_M, 6),
        "alkalinity_mg_L_CaCO3": result.alkalinity_mg_L_CaCO3,
        "alkalinity_mg_L_CaCO3_display": round(
            result.alkalinity_mg_L_CaCO3, 2
        ),
        "species": dict(result.species),
        
        "species_basis": "mol/kgw",
        "species_display": {
            species: round(conc, 9) for species, conc in result.species.items()
        },
        "saturation_indices": dict(result.saturation_indices),
        "saturation_indices_display": {
            mineral: round(si, 3) for mineral, si in result.saturation_indices.items()
        },
        "charge_balance_percent": result.charge_balance_percent,
        "charge_balance_percent_display": round(result.charge_balance_percent, 2),
        "value_standing": (
            "exact_primaries_authoritative_display_fields_rounded"
        ),
        "charge_balance_valid": charge_balance_valid,
        # the database that answered and the screened minerals it does not define (D5 of
        
        "phreeqc_database": result.database,
        "saturation_indices_not_in_database": list(result.saturation_indices_not_in_database),
        "input_validation_standing": (
            "passed"
            if charge_balance_valid is True
            else "failed_charge_balance"
            if charge_balance_valid is False
            else "not_checked"
        ),
        "warnings": [charge_balance_warning] if charge_balance_warning else [],
    }

    # Add interpretation
    interpretation_parts = []

    # pH interpretation
    if result.pH < 4.0:
        interpretation_parts.append("Highly acidic solution (pH < 4.0)")
    elif result.pH < 7.0:
        interpretation_parts.append("Acidic solution (pH < 7.0)")
    elif result.pH > 10.0:
        interpretation_parts.append("Highly alkaline solution (pH > 10.0)")
    elif result.pH > 8.5:
        interpretation_parts.append("Alkaline solution (pH > 8.5)")
    else:
        interpretation_parts.append(f"Near-neutral pH ({result.pH:.2f})")

    # Saturation index interpretation
    si_calcite = result.saturation_indices.get("Calcite")
    if si_calcite is None:
        interpretation_parts.append("Calcite saturation index was not returned")
    elif si_calcite > 0.5:
        interpretation_parts.append(
            "Supersaturated with calcite "
            f"(SI = {repr(float(si_calcite))}) - scaling likely"
        )
    elif si_calcite < -0.5:
        interpretation_parts.append(
            "Undersaturated with calcite "
            f"(SI = {repr(float(si_calcite))})"
        )

    # Ionic strength interpretation
    if result.ionic_strength_M > 0.5:
        interpretation_parts.append(
            "High ionic strength "
            f"({repr(float(result.ionic_strength_M))} M) - saline water"
        )
    elif result.ionic_strength_M < 0.001:
        interpretation_parts.append(
            "Low ionic strength "
            f"({repr(float(result.ionic_strength_M))} M) - freshwater"
        )

    output["interpretation"] = ". ".join(interpretation_parts)

    return output
