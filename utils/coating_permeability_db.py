"""
Coating Permeability Database - Hybrid Approach

Loads coating permeability data with two-tier strategy:
1. Primary: Pre-extracted YAML (fast, deterministic, offline)
2. Fallback: Semantic search on corrosion_kb (slower, handles unknowns)

Based on Codex review recommendations for Phase 0 completion.
"""

from typing import Dict, Any, Optional, Callable
import yaml
import logging
from pathlib import Path
import re

logger = logging.getLogger(__name__)


class CoatingPermeabilityDatabase:
    """
    Hybrid coating permeability database.

    Architecture:
    - PRIMARY: YAML lookup (~1ms, deterministic, offline)
    - FALLBACK: Semantic search (~200-500ms, semantic understanding)

    Provenance tracking for all values per Codex recommendation.
    """

    def __init__(
        self,
        yaml_path: Optional[str] = None,
        semantic_search_function: Optional[Callable] = None,
    ):
        """
        Initialize coating permeability database.

        Args:
            yaml_path: Path to pre-extracted YAML file
            semantic_search_function: Function(query: str, top_k: int) -> List[Dict]
        """
        self.yaml_path = yaml_path or self._default_yaml_path()
        self.semantic_search = semantic_search_function
        self._yaml_data: Optional[Dict] = None
        self._cache: Dict[str, Dict[str, Any]] = {}

    def _default_yaml_path(self) -> str:
        """Get default YAML path relative to this module"""
        base_dir = Path(__file__).parent.parent
        return str(base_dir / "databases" / "coating_permeability.yaml")

    def _load_yaml(self) -> Dict:
        """Lazy load YAML data on first access"""
        if self._yaml_data is not None:
            return self._yaml_data

        try:
            yaml_file = Path(self.yaml_path)
            if yaml_file.exists():
                with open(yaml_file, 'r') as f:
                    self._yaml_data = yaml.safe_load(f)
                    logger.info(f"Loaded coating permeability from {self.yaml_path}")
            else:
                logger.warning(f"YAML file not found: {self.yaml_path}")
                self._yaml_data = {}
        except Exception as e:
            logger.error(f"Failed to load YAML: {e}")
            self._yaml_data = {}

        return self._yaml_data

    def get_permeability(
        self,
        coating_type: str,
        temperature_C: Optional[float] = 25.0,
    ) -> Dict[str, Any]:
        """
        Get permeability data for coating type.

        Args:
            coating_type: Coating material (e.g., "epoxy", "polyurethane", "FBE")
            temperature_C: Operating temperature (affects diffusion rates)

        Returns:
            {
                "coating_type": str,
                "temperature_C": float,
                "moisture_transmission_mg_per_24hr_per_sqin": float,
                "oxygen_permeability_cm3_mil_per_100sqin_24hr_atm": float,
                "diffusion_constant_cm2_per_s": float,
                "source": str,
                "provenance": {
                    "method": "yaml_lookup" | "semantic_search",
                    "confidence": "high" | "medium" | "low",
                    "extraction_date": str (ISO 8601),
                    "source_document": str,
                    "vector_chunk_ids": List[str] (if semantic search),
                },
            }
        """
        # Check cache first
        cache_key = f"{coating_type}_{temperature_C}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Try YAML lookup first (fast path)
        yaml_data = self._load_yaml()
        coating_key = self._normalize_coating_name(coating_type)

        if coating_key in yaml_data.get("coatings", {}):
            result = self._build_yaml_result(
                yaml_data["coatings"][coating_key],
                coating_type,
                temperature_C,
            )
            self._cache[cache_key] = result
            return result

        # Fallback to semantic search (slow path)
        if self.semantic_search is not None:
            logger.info(f"YAML miss for '{coating_type}', falling back to semantic search")
            result = self._semantic_search_fallback(coating_type, temperature_C)
            self._cache[cache_key] = result
            return result

        # No data available
        logger.warning(
            f"No permeability data found for '{coating_type}' "
            f"(YAML missing and no semantic search configured)"
        )
        return self._build_empty_result(coating_type, temperature_C)

    def _normalize_coating_name(self, coating_type: str) -> str:
        """Normalize coating name for YAML lookup"""
        # Convert to lowercase, remove spaces/hyphens
        normalized = coating_type.lower().replace("-", "_").replace(" ", "_")

        # Common aliases
        aliases = {
            "fusion_bonded_epoxy": "fbe",
            "polyvinyl_acetate": "pva",
            "polyurethane": "pu",
            "fluorinated_ethylene_propylene": "fep",
        }

        return aliases.get(normalized, normalized)

    def _build_yaml_result(
        self,
        yaml_entry: Dict,
        coating_type: str,
        temperature_C: float,
    ) -> Dict[str, Any]:
        """Build result from YAML entry with full provenance"""
        return {
            "coating_type": coating_type,
            "temperature_C": temperature_C,
            "moisture_transmission_mg_per_24hr_per_sqin": yaml_entry.get(
                "moisture_transmission_mg_per_24hr_per_sqin"
            ),
            "oxygen_permeability_cm3_mil_per_100sqin_24hr_atm": yaml_entry.get(
                "oxygen_permeability_cm3_mil_per_100sqin_24hr_atm"
            ),
            "diffusion_constant_cm2_per_s": yaml_entry.get(
                "diffusion_constant_cm2_per_s"
            ),
            "source": yaml_entry.get("source", "Unknown"),
            "provenance": {
                "method": "yaml_lookup",
                "confidence": "high",
                "extraction_date": yaml_entry.get("extraction_date", "Unknown"),
                "source_document": yaml_entry.get("source_document", "Unknown"),
                "source_repo": yaml_entry.get("source_repo", "corrosion_kb"),
                "quality_note": yaml_entry.get("quality_note", "Pre-extracted and verified"),
            },
        }

    def _semantic_search_fallback(
        self,
        coating_type: str,
        temperature_C: float,
    ) -> Dict[str, Any]:
        """
        Fallback to semantic search when YAML misses.

        Extracts permeability from handbook text via regex parsing.
        """
        query = (
            f"{coating_type} coating permeability moisture oxygen "
            f"diffusion transmission rate"
        )

        try:
            results = self.semantic_search(query, top_k=5)

            if not results:
                return self._build_empty_result(coating_type, temperature_C)

            # Parse numerical values from search results
            parsed = self._parse_permeability_from_text(results)

            return {
                "coating_type": coating_type,
                "temperature_C": temperature_C,
                "moisture_transmission_mg_per_24hr_per_sqin": parsed.get(
                    "moisture_transmission"
                ),
                "oxygen_permeability_cm3_mil_per_100sqin_24hr_atm": parsed.get(
                    "oxygen_permeability"
                ),
                "diffusion_constant_cm2_per_s": parsed.get("diffusion_constant"),
                "source": parsed.get("source", "Semantic search extraction"),
                "provenance": {
                    "method": "semantic_search",
                    "confidence": parsed.get("confidence", "medium"),
                    "extraction_date": None,  # Dynamic extraction
                    "source_document": parsed.get("document", "corrosion_kb"),
                    "vector_chunk_ids": [r.get("id", "") for r in results[:3]],
                    "quality_note": (
                        "Extracted via semantic search - consider enriching YAML"
                    ),
                },
            }

        except Exception as e:
            logger.error(f"Semantic search failed: {e}")
            return self._build_empty_result(coating_type, temperature_C)

    def _parse_permeability_from_text(
        self,
        search_results: list,
    ) -> Dict[str, Any]:
        """
        Parse permeability values from semantic search results.

        Looks for patterns like:
        - "moisture transmission: 8.5 mg per 24hr per sq in"
        - "oxygen permeability: 0.15 cm³·mil/(100 sq in·24 hr·atm)"
        - "diffusion constant: 1.2e-9 cm²/s"
        """
        parsed = {
            "moisture_transmission": None,
            "oxygen_permeability": None,
            "diffusion_constant": None,
            "source": None,
            "document": None,
            "confidence": "low",
        }

        # Combine all result texts
        combined_text = "\n".join([r.get("text", "") for r in search_results])

        # Regex patterns for common units
        moisture_pattern = r"(\d+\.?\d*)\s*mg.*?(?:24\s*hr|day).*?(?:sq\s*in|in)"
        oxygen_pattern = r"(\d+\.?\d*)\s*cm[³3].*?mil.*?(?:100\s*sq\s*in|atm)"
        diffusion_pattern = r"(\d+\.?\d*[eE]?[-+]?\d*)\s*cm[²2]\/s"

        # Extract moisture transmission
        moisture_match = re.search(moisture_pattern, combined_text, re.IGNORECASE)
        if moisture_match:
            parsed["moisture_transmission"] = float(moisture_match.group(1))
            parsed["confidence"] = "medium"

        # Extract oxygen permeability
        oxygen_match = re.search(oxygen_pattern, combined_text, re.IGNORECASE)
        if oxygen_match:
            parsed["oxygen_permeability"] = float(oxygen_match.group(1))
            parsed["confidence"] = "medium"

        # Extract diffusion constant
        diffusion_match = re.search(diffusion_pattern, combined_text, re.IGNORECASE)
        if diffusion_match:
            parsed["diffusion_constant"] = float(diffusion_match.group(1))
            parsed["confidence"] = "medium"

        # Extract source information
        if search_results:
            first_result = search_results[0]
            parsed["source"] = first_result.get("source", "Unknown")
            parsed["document"] = first_result.get("path", "corrosion_kb")

        # Upgrade confidence if multiple values found
        found_count = sum(
            1 for v in [
                parsed["moisture_transmission"],
                parsed["oxygen_permeability"],
                parsed["diffusion_constant"],
            ] if v is not None
        )

        if found_count >= 2:
            parsed["confidence"] = "high"

        return parsed

    def _build_empty_result(
        self,
        coating_type: str,
        temperature_C: float,
    ) -> Dict[str, Any]:
        """Build empty result when no data found"""
        return {
            "coating_type": coating_type,
            "temperature_C": temperature_C,
            "moisture_transmission_mg_per_24hr_per_sqin": None,
            "oxygen_permeability_cm3_mil_per_100sqin_24hr_atm": None,
            "diffusion_constant_cm2_per_s": None,
            "source": "No data available",
            "provenance": {
                "method": "none",
                "confidence": "none",
                "extraction_date": None,
                "source_document": None,
                "vector_chunk_ids": [],
                "quality_note": "No data found - consider manual extraction",
            },
        }

    def list_available_coatings(self) -> list:
        """List all coatings available in YAML database"""
        yaml_data = self._load_yaml()
        return list(yaml_data.get("coatings", {}).keys())
