"""
Corrosion Engineering MCP Server

FastMCP server providing physics-based corrosion engineering tools for AI agents.

Architecture:
- Tier 0: Handbook lookup via semantic search (<0.5 sec)
- Tier 1: Chemistry via PHREEQC/phreeqpython (1 sec)
- Tier 2: Mechanistic physics models (1-5 sec)
- Tier 3: Uncertainty quantification via Monte Carlo (5-10 sec)

Phase 0 Implementation:
- 3 Tier 0 tools (material_screening, typical_rates, mechanism_guidance)
- Plugin architecture foundation
- Validation framework

Future Phases:
- Phase 1: PHREEQC + NORSOK M-506 + aerated chloride
- Phase 2: Galvanic + Pourbaix + coating barriers
- Phase 3: CUI + MIC + FAC + stainless screening
- Phase 4: MULTICORP + Monte Carlo UQ

Usage:
    python server.py

    # Or with custom port
    python server.py --port 8765
"""

from fastmcp import FastMCP
import logging
from typing import List, Optional

# Import Tier 0 tools
from tools.handbook.material_screening import material_screening_query
from tools.handbook.typical_rates import typical_rates_query
from tools.handbook.mechanism_guidance import mechanism_guidance_query

# Import schemas for type hints
from core.schemas import (
    MaterialCompatibility,
    TypicalRateResult,
    MechanismGuidance,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# Initialize FastMCP Server
# ============================================================================

mcp = FastMCP("Corrosion Engineering")

# ============================================================================
# Tier 0: Handbook Lookup Tools
# ============================================================================

@mcp.tool()
def screen_materials(
    environment: str,
    candidates: List[str],
    application: Optional[str] = None,
) -> MaterialCompatibility:
    """
    Screen materials for compatibility with specified environment.

    Uses semantic search on corrosion handbooks (2,980 vector chunks) to quickly
    assess material-environment compatibility and provide typical rate ranges.

    Args:
        environment: Environment description (e.g., "seawater, 35 g/L Cl, 25°C")
        candidates: List of material identifiers (e.g., ["CS", "316L", "duplex"])
        application: Optional application (e.g., "heat_exchanger_tubes")

    Returns:
        MaterialCompatibility with rating, rate range, and handbook sources

    Example:
        result = screen_materials(
            environment="CO2-rich brine, 60°C, pCO2=0.5 bar",
            candidates=["CS", "316L"],
            application="piping"
        )
        print(result.compatibility)  # "acceptable", "marginal", "not_recommended"
        print(result.typical_rate_range)  # (0.1, 0.3) mm/y
    """
    logger.info(f"Screening materials {candidates} for environment: {environment}")

    # Note: mcp_search_function will be injected when corrosion_kb MCP integration is complete
    # For now, uses placeholder search
    result = material_screening_query(
        environment=environment,
        candidates=candidates,
        application=application,
        mcp_search_function=None,  # Will be injected in production
    )

    logger.info(f"Screening complete: {result.material} → {result.compatibility}")
    return result


@mcp.tool()
def query_typical_rates(
    material: str,
    environment_summary: str,
) -> TypicalRateResult:
    """
    Query handbook for typical corrosion rates.

    Searches corrosion handbooks for empirical rate data reported in literature
    for the specified material-environment combination.

    Args:
        material: Material identifier (e.g., "CS", "316L", "duplex")
        environment_summary: Brief description (e.g., "seawater, 25°C, stagnant")

    Returns:
        TypicalRateResult with rate range (min, max, typical) and sources

    Example:
        result = query_typical_rates(
            material="CS",
            environment_summary="CO2-rich brine, 60°C, pH 6.8"
        )
        print(f"Typical: {result.rate_typical_mm_per_y:.3f} mm/y")
        print(f"Range: {result.rate_min_mm_per_y:.3f} - {result.rate_max_mm_per_y:.3f}")
    """
    logger.info(f"Querying typical rates for {material} in {environment_summary}")

    result = typical_rates_query(
        material=material,
        environment_summary=environment_summary,
        mcp_search_function=None,  # Will be injected in production
    )

    logger.info(f"Typical rate: {result.rate_typical_mm_per_y:.3f} mm/y")
    return result


@mcp.tool()
def identify_mechanism(
    material: str,
    symptoms: List[str],
    environment: Optional[str] = None,
) -> MechanismGuidance:
    """
    Identify probable corrosion mechanisms and get mitigation guidance.

    Uses semantic search to match symptoms to corrosion mechanisms and provide
    handbook-based recommendations for testing and mitigation.

    Args:
        material: Material identifier (e.g., "304_SS", "CS")
        symptoms: Observed symptoms (e.g., ["localized_attack", "pits", "crevices"])
        environment: Optional environment description

    Returns:
        MechanismGuidance with probable mechanisms, recommendations, and tests

    Example:
        result = identify_mechanism(
            material="304_SS",
            symptoms=["localized_attack", "pits"],
            environment="cooling water, 500 mg/L Cl"
        )
        print(result.probable_mechanisms)  # ["pitting", "crevice"]
        print(result.recommendations)  # ["Upgrade to 316L", "Reduce chloride", ...]
        print(result.tests_recommended)  # ["Potentiodynamic polarization", ...]
    """
    logger.info(f"Identifying mechanism for {material} with symptoms: {symptoms}")

    result = mechanism_guidance_query(
        material=material,
        symptoms=symptoms,
        environment=environment,
        mcp_search_function=None,  # Will be injected in production
    )

    logger.info(f"Probable mechanisms: {result.probable_mechanisms}")
    return result


# ============================================================================
# Tier 1: Chemistry Tools (Phase 1 - Not Yet Implemented)
# ============================================================================

# @mcp.tool()
# def run_phreeqc_speciation(...):
#     """Phase 1: PHREEQC speciation via phreeqpython"""
#     pass

# @mcp.tool()
# def calculate_pourbaix(...):
#     """Phase 2: Pourbaix diagram generation"""
#     pass


# ============================================================================
# Tier 2: Mechanistic Physics Tools (Phase 1-3 - Not Yet Implemented)
# ============================================================================

# @mcp.tool()
# def predict_co2_h2s_corrosion(...):
#     """Phase 1: CO2/H2S corrosion via NORSOK M-506 + MULTICORP"""
#     pass

# @mcp.tool()
# def predict_aerated_chloride_corrosion(...):
#     """Phase 1: O2-limited corrosion via Chilton-Colburn"""
#     pass

# @mcp.tool()
# def predict_galvanic_corrosion(...):
#     """Phase 2: Galvanic corrosion via NRL mixed-potential"""
#     pass

# @mcp.tool()
# def calculate_coating_throughput(...):
#     """Phase 2: Coating transport via Zargarnezhad"""
#     pass

# @mcp.tool()
# def predict_cui_risk(...):
#     """Phase 3: CUI via DNV-RP-G109"""
#     pass

# @mcp.tool()
# def assess_mic_risk(...):
#     """Phase 3: MIC risk assessment"""
#     pass

# @mcp.tool()
# def predict_fac_rate(...):
#     """Phase 3: Flow-accelerated corrosion"""
#     pass

# @mcp.tool()
# def screen_stainless_pitting(...):
#     """Phase 3: Stainless steel pitting via PREN/CPT"""
#     pass

# @mcp.tool()
# def calculate_dewpoint(...):
#     """Phase 3: Psychrometrics via PsychroLib"""
#     pass


# ============================================================================
# Tier 3: Uncertainty Quantification (Phase 4 - Not Yet Implemented)
# ============================================================================

# @mcp.tool()
# def propagate_uncertainty_monte_carlo(...):
#     """Phase 4: Monte Carlo uncertainty propagation"""
#     pass


# ============================================================================
# Server Information
# ============================================================================

@mcp.tool()
def get_server_info() -> dict:
    """
    Get corrosion engineering MCP server information.

    Returns:
        Dictionary with server version, capabilities, and tool count

    Example:
        info = get_server_info()
        print(f"Phase: {info['phase']}")
        print(f"Available tools: {info['tool_count']}")
    """
    return {
        "name": "Corrosion Engineering MCP Server",
        "version": "0.1.0",
        "phase": "Phase 0 (Tier 0 Handbook Tools)",
        "tool_count": 4,  # 3 Tier 0 tools + get_server_info
        "architecture": "4-Tier (Handbook → Chemistry → Physics → Uncertainty)",
        "total_tools_planned": 15,
        "knowledge_base": "2,980 vector chunks from corrosion handbooks",
        "implemented_tiers": ["Tier 0: Handbook"],
        "planned_tiers": ["Tier 1: Chemistry", "Tier 2: Mechanistic Physics", "Tier 3: Uncertainty"],
        "github": "https://github.com/puran-water/corrosion-engineering-mcp",
        "documentation": "See README.md for detailed architecture and roadmap",
    }


# ============================================================================
# Run Server
# ============================================================================

if __name__ == "__main__":
    logger.info("=" * 70)
    logger.info("Corrosion Engineering MCP Server - Phase 0")
    logger.info("=" * 70)
    logger.info("Implemented Tools:")
    logger.info("  [Tier 0] screen_materials - Material compatibility screening")
    logger.info("  [Tier 0] query_typical_rates - Handbook rate lookup")
    logger.info("  [Tier 0] identify_mechanism - Mechanism identification")
    logger.info("  [Info] get_server_info - Server information")
    logger.info("=" * 70)
    logger.info("Future Phases:")
    logger.info("  Phase 1: PHREEQC + NORSOK M-506 + aerated Cl")
    logger.info("  Phase 2: Galvanic + Pourbaix + coating barriers")
    logger.info("  Phase 3: CUI + MIC + FAC + stainless screening")
    logger.info("  Phase 4: MULTICORP + Monte Carlo UQ")
    logger.info("=" * 70)

    # Run the server
    mcp.run()
