"""Literature-anchored chloride response for the suppressed E_pit model.

The hybrid calculation's absolute E_pit intercept is unavailable. The retained
Galvele 88 mV/decade chloride response is public only for the measured
Fe-18Cr-8Ni (Type-304) composition; no pitting risk classifier or recommendation
is exposed from an absolute value.
"""

from corrosion_engineering_mcp.utils.localized_criteria import GALVELE_1976_CHLORIDE_SLOPE_CITATION_RECORD


class GalveleMaterialScopeMismatchError(ValueError):
    """The measured Type-304 chloride slope cannot be transferred by alloy name."""

    def __init__(self, queried_material: str) -> None:
        self.code = "GALVELE_MATERIAL_SCOPE_MISMATCH"
        self.details = {
            "reason_code": self.code,
            "queried_material": queried_material,
            "measured_material": "Fe-18Cr-8Ni (Type 304 composition)",
            "matching_identifiers": ["304", "SS304", "Fe-18Cr-8Ni"],
            "source": "Galvele 1976 Table III, doi:10.1149/1.2132857",
            "source_record": {
                **GALVELE_1976_CHLORIDE_SLOPE_CITATION_RECORD,
                "authors": list(
                    GALVELE_1976_CHLORIDE_SLOPE_CITATION_RECORD["authors"]
                ),
            },
            "transfer_to_other_alloys": "not_permitted",
        }
        super().__init__(
            "GALVELE_MATERIAL_SCOPE_MISMATCH: the 88 mV/decade chloride "
            "response was measured on Fe-18Cr-8Ni (Type 304 composition), not "
            f"{queried_material!r}; transfer to another alloy is suppressed."
        )


class AbsolutePittingPotentialSuppressedError(ValueError):
    """The private hybrid absolute E_pit calculation is unavailable."""

    def __init__(self, queried_material: str) -> None:
        self.code = "ABSOLUTE_PITTING_POTENTIAL_SUPPRESSED"
        self.details = {
            "reason_code": self.code,
            "queried_material": queried_material,
            "suppressed_output": "absolute_E_pit_VSCE",
            "retained_alternative": (
                "pitting_potential_chloride_response_mV_per_decade_for_"
                "Fe-18Cr-8Ni_Type_304_only"
            ),
        }
        super().__init__(
            "ABSOLUTE_PITTING_POTENTIAL_SUPPRESSED: the hybrid NRL/Galvele "
            "absolute E_pit calculation has no independent per-alloy anchor and "
            "is unavailable even for the matching Type-304 slope scope. Only "
            "the sourced 88 mV/decade chloride response is retained."
        )


def _unvalidated_absolute_pitting_potential(
    material_name: str,
    temperature_C: float,
    chloride_mg_L: float,
    pH: float,
    i_threshold_A_cm2: float = 1e-6,
) -> None:
    """Compatibility tombstone for the withdrawn absolute E_pit helper.

    The old implementation hybridized an NRL response surface, a Galvele
    chloride shift, a changed Nernst convention, and an invented threshold
    current. It returned that absolute number for HY80, HY100, and SS316 while
    transferring Galvele's Type-304 slope to each alloy.

    Only a Type-304 identifier reaches the absolute-value suppression. Every
    other material fails the Galvele scope gate first. The numeric parameters
    remain solely for call compatibility and are never evaluated into a value.
    """
    normalized = material_name.upper().replace("_", "").replace(" ", "")
    if normalized not in {"304", "SS304", "FE-18CR-8NI", "FE18CR8NI"}:
        raise GalveleMaterialScopeMismatchError(material_name)

    raise AbsolutePittingPotentialSuppressedError(material_name)


def pitting_potential_chloride_response_mV_per_decade(
    material_name: str,
    temperature_C: float = 25.0,
    pH: float = 7.0,
    Cl_ref_mg_L: float = 100.0,
) -> float:
    """Return the measured Type-304 chloride response, never an alloy transfer.

    The absolute pitting potential is suppressed everywhere (see
    `_unvalidated_absolute_pitting_potential`), but the chloride response is
    anchored: Galvele 1976 Table III measures 88 mV/decade for Fe-18Cr-8Ni in
    NaCl. Fe-18Cr-8Ni is a Type-304 composition; 316/316L, HY80, HY100 and all
    other queried materials fail typed rather than inheriting that slope.

    The retained temperature, pH and reference-chloride parameters are validated
    for backward call compatibility, but do not change the measured slope.
    """
    normalized = material_name.upper().replace("_", "").replace(" ", "")
    if normalized not in {"304", "SS304", "FE-18CR-8NI", "FE18CR8NI"}:
        raise GalveleMaterialScopeMismatchError(material_name)
    if not 0.0 <= temperature_C <= 150.0:
        raise ValueError(
            f"Temperature {temperature_C}°C out of valid range (0-150°C)"
        )
    if not 1.0 <= pH <= 13.0:
        raise ValueError(f"pH {pH} out of valid range (1-13)")
    if Cl_ref_mg_L <= 0.0:
        raise ValueError("Cl_ref_mg_L must be positive")

    from corrosion_engineering_mcp.utils.localized_criteria import B_CL_FE18CR8NI_NACL_V_PER_DECADE

    return B_CL_FE18CR8NI_NACL_V_PER_DECADE * 1000.0


if __name__ == "__main__":
    print("=" * 70)
    print('corrosion_engineering_mcp.utils.pitting_assessment')
    print("=" * 70)
    print(
        "\nAbsolute E_pit is NOT reported by this module's demo and is "
        "suppressed wherever the server consumes it.\n"
        "Only the Galvele Type-304 chloride response is retained.\n"
    )
    print("Chloride response (anchored only for Fe-18Cr-8Ni / Type 304):")
    slope = pitting_potential_chloride_response_mV_per_decade("SS304")
    print(f"  d(E_pit)/d(log10 Cl) = {slope:.1f} mV/decade")
    print("  expected 88 mV/decade [Galvele 1976 Table III, Fe-18Cr-8Ni in NaCl]")
