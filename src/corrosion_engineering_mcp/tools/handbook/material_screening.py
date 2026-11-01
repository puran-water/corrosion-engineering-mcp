"""
Tier 0 Tool: Material Compatibility Screening

Uses semantic search on corrosion_kb to quickly screen materials for
compatibility with specified environments.

Example Query:
    "316 stainless steel compatibility with seawater at 60°C"

Returns:
    - Compatibility rating (acceptable/marginal/not_recommended)
    - Typical rate ranges from handbooks
    - Warnings and recommendations
    - Source citations
"""

from typing import Dict, Any, List, Optional
from corrosion_engineering_mcp.core.schemas import MaterialCompatibility, ProvenanceMetadata, ConfidenceLevel
from corrosion_engineering_mcp.core.interfaces import HandbookLookup
from corrosion_engineering_mcp.core.errors import CorrosionPublicError
from corrosion_engineering_mcp.core.materials import find_material_ids, try_canonical_id
from corrosion_engineering_mcp.tools.handbook.engine import HandbookCorpusAdapter
import logging

# This will be imported from MCP client context
# For now, we'll define the interface
try:
    from mcp import search_corrosion_kb  # Will be available via MCP server context
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    logging.warning("MCP corrosion_kb not available - CORROSION_CORPUS_PATH required")


class MaterialScreeningLookup(HandbookLookup):
    """
    Material compatibility screening via semantic search.

    Queries corrosion handbooks for material-environment compatibility,
    typical corrosion rates, and usage recommendations.
    """

    def __init__(self, mcp_search_function=None):
        """
        Initialize material screening tool.

        Args:
            mcp_search_function: Function to call corrosion_kb semantic search
                                 (will be injected by MCP server context)
        """
        self._logger = logging.getLogger(__name__)
        self._corpus = HandbookCorpusAdapter(mcp_search_function)

    def query(
        self,
        query_text: str,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Query handbook for material compatibility.

        Args:
            query_text: Natural language query describing material and environment
            filters: Optional filters (not used in current implementation)

        Returns:
            Dictionary matching MaterialCompatibility schema
        """
        try:
            search_results = self._corpus.search(query_text, mode="rerank", top_k=5)

            # Parse results into structured format
            parsed = self._parse_results(search_results, query_text)

            return parsed

        except CorrosionPublicError:
            raise
        except Exception as e:
            self._logger.error(f"Material screening query failed: {e}")
            raise RuntimeError(f"Material screening error: {e}")

    def _parse_results(
        self,
        search_results: List[Dict[str, Any]],
        query_text: str,
    ) -> Dict[str, Any]:
        """
        Parse semantic search results into MaterialCompatibility schema.

        Extracts:
        - Compatibility rating from handbook text
        - Typical rate ranges
        - Recommendations and warnings
        """
        # Extract material and environment from query
        # (Simplified - would use NLP in production)
        material, environment = self._extract_material_environment(query_text)

        # Analyze top results for compatibility indicators
        compatibility = self._assess_compatibility(search_results)
        rate_range = self._extract_rate_range(search_results)
        notes = self._compile_notes(search_results)
        sources = self._extract_sources(search_results)

        return {
            "material": material,
            "environment": environment,
            "compatibility": compatibility,
            "typical_rate_range": rate_range,
            "notes": notes,
            "provenance": {
                "model": "kb.material_screening",
                "version": "1.0.0",
                "validation_dataset": None,
                "confidence": self._assess_confidence(search_results),
                "sources": sources,
                "assumptions": ["Handbook data represents typical service conditions"],
                "warnings": self._extract_warnings(search_results),
            }
        }

    def _extract_material_environment(self, query_text: str) -> tuple[str, str]:
        """Extract material and environment from query text"""
        matches = find_material_ids(query_text)
        material = matches[0] if matches else "unknown"

        # Environment is the full query for now
        environment = query_text

        return material, environment

    def _assess_compatibility(self, results: List[Dict[str, Any]]) -> str:
        """
        Assess compatibility from search results.

        Returns: "acceptable", "marginal", or "not_recommended"
        """
        # Analyze top results for positive/negative indicators
        positive_indicators = ["acceptable", "suitable", "recommended", "good performance", "resistant"]
        negative_indicators = ["not recommended", "avoid", "unsuitable", "severe", "rapid corrosion", "failure"]
        marginal_indicators = ["marginal", "limited", "with caution", "requires monitoring"]

        text_combined = " ".join([r.get("text", "") for r in results[:3]]).lower()

        # Count indicators
        negative_count = sum(1 for ind in negative_indicators if ind in text_combined)
        positive_count = sum(1 for ind in positive_indicators if ind in text_combined)
        marginal_count = sum(1 for ind in marginal_indicators if ind in text_combined)

        if negative_count > positive_count:
            return "not_recommended"
        elif marginal_count > 0 or (positive_count == negative_count):
            return "marginal"
        else:
            return "acceptable"

    def _extract_rate_range(self, results: List[Dict[str, Any]]) -> Optional[tuple[float, float]]:
        """
        Extract typical corrosion rate range from results.

        Looks for patterns like "0.1-0.3 mm/y" or "2-5 mpy" in text.
        """
        import re

        # Pattern for rate ranges (simplified)
        pattern = r'(\d+\.?\d*)\s*-\s*(\d+\.?\d*)\s*(mm/y|mpy|ipy)'

        for result in results[:5]:
            text = result.get("text", "")
            match = re.search(pattern, text)
            if match:
                min_rate = float(match.group(1))
                max_rate = float(match.group(2))
                unit = match.group(3)

                # Convert to mm/y if needed
                # mpy (mils/year): 1 mil = 0.0254 mm, so mpy / 39.37 = mm/y
                # ipy (inches/year): 1 inch = 25.4 mm, so ipy * 25.4 = mm/y
                if unit == "mpy":
                    min_rate /= 39.37
                    max_rate /= 39.37
                elif unit == "ipy":
                    # FIXED: was /= 39.37 (wrong by 1000×)
                    min_rate *= 25.4
                    max_rate *= 25.4

                return (min_rate, max_rate)

        return None

    def _compile_notes(self, results: List[Dict[str, Any]]) -> str:
        """Compile detailed notes from top results"""
        notes = []
        for i, result in enumerate(results[:3], 1):
            text = result.get("text", "")[:200]  # First 200 chars
            notes.append(f"[Source {i}] {text}...")

        return "\n\n".join(notes)

    def _extract_sources(self, results: List[Dict[str, Any]]) -> List[str]:
        """Extract source citations from results"""
        sources = []
        for result in results[:5]:
            path = result.get("path", "")
            if path:
                # Extract filename and offset
                source = f"{path}"
                if "offset" in result:
                    source += f" (offset {result['offset']})"
                sources.append(source)

        return sources[:3]  # Top 3 sources

    def _extract_warnings(self, results: List[Dict[str, Any]]) -> List[str]:
        """Extract warnings from search results"""
        warnings = []
        warning_keywords = ["warning", "caution", "avoid", "not recommended", "severe"]

        for result in results[:5]:
            text = result.get("text", "").lower()
            for keyword in warning_keywords:
                if keyword in text:
                    warnings.append(f"Handbook mentions: {keyword}")
                    break

        return warnings

    def _assess_confidence(self, results: List[Dict[str, Any]]) -> str:
        """Assess confidence based on search result quality"""
        if not results:
            return "low"

        # Check if top results have high relevance scores
        top_score = results[0].get("score", 0) if results else 0

        if top_score > 0.8:
            return "high"
        elif top_score > 0.6:
            return "medium"
        else:
            return "low"

# ============================================================================
# MCP Tool Function
# ============================================================================

def material_screening_query(
    environment: str,
    candidates: List[str],
    application: Optional[str] = None,
    mcp_search_function=None,
) -> MaterialCompatibility:
    """
    Screen materials for compatibility with specified environment.

    This is the main MCP tool function that will be registered with FastMCP.

    Args:
        environment: Environment description (e.g., "seawater, 35 g/L Cl, 25°C")
        candidates: List of material identifiers to screen (e.g., ["CS", "316L", "duplex"])
        application: Optional application description (e.g., "heat_exchanger_tubes")
        mcp_search_function: Injected semantic search function

    Returns:
        MaterialCompatibility object with screening results

    Example:
        result = material_screening_query(
            environment="CO2-rich brine, 60°C, 35 g/L Cl, pCO2=0.5 bar",
            candidates=["CS", "316L", "duplex"],
            application="piping"
        )
        print(result.compatibility)  # "acceptable", "marginal", or "not_recommended"
    """
    lookup = MaterialScreeningLookup(mcp_search_function)

    # Screen all candidates and return results for best/most compatible option
    # For now, combine all candidates in a single query to get comprehensive coverage
    resolved_candidates = [
        try_canonical_id(candidate) or candidate
        for candidate in candidates
    ]
    materials_str = ", ".join(resolved_candidates) if resolved_candidates else "carbon_steel"

    query_text = f"{materials_str} corrosion in {environment}"
    if application:
        query_text += f" for {application} application"

    result_dict = lookup.query(query_text)

    # Override material field to show all candidates screened
    if candidates:
        result_dict["material"] = materials_str
        result_dict["environment"] = f"{materials_str} corrosion in {environment}"

    # Convert to Pydantic model
    return MaterialCompatibility(**result_dict)
