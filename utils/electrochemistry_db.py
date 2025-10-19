"""
Electrochemistry Database - Hybrid Approach

Loads Tafel slopes, exchange current densities, and electrochemical parameters with:
1. Primary: Pre-extracted YAML (fast, deterministic, offline)
2. Fallback: Semantic search on corrosion_kb (slower, handles unknowns)

Based on Codex review recommendations for Phase 0 completion.
"""

from typing import Dict, Any, Optional, Callable
import yaml
import logging
from pathlib import Path
import re
import math

logger = logging.getLogger(__name__)


class ElectrochemistryDatabase:
    """
    Hybrid electrochemistry parameter database.

    Architecture:
    - PRIMARY: YAML lookup (~1ms, deterministic, offline)
    - FALLBACK: Semantic search (~200-500ms, semantic understanding)

    Provides:
    - Tafel slopes (ba, bc)
    - Exchange current densities (i0)
    - Transfer coefficients (alpha)
    - Temperature dependencies
    """

    def __init__(
        self,
        yaml_path: Optional[str] = None,
        semantic_search_function: Optional[Callable] = None,
    ):
        """
        Initialize electrochemistry database.

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
        return str(base_dir / "databases" / "electrochemistry.yaml")

    def _load_yaml(self) -> Dict:
        """Lazy load YAML data on first access"""
        if self._yaml_data is not None:
            return self._yaml_data

        try:
            yaml_file = Path(self.yaml_path)
            if yaml_file.exists():
                with open(yaml_file, 'r') as f:
                    self._yaml_data = yaml.safe_load(f)
                    logger.info(f"Loaded electrochemistry data from {self.yaml_path}")
            else:
                logger.warning(f"YAML file not found: {self.yaml_path}")
                self._yaml_data = {}
        except Exception as e:
            logger.error(f"Failed to load YAML: {e}")
            self._yaml_data = {}

        return self._yaml_data

    def get_tafel_slopes(
        self,
        material: str,
        reaction: str,
        electrolyte: str = "seawater",
        temperature_C: float = 25.0,
        pH: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Get Tafel slopes and exchange current density.

        Args:
            material: Material name (e.g., "Carbon Steel", "316L")
            reaction: Reaction type (e.g., "Fe_oxidation", "O2_reduction")
            electrolyte: Electrolyte type (e.g., "seawater", "brine", "freshwater")
            temperature_C: Temperature in Celsius
            pH: Optional pH (used for H+ dependent reactions)

        Returns:
            {
                "material": str,
                "reaction": str,
                "electrolyte": str,
                "temperature_C": float,
                "ba_V_per_decade": float,  # Anodic Tafel slope
                "bc_V_per_decade": float,  # Cathodic Tafel slope
                "i0_A_per_m2": float,      # Exchange current density
                "alpha": float,            # Transfer coefficient
                "n_electrons": int,        # Number of electrons transferred
                "source": str,
                "provenance": {
                    "method": "yaml_lookup" | "semantic_search" | "calculated",
                    "confidence": "high" | "medium" | "low",
                    "extraction_date": str,
                    "source_document": str,
                    "source_repo": str,
                    "quality_note": str,
                },
            }
        """
        # Check cache
        cache_key = f"{material}_{reaction}_{electrolyte}_{temperature_C}_{pH}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Try YAML lookup first (fast path)
        yaml_data = self._load_yaml()
        reaction_key = self._normalize_reaction_key(material, reaction, electrolyte)

        reactions_db = yaml_data.get("reactions", {})
        if reaction_key in reactions_db:
            result = self._build_yaml_result(
                reactions_db[reaction_key],
                material,
                reaction,
                electrolyte,
                temperature_C,
                pH,
            )
            self._cache[cache_key] = result
            return result

        # Try calculating from Butler-Volmer equations if base parameters available
        calculated = self._calculate_from_butler_volmer(
            material, reaction, electrolyte, temperature_C, pH
        )
        if calculated:
            self._cache[cache_key] = calculated
            return calculated

        # Fallback to semantic search (slow path)
        if self.semantic_search is not None:
            logger.info(
                f"YAML miss for '{material}/{reaction}', "
                f"falling back to semantic search"
            )
            result = self._semantic_search_fallback(
                material, reaction, electrolyte, temperature_C, pH
            )
            self._cache[cache_key] = result
            return result

        # No data available
        logger.warning(
            f"No electrochemistry data found for '{material}/{reaction}' "
            f"(YAML missing and no semantic search configured)"
        )
        return self._build_empty_result(
            material, reaction, electrolyte, temperature_C, pH
        )

    def _normalize_reaction_key(
        self,
        material: str,
        reaction: str,
        electrolyte: str,
    ) -> str:
        """Generate normalized key for YAML lookup"""
        mat_norm = material.lower().replace(" ", "_").replace("-", "_")
        rxn_norm = reaction.lower().replace(" ", "_").replace("-", "_")
        elec_norm = electrolyte.lower().replace(" ", "_")

        return f"{mat_norm}_{rxn_norm}_{elec_norm}"

    def _calculate_from_butler_volmer(
        self,
        material: str,
        reaction: str,
        electrolyte: str,
        temperature_C: float,
        pH: Optional[float],
    ) -> Optional[Dict[str, Any]]:
        """
        Calculate Tafel slopes from Butler-Volmer equation.

        Butler-Volmer: ba = 2.303 × (RT / αnF)
                       bc = -2.303 × (RT / αnF)

        Where:
        R = Gas constant (8.314 J/mol·K)
        T = Temperature (K)
        α = Transfer coefficient (typically 0.5)
        n = Number of electrons transferred
        F = Faraday constant (96485 C/mol)
        """
        # Check if we have base parameters in YAML
        yaml_data = self._load_yaml()
        base_params = yaml_data.get("base_parameters", {})

        reaction_norm = reaction.lower().replace(" ", "_")
        if reaction_norm not in base_params:
            return None

        params = base_params[reaction_norm]

        # Constants
        R = 8.314  # J/mol·K
        F = 96485  # C/mol
        T_K = temperature_C + 273.15

        alpha = params.get("alpha", 0.5)
        n = params.get("n_electrons", 2)

        # Calculate Tafel slopes
        RT_over_alphaF = (R * T_K) / (alpha * F)
        ba = 2.303 * RT_over_alphaF
        bc = -2.303 * RT_over_alphaF

        # Exchange current density (temperature corrected if available)
        i0_ref = params.get("i0_A_per_m2_25C", 1e-6)
        Ea = params.get("activation_energy_kJ_per_mol", 40.0) * 1000  # Convert to J/mol

        # Arrhenius correction: i0(T) = i0_ref × exp(-Ea/R × (1/T - 1/T_ref))
        T_ref = 298.15  # 25°C
        i0 = i0_ref * math.exp(-(Ea / R) * (1 / T_K - 1 / T_ref))

        return {
            "material": material,
            "reaction": reaction,
            "electrolyte": electrolyte,
            "temperature_C": temperature_C,
            "pH": pH,
            "ba_V_per_decade": round(ba, 4),
            "bc_V_per_decade": round(bc, 4),
            "i0_A_per_m2": i0,
            "alpha": alpha,
            "n_electrons": n,
            "source": "Butler-Volmer calculation",
            "provenance": {
                "method": "calculated",
                "confidence": "high",
                "extraction_date": None,
                "source_document": "Butler-Volmer equation",
                "source_repo": "core calculation",
                "quality_note": f"Calculated from base parameters at {temperature_C}°C",
            },
        }

    def _build_yaml_result(
        self,
        yaml_entry: Dict,
        material: str,
        reaction: str,
        electrolyte: str,
        temperature_C: float,
        pH: Optional[float],
    ) -> Dict[str, Any]:
        """Build result from YAML entry with full provenance"""
        return {
            "material": material,
            "reaction": reaction,
            "electrolyte": electrolyte,
            "temperature_C": temperature_C,
            "pH": pH,
            "ba_V_per_decade": yaml_entry.get("ba_V_per_decade"),
            "bc_V_per_decade": yaml_entry.get("bc_V_per_decade"),
            "i0_A_per_m2": yaml_entry.get("i0_A_per_m2"),
            "alpha": yaml_entry.get("alpha", 0.5),
            "n_electrons": yaml_entry.get("n_electrons", 2),
            "source": yaml_entry.get("source", "Unknown"),
            "provenance": {
                "method": "yaml_lookup",
                "confidence": "high",
                "extraction_date": yaml_entry.get("extraction_date", "Unknown"),
                "source_document": yaml_entry.get("source_document", "Unknown"),
                "source_repo": yaml_entry.get("source_repo", "corrosion_kb"),
                "quality_note": yaml_entry.get(
                    "quality_note", "Pre-extracted and verified"
                ),
            },
        }

    def _semantic_search_fallback(
        self,
        material: str,
        reaction: str,
        electrolyte: str,
        temperature_C: float,
        pH: Optional[float],
    ) -> Dict[str, Any]:
        """
        Fallback to semantic search when YAML misses.

        Extracts Tafel slopes and i0 from handbook text.
        """
        query = (
            f"{material} {reaction} Tafel slope exchange current density "
            f"{electrolyte} electrochemical kinetics"
        )

        try:
            results = self.semantic_search(query, top_k=5)

            if not results:
                return self._build_empty_result(
                    material, reaction, electrolyte, temperature_C, pH
                )

            # Parse electrochemical parameters
            parsed = self._parse_electrochemistry_from_text(results)

            return {
                "material": material,
                "reaction": reaction,
                "electrolyte": electrolyte,
                "temperature_C": temperature_C,
                "pH": pH,
                "ba_V_per_decade": parsed.get("ba"),
                "bc_V_per_decade": parsed.get("bc"),
                "i0_A_per_m2": parsed.get("i0"),
                "alpha": parsed.get("alpha", 0.5),
                "n_electrons": parsed.get("n", 2),
                "source": parsed.get("source", "Semantic search extraction"),
                "provenance": {
                    "method": "semantic_search",
                    "confidence": parsed.get("confidence", "medium"),
                    "extraction_date": None,
                    "source_document": parsed.get("document", "corrosion_kb"),
                    "vector_chunk_ids": [r.get("id", "") for r in results[:3]],
                    "quality_note": (
                        "Extracted via semantic search - consider enriching YAML"
                    ),
                },
            }

        except Exception as e:
            logger.error(f"Semantic search failed: {e}")
            return self._build_empty_result(
                material, reaction, electrolyte, temperature_C, pH
            )

    def _parse_electrochemistry_from_text(
        self,
        search_results: list,
    ) -> Dict[str, Any]:
        """
        Parse electrochemical parameters from semantic search results.

        Looks for patterns like:
        - "anodic Tafel slope: 0.06 V/decade"
        - "cathodic Tafel slope: -0.12 V/decade"
        - "exchange current density: 1.0e-5 A/m²"
        - "transfer coefficient α = 0.5"
        """
        parsed = {
            "ba": None,
            "bc": None,
            "i0": None,
            "alpha": None,
            "n": None,
            "source": None,
            "document": None,
            "confidence": "low",
        }

        combined_text = "\n".join([r.get("text", "") for r in search_results])

        # Regex patterns
        ba_pattern = r"(?:anodic|ba).*?Tafel.*?(\d+\.?\d*)\s*[VmV]"
        bc_pattern = r"(?:cathodic|bc).*?Tafel.*?[-−]?(\d+\.?\d*)\s*[VmV]"
        i0_pattern = r"(?:exchange current|i0|i₀).*?(\d+\.?\d*[eE]?[-+]?\d*)\s*A"
        alpha_pattern = r"(?:transfer coefficient|alpha|α).*?[=:]?\s*(\d+\.?\d*)"

        # Extract ba
        ba_match = re.search(ba_pattern, combined_text, re.IGNORECASE)
        if ba_match:
            parsed["ba"] = float(ba_match.group(1))
            if parsed["ba"] > 1.0:  # Likely in mV, convert to V
                parsed["ba"] /= 1000.0
            parsed["confidence"] = "medium"

        # Extract bc
        bc_match = re.search(bc_pattern, combined_text, re.IGNORECASE)
        if bc_match:
            parsed["bc"] = -float(bc_match.group(1))  # Ensure negative
            if abs(parsed["bc"]) > 1.0:  # Likely in mV
                parsed["bc"] /= 1000.0
            parsed["confidence"] = "medium"

        # Extract i0
        i0_match = re.search(i0_pattern, combined_text, re.IGNORECASE)
        if i0_match:
            parsed["i0"] = float(i0_match.group(1))
            parsed["confidence"] = "medium"

        # Extract alpha
        alpha_match = re.search(alpha_pattern, combined_text, re.IGNORECASE)
        if alpha_match:
            parsed["alpha"] = float(alpha_match.group(1))

        # Extract source
        if search_results:
            first_result = search_results[0]
            parsed["source"] = first_result.get("source", "Unknown")
            parsed["document"] = first_result.get("path", "corrosion_kb")

        # Upgrade confidence if multiple parameters found
        found_count = sum(
            1 for v in [parsed["ba"], parsed["bc"], parsed["i0"]]
            if v is not None
        )
        if found_count >= 2:
            parsed["confidence"] = "high"

        return parsed

    def _build_empty_result(
        self,
        material: str,
        reaction: str,
        electrolyte: str,
        temperature_C: float,
        pH: Optional[float],
    ) -> Dict[str, Any]:
        """Build empty result when no data found"""
        return {
            "material": material,
            "reaction": reaction,
            "electrolyte": electrolyte,
            "temperature_C": temperature_C,
            "pH": pH,
            "ba_V_per_decade": None,
            "bc_V_per_decade": None,
            "i0_A_per_m2": None,
            "alpha": None,
            "n_electrons": None,
            "source": "No data available",
            "provenance": {
                "method": "none",
                "confidence": "none",
                "extraction_date": None,
                "source_document": None,
                "source_repo": None,
                "vector_chunk_ids": [],
                "quality_note": "No data found - consider manual extraction",
            },
        }

    def list_available_reactions(self) -> list:
        """List all reactions available in YAML database"""
        yaml_data = self._load_yaml()
        return list(yaml_data.get("reactions", {}).keys())
