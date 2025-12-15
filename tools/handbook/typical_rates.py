"""
Tier 0 Tool: Typical Corrosion Rates Lookup

Uses semantic search on corrosion_kb to retrieve empirical corrosion rate
data from handbooks for material-environment combinations.

Example Query:
    "Carbon steel corrosion rate in CO2-saturated brine at 60°C"

Returns:
    - Typical rate range (min, max, median)
    - Operating conditions for reported rates
    - Handbook sources with page references
"""

from typing import Dict, Any, List, Optional
from core.schemas import TypicalRateResult, ProvenanceMetadata, ConfidenceLevel
from core.interfaces import HandbookLookup
import logging
import re


class TypicalRatesLookup(HandbookLookup):
    """
    Typical corrosion rate lookup via semantic search.

    Queries corrosion handbooks for empirical rate data reported
    in tables and case studies.
    """

    def __init__(self, mcp_search_function=None):
        """
        Initialize typical rates lookup tool.

        Args:
            mcp_search_function: Function to call corrosion_kb semantic search
        """
        self._logger = logging.getLogger(__name__)
        self._mcp_search = mcp_search_function

    def query(
        self,
        query_text: str,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Query handbook for typical corrosion rates.

        Args:
            query_text: Natural language query describing material and environment
            filters: Optional filters

        Returns:
            Dictionary matching TypicalRateResult schema
        """
        try:
            # Call semantic search with focus on rate data
            search_query = self._enhance_query_for_rates(query_text)

            if self._mcp_search:
                search_results = self._mcp_search(
                    query=search_query,
                    mode="rerank",
                    top_k=8,  # More results for rate extraction
                )
            else:
                search_results = self._placeholder_search(query_text)

            # Parse and extract rate data
            parsed = self._parse_rate_results(search_results, query_text)

            return parsed

        except Exception as e:
            self._logger.error(f"Typical rates query failed: {e}")
            raise RuntimeError(f"Typical rates lookup error: {e}")

    def _enhance_query_for_rates(self, query_text: str) -> str:
        """Enhance query to target rate data in handbooks"""
        # Add keywords that help find rate tables
        if "rate" not in query_text.lower():
            query_text += " corrosion rate"

        return query_text

    def _parse_rate_results(
        self,
        search_results: List[Dict[str, Any]],
        query_text: str,
    ) -> Dict[str, Any]:
        """
        Parse semantic search results to extract rate ranges.

        Looks for:
        - Numerical rates in text (mm/y, mpy, ipy)
        - Tables with rate data
        - Operating conditions (temperature, velocity, etc.)
        """
        material, environment = self._extract_material_environment(query_text)

        # Extract all rates found in results
        rates_mm_per_y = self._extract_all_rates(search_results)

        if rates_mm_per_y:
            rate_min = min(rates_mm_per_y)
            rate_max = max(rates_mm_per_y)
            rate_typical = self._calculate_typical(rates_mm_per_y)
        else:
            # No rates found - return placeholder
            rate_min = 0.0
            rate_max = 0.0
            rate_typical = 0.0

        conditions = self._extract_conditions(search_results)
        sources = self._extract_sources(search_results)

        return {
            "material": material,
            "environment": environment,
            "rate_min_mm_per_y": rate_min,
            "rate_max_mm_per_y": rate_max,
            "rate_typical_mm_per_y": rate_typical,
            "conditions": conditions,
            "provenance": {
                "model": "kb.typical_rates",
                "version": "1.0.0",
                "validation_dataset": None,
                "confidence": "high" if rates_mm_per_y else "low",
                "sources": sources,
                "assumptions": ["Handbook rates are typical values, not design values"],
                "warnings": self._generate_warnings(rates_mm_per_y, rate_max),
            }
        }

    def _extract_material_environment(self, query_text: str) -> tuple[str, str]:
        """Extract material and environment from query"""
        query_lower = query_text.lower()

        # Material extraction (simplified)
        materials_map = {
            "carbon steel": "CS",
            "316": "316L",
            "304": "304",
            "duplex": "duplex",
            "cs": "CS",
        }

        material = "unknown"
        for key, val in materials_map.items():
            if key in query_lower:
                material = val
                break

        # Environment extraction
        environment = query_text
        return material, environment

    def _extract_all_rates(self, results: List[Dict[str, Any]]) -> List[float]:
        """
        Extract all corrosion rates from search results.

        Patterns matched:
        - "0.15 mm/y"
        - "2-5 mpy"
        - "0.007 ipy"
        """
        rates_mm_per_y = []

        # Regular expressions for rate extraction
        patterns = [
            r'(\d+\.?\d*)\s*(mm/y|mm/yr)',  # mm/y format
            r'(\d+\.?\d*)\s*(mpy)',          # mpy format
            r'(\d+\.?\d*)\s*(ipy)',          # ipy format
        ]

        for result in results:
            text = result.get("text", "")

            for pattern in patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                for match in matches:
                    rate_value = float(match[0])
                    unit = match[1].lower()

                    # Convert to mm/y
                    # mpy (mils/year): 1 mil = 0.0254 mm, so mpy / 39.37 = mm/y
                    # ipy (inches/year): 1 inch = 25.4 mm, so ipy * 25.4 = mm/y
                    if unit == "mpy":
                        rate_mm_y = rate_value / 39.37
                    elif unit == "ipy":
                        # FIXED: was / 39.37 (wrong by 1000×)
                        rate_mm_y = rate_value * 25.4
                    else:
                        rate_mm_y = rate_value

                    # Sanity check (rates typically < 10 mm/y for service)
                    if 0 < rate_mm_y < 50:
                        rates_mm_per_y.append(rate_mm_y)

        return rates_mm_per_y

    def _calculate_typical(self, rates: List[float]) -> float:
        """Calculate typical (median) rate from extracted values"""
        if not rates:
            return 0.0

        sorted_rates = sorted(rates)
        n = len(sorted_rates)

        if n % 2 == 0:
            return (sorted_rates[n//2 - 1] + sorted_rates[n//2]) / 2
        else:
            return sorted_rates[n//2]

    def _extract_conditions(self, results: List[Dict[str, Any]]) -> str:
        """Extract operating conditions description from results"""
        conditions = []

        # Look for temperature mentions
        temp_pattern = r'(\d+)\s*°C'
        for result in results[:3]:
            text = result.get("text", "")
            temp_match = re.search(temp_pattern, text)
            if temp_match:
                conditions.append(f"Temperature: {temp_match.group(1)}°C")

        # Look for velocity mentions
        vel_pattern = r'(\d+\.?\d*)\s*(m/s|ft/s)'
        for result in results[:3]:
            text = result.get("text", "")
            vel_match = re.search(vel_pattern, text)
            if vel_match:
                conditions.append(f"Velocity: {vel_match.group(0)}")

        # Look for pH mentions
        ph_pattern = r'pH\s*[=:~]?\s*(\d+\.?\d*)'
        for result in results[:3]:
            text = result.get("text", "")
            ph_match = re.search(ph_pattern, text, re.IGNORECASE)
            if ph_match:
                conditions.append(f"pH: {ph_match.group(1)}")

        if not conditions:
            return "Conditions not specified in handbook excerpts"

        return "; ".join(conditions[:3])

    def _extract_sources(self, results: List[Dict[str, Any]]) -> List[str]:
        """Extract source citations"""
        sources = []
        for result in results[:5]:
            path = result.get("path", "")
            if path:
                source = path
                if "offset" in result:
                    source += f" (offset {result['offset']})"
                sources.append(source)

        return sources[:3]

    def _generate_warnings(self, rates: List[float], rate_max: float) -> List[str]:
        """Generate warnings based on extracted rates"""
        warnings = []

        if not rates:
            warnings.append("No numerical rates found in handbook search - use with caution")

        if rate_max > 1.0:
            warnings.append(f"Maximum rate ({rate_max:.2f} mm/y) indicates aggressive environment")

        if len(rates) < 3:
            warnings.append("Limited data points - consider validation with additional sources")

        return warnings

    def _placeholder_search(self, query_text: str) -> List[Dict[str, Any]]:
        """Placeholder for development"""
        return [
            {
                "text": "Carbon steel in CO2-saturated brine at 60°C shows corrosion rates ranging from "
                        "0.12 to 0.25 mm/y (4.7 to 9.8 mpy) under quiescent conditions. Higher velocities "
                        "(>2 m/s) can increase rates by 2-3x due to protective scale removal.",
                "score": 0.88,
                "path": "the_corrosion_handbook.pdf",
                "offset": 2345,
            },
            {
                "text": "At pH 6.5-7.0 and 60°C, uniform CO2 corrosion of carbon steel proceeds at approximately "
                        "0.15 mm/yr in the absence of protective FeCO3 scale formation.",
                "score": 0.82,
                "path": "handbook_of_corrosion_engineering.pdf",
                "offset": 6789,
            },
        ]


# ============================================================================
# MCP Tool Function
# ============================================================================

def typical_rates_query(
    material: str,
    environment_summary: str,
    mcp_search_function=None,
) -> TypicalRateResult:
    """
    Query handbook for typical corrosion rates.

    This is the main MCP tool function that will be registered with FastMCP.

    Args:
        material: Material identifier (e.g., "CS", "316L")
        environment_summary: Brief environment description (e.g., "seawater, 25°C, stagnant")
        mcp_search_function: Injected semantic search function

    Returns:
        TypicalRateResult object with rate range and sources

    Example:
        result = typical_rates_query(
            material="CS",
            environment_summary="CO2-rich brine, 60°C, pH 6.8"
        )
        print(f"Typical rate: {result.rate_typical_mm_per_y:.3f} mm/y")
        print(f"Range: {result.rate_min_mm_per_y:.3f} - {result.rate_max_mm_per_y:.3f} mm/y")
    """
    lookup = TypicalRatesLookup(mcp_search_function)

    query_text = f"{material} corrosion rate in {environment_summary}"

    result_dict = lookup.query(query_text)

    return TypicalRateResult(**result_dict)
