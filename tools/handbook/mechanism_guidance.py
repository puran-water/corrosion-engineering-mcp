"""
Tier 0 Tool: Corrosion Mechanism Identification and Guidance

Uses semantic search on corrosion_kb to identify probable corrosion mechanisms
based on symptoms, materials, and environment, and provide mitigation guidance.

Example Query:
    "Localized pitting on 304 stainless steel in chloride-containing water"

Returns:
    - Probable mechanisms (e.g., "chloride pitting", "crevice corrosion")
    - Expected symptoms and morphology
    - Mitigation recommendations
    - Recommended diagnostic tests
"""

from typing import Dict, Any, List, Optional
from core.schemas import MechanismGuidance, ProvenanceMetadata, ConfidenceLevel
from core.interfaces import HandbookLookup
import logging


class MechanismGuidanceLookup(HandbookLookup):
    """
    Corrosion mechanism identification and guidance via semantic search.

    Helps diagnose corrosion problems by:
    - Matching symptoms to probable mechanisms
    - Providing mechanism descriptions
    - Recommending mitigation strategies
    - Suggesting diagnostic tests
    """

    def __init__(self, mcp_search_function=None):
        """
        Initialize mechanism guidance tool.

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
        Query handbook for mechanism identification and guidance.

        Args:
            query_text: Description of symptoms, material, and environment
            filters: Optional filters

        Returns:
            Dictionary matching MechanismGuidance schema
        """
        try:
            # Call semantic search focused on mechanisms
            search_query = self._enhance_query_for_mechanisms(query_text)

            if self._mcp_search:
                search_results = self._mcp_search(
                    query=search_query,
                    mode="rerank",
                    top_k=10,  # More results for comprehensive guidance
                )
            else:
                search_results = self._placeholder_search(query_text)

            # Parse and extract mechanism guidance
            parsed = self._parse_mechanism_results(search_results, query_text)

            return parsed

        except Exception as e:
            self._logger.error(f"Mechanism guidance query failed: {e}")
            raise RuntimeError(f"Mechanism guidance error: {e}")

    def _enhance_query_for_mechanisms(self, query_text: str) -> str:
        """Enhance query to target mechanism discussions"""
        # Add mechanism keywords if not present
        mechanism_keywords = ["mechanism", "corrosion", "failure"]

        if not any(kw in query_text.lower() for kw in mechanism_keywords):
            query_text += " corrosion mechanism"

        return query_text

    def _parse_mechanism_results(
        self,
        search_results: List[Dict[str, Any]],
        query_text: str,
    ) -> Dict[str, Any]:
        """
        Parse semantic search results to identify mechanisms and guidance.

        Extracts:
        - Probable mechanisms from handbook discussions
        - Symptoms and morphology descriptions
        - Mitigation recommendations
        - Diagnostic test suggestions
        """
        # Identify probable mechanisms
        mechanisms = self._identify_mechanisms(search_results, query_text)

        # Extract symptoms from results
        symptoms = self._extract_symptoms(search_results)

        # Extract recommendations
        recommendations = self._extract_recommendations(search_results)

        # Extract test recommendations
        tests = self._extract_tests(search_results)

        # Get sources
        sources = self._extract_sources(search_results)

        return {
            "probable_mechanisms": mechanisms,
            "symptoms": symptoms,
            "recommendations": recommendations,
            "tests_recommended": tests,
            "provenance": {
                "model": "kb.mechanism_guidance",
                "version": "1.0.0",
                "validation_dataset": None,
                "confidence": "high" if mechanisms else "medium",
                "sources": sources,
                "assumptions": ["Mechanism identification based on handbook correlations"],
                "warnings": self._generate_warnings(mechanisms, query_text),
            }
        }

    def _identify_mechanisms(
        self,
        results: List[Dict[str, Any]],
        query_text: str,
    ) -> List[str]:
        """
        Identify probable corrosion mechanisms from search results.

        Common mechanisms:
        - Uniform corrosion
        - Pitting corrosion
        - Crevice corrosion
        - Stress corrosion cracking (SCC)
        - Galvanic corrosion
        - Intergranular corrosion
        - Erosion-corrosion
        - Microbiologically influenced corrosion (MIC)
        - Corrosion fatigue
        """
        mechanisms = []

        # Mechanism keyword mapping
        mechanism_keywords = {
            "pitting": ["pitting", "pit", "localized attack"],
            "crevice": ["crevice", "crevice corrosion", "occluded cell"],
            "SCC": ["stress corrosion", "SCC", "cracking"],
            "galvanic": ["galvanic", "dissimilar metal", "bimetallic"],
            "intergranular": ["intergranular", "grain boundary", "sensitization"],
            "erosion-corrosion": ["erosion", "erosion-corrosion", "flow-accelerated"],
            "MIC": ["microbiological", "MIC", "biofouling", "biofilm"],
            "uniform": ["uniform", "general corrosion"],
            "dealloying": ["dealloying", "dezincification", "selective leaching"],
            "hydrogen": ["hydrogen", "HIC", "hydrogen embrittlement"],
        }

        # Search for mechanism mentions in results
        text_combined = " ".join([r.get("text", "") for r in results[:5]]).lower()
        query_lower = query_text.lower()

        for mechanism, keywords in mechanism_keywords.items():
            # Check both query and results
            if any(kw in query_lower or kw in text_combined for kw in keywords):
                mechanisms.append(mechanism)

        # If no mechanisms identified, infer from material/environment
        if not mechanisms:
            mechanisms = self._infer_mechanisms(query_text)

        return mechanisms[:3]  # Top 3 most probable

    def _infer_mechanisms(self, query_text: str) -> List[str]:
        """Infer mechanisms from material and environment keywords"""
        query_lower = query_text.lower()
        inferred = []

        # Chloride + stainless → pitting
        if ("chloride" in query_lower or "cl" in query_lower) and "stainless" in query_lower:
            inferred.append("pitting")

        # CO2 or H2S → uniform sweet/sour corrosion
        if "co2" in query_lower or "h2s" in query_lower:
            inferred.append("uniform")

        # Dissimilar metals → galvanic
        if "dissimilar" in query_lower or "galvanic" in query_lower:
            inferred.append("galvanic")

        return inferred if inferred else ["uniform"]  # Default

    def _extract_symptoms(self, results: List[Dict[str, Any]]) -> List[str]:
        """Extract symptom descriptions from results"""
        symptoms = []

        symptom_keywords = {
            "localized pits": ["pit", "pitting", "localized"],
            "cracking": ["crack", "cracking"],
            "thinning": ["thinning", "wall loss", "general attack"],
            "discoloration": ["discoloration", "staining", "deposit"],
            "perforation": ["perforation", "through-wall", "leak"],
        }

        text_combined = " ".join([r.get("text", "") for r in results[:5]]).lower()

        for symptom, keywords in symptom_keywords.items():
            if any(kw in text_combined for kw in keywords):
                symptoms.append(symptom)

        return symptoms[:5]  # Top 5 symptoms

    def _extract_recommendations(self, results: List[Dict[str, Any]]) -> List[str]:
        """Extract mitigation recommendations from results"""
        recommendations = []

        # Look for recommendation sections in text
        rec_indicators = [
            "recommend",
            "mitigation",
            "prevention",
            "control",
            "reduce",
            "avoid",
        ]

        for result in results[:5]:
            text = result.get("text", "")
            text_lower = text.lower()

            # Find sentences containing recommendation indicators
            sentences = text.split(". ")
            for sentence in sentences:
                if any(indicator in sentence.lower() for indicator in rec_indicators):
                    # Clean and add
                    clean_sentence = sentence.strip()
                    if len(clean_sentence) > 20 and len(clean_sentence) < 200:
                        recommendations.append(clean_sentence)

        # If no explicit recommendations found, provide general ones
        if not recommendations:
            recommendations = [
                "Consult handbook sections on mechanism-specific mitigation",
                "Consider material upgrade or protective coating",
                "Monitor corrosion rate and adjust operating conditions",
            ]

        return recommendations[:5]  # Top 5 recommendations

    def _extract_tests(self, results: List[Dict[str, Any]]) -> List[str]:
        """Extract recommended diagnostic tests from results"""
        tests = []

        # Common test keywords
        test_keywords = {
            "electrochemical testing": ["electrochemical", "potentiodynamic", "LPR", "EIS"],
            "metallography": ["metallography", "microscopy", "cross-section"],
            "chemical analysis": ["chemical analysis", "EDS", "XRF"],
            "corrosion coupons": ["coupon", "weight loss"],
            "ultrasonic testing": ["UT", "ultrasonic", "thickness"],
        }

        text_combined = " ".join([r.get("text", "") for r in results[:5]]).lower()

        for test, keywords in test_keywords.items():
            if any(kw in text_combined for kw in keywords):
                tests.append(test)

        # Default recommendations if none found
        if not tests:
            tests = [
                "Visual inspection",
                "Corrosion coupon exposure",
                "Water chemistry analysis",
            ]

        return tests[:4]  # Top 4 tests

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

    def _generate_warnings(self, mechanisms: List[str], query_text: str) -> List[str]:
        """Generate warnings based on identified mechanisms"""
        warnings = []

        if not mechanisms:
            warnings.append("Could not definitively identify mechanism - consider expert consultation")

        if "SCC" in mechanisms:
            warnings.append("SCC can lead to sudden failure - immediate attention recommended")

        if "pitting" in mechanisms and "stainless" in query_text.lower():
            warnings.append("Stainless steel pitting can progress rapidly - monitor closely")

        return warnings

    def _placeholder_search(self, query_text: str) -> List[Dict[str, Any]]:
        """Placeholder for development"""
        return [
            {
                "text": "Pitting corrosion of stainless steels in chloride environments is characterized by "
                        "localized breakdown of the passive film. Symptoms include small pits with undermined "
                        "edges. Mitigation: increase molybdenum content (316 → duplex), reduce chloride, "
                        "control temperature. Recommended tests: potentiodynamic polarization, CPT determination.",
                "score": 0.90,
                "path": "handbook_of_corrosion_engineering.pdf",
                "offset": 3456,
            },
            {
                "text": "Localized corrosion in crevices forms due to differential aeration. Avoid tight crevices "
                        "in design, use crevice-resistant alloys (super duplex), ensure complete drainage.",
                "score": 0.85,
                "path": "the_corrosion_handbook.pdf",
                "offset": 7890,
            },
        ]


# ============================================================================
# MCP Tool Function
# ============================================================================

def mechanism_guidance_query(
    material: str,
    symptoms: List[str],
    environment: Optional[str] = None,
    mcp_search_function=None,
) -> MechanismGuidance:
    """
    Identify probable corrosion mechanisms and get guidance.

    This is the main MCP tool function that will be registered with FastMCP.

    Args:
        material: Material identifier (e.g., "304_SS", "CS")
        symptoms: List of observed symptoms (e.g., ["localized_attack", "pits", "chlorides_present"])
        environment: Optional environment description
        mcp_search_function: Injected semantic search function

    Returns:
        MechanismGuidance object with mechanism identification and recommendations

    Example:
        result = mechanism_guidance_query(
            material="304_SS",
            symptoms=["localized_attack", "pits", "crevices"],
            environment="cooling water, 500 mg/L Cl"
        )
        print(result.probable_mechanisms)  # ["pitting", "crevice"]
        print(result.recommendations)
    """
    lookup = MechanismGuidanceLookup(mcp_search_function)

    # Build query from inputs
    query_parts = [material]
    query_parts.extend(symptoms)
    if environment:
        query_parts.append(environment)

    query_text = " ".join(query_parts) + " corrosion mechanism"

    result_dict = lookup.query(query_text)

    return MechanismGuidance(**result_dict)
