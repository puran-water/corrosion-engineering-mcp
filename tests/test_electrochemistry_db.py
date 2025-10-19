"""
Unit tests for ElectrochemistryDatabase

Tests Butler-Volmer calculations, Arrhenius temperature correction,
Tafel slope parsing, and hybrid YAML/semantic search.
"""

import pytest
from unittest.mock import Mock, patch, mock_open
from pathlib import Path
import sys
import yaml
import math

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.electrochemistry_db import ElectrochemistryDatabase


# Mock YAML data
MOCK_YAML = """
metadata:
  extraction_method: "Semantic search + Butler-Volmer calculation"
  extraction_date: "2025-10-18"

base_parameters:
  fe_oxidation:
    reaction: "Fe → Fe²⁺ + 2e⁻"
    n_electrons: 2
    alpha: 0.5
    i0_A_per_m2_25C: 1.0e-5
    activation_energy_kJ_per_mol: 40.0
    source: "Handbook of Corrosion Engineering"

reactions:
  carbon_steel_fe_oxidation_seawater:
    material: "Carbon Steel"
    reaction: "Fe_oxidation"
    electrolyte: "seawater"
    ba_V_per_decade: 0.060
    bc_V_per_decade: -0.120
    i0_A_per_m2: 1.0e-5
    alpha: 0.5
    n_electrons: 2
    temperature_C: 25
    pH: 8.2
    source: "Handbook of Corrosion Engineering, Fig 3.14"
    source_document: "Handbook of Corrosion Engineering"
    extraction_date: "2025-10-18"
"""


class TestElectrochemistryDatabase:
    """Test suite for ElectrochemistryDatabase"""

    def test_yaml_lookup_carbon_steel(self):
        """Test YAML lookup for carbon steel Fe oxidation"""
        with patch('builtins.open', mock_open(read_data=MOCK_YAML)):
            with patch('pathlib.Path.exists', return_value=True):
                db = ElectrochemistryDatabase(semantic_search_function=None)
                result = db.get_tafel_slopes(
                    material="Carbon Steel",
                    reaction="Fe_oxidation",
                    electrolyte="seawater",
                    temperature_C=25.0,
                )

        assert result["ba_V_per_decade"] == 0.060
        assert result["bc_V_per_decade"] == -0.120
        assert result["i0_A_per_m2"] == 1.0e-5
        assert result["provenance"]["method"] == "yaml_lookup"
        assert result["provenance"]["confidence"] == "high"

    def test_butler_volmer_calculation_25C(self):
        """Test Butler-Volmer Tafel slope calculation at 25°C"""
        R = 8.314  # J/mol·K
        F = 96485  # C/mol
        T = 298.15  # 25°C in K
        alpha = 0.5
        n = 2

        # Expected Tafel slope
        RT_over_alphaF = (R * T) / (alpha * F)
        expected_ba = 2.303 * RT_over_alphaF

        with patch('builtins.open', mock_open(read_data=MOCK_YAML)):
            with patch('pathlib.Path.exists', return_value=True):
                db = ElectrochemistryDatabase()

                # Calculate via Butler-Volmer
                result = db._calculate_from_butler_volmer(
                    material="Test Material",
                    reaction="fe_oxidation",
                    electrolyte="seawater",
                    temperature_C=25.0,
                    pH=7.0,
                )

        assert result is not None
        assert abs(result["ba_V_per_decade"] - expected_ba) < 0.001
        assert result["bc_V_per_decade"] < 0  # Cathodic is negative
        assert result["provenance"]["method"] == "calculated"

    def test_arrhenius_temperature_correction(self):
        """Test Arrhenius correction for temperature dependency of i0"""
        with patch('builtins.open', mock_open(read_data=MOCK_YAML)):
            with patch('pathlib.Path.exists', return_value=True):
                db = ElectrochemistryDatabase()

                # Calculate at 25°C (reference)
                result_25C = db._calculate_from_butler_volmer(
                    material="Test",
                    reaction="fe_oxidation",
                    electrolyte="seawater",
                    temperature_C=25.0,
                    pH=7.0,
                )

                # Calculate at 60°C (higher temperature)
                result_60C = db._calculate_from_butler_volmer(
                    material="Test",
                    reaction="fe_oxidation",
                    electrolyte="seawater",
                    temperature_C=60.0,
                    pH=7.0,
                )

        # i0 should increase with temperature (Arrhenius)
        assert result_60C["i0_A_per_m2"] > result_25C["i0_A_per_m2"]

        # Tafel slopes should also change with temperature (RT/F term)
        assert result_60C["ba_V_per_decade"] > result_25C["ba_V_per_decade"]

    def test_reaction_key_normalization(self):
        """Test normalization of material/reaction/electrolyte keys"""
        db = ElectrochemistryDatabase()

        # Test various input formats
        key1 = db._normalize_reaction_key("Carbon Steel", "Fe Oxidation", "seawater")
        key2 = db._normalize_reaction_key("carbon steel", "fe_oxidation", "seawater")

        assert key1 == key2  # Should normalize to same key

    def test_regex_parsing_tafel_slopes(self):
        """Test regex parsing of Tafel slopes from text"""
        db = ElectrochemistryDatabase()

        text = (
            "For this material, anodic Tafel slope ba = 0.060 V/decade "
            "and cathodic Tafel slope bc = -0.120 V/decade"
        )
        results = [{"text": text, "source": "Test", "path": "test.pdf", "id": "001"}]

        parsed = db._parse_electrochemistry_from_text(results)

        assert parsed["ba"] is not None
        assert abs(parsed["ba"] - 0.060) < 0.001

        assert parsed["bc"] is not None
        assert abs(parsed["bc"] - (-0.120)) < 0.001

    def test_regex_parsing_exchange_current_density(self):
        """Test regex parsing of exchange current density"""
        db = ElectrochemistryDatabase()

        text = "exchange current density i0 = 1.0e-5 A/m²"
        results = [{"text": text, "source": "Test", "path": "test.pdf", "id": "001"}]

        parsed = db._parse_electrochemistry_from_text(results)

        assert parsed["i0"] is not None
        assert abs(parsed["i0"] - 1.0e-5) < 1e-6

    def test_confidence_scoring_electrochemistry(self):
        """Test confidence scoring based on found parameters"""
        db = ElectrochemistryDatabase()

        # Low confidence: only ba found
        text_low = "anodic Tafel slope: 0.060 V/decade"
        results_low = [{"text": text_low, "source": "Test", "path": "test.pdf", "id": "001"}]
        parsed_low = db._parse_electrochemistry_from_text(results_low)
        assert parsed_low["confidence"] == "medium"

        # High confidence: ba, bc, i0 found
        text_high = (
            "anodic Tafel slope ba = 0.060 V/decade, "
            "cathodic Tafel slope bc = -0.120 V/decade, "
            "exchange current density i0 = 1.0e-5 A/m²"
        )
        results_high = [{"text": text_high, "source": "Test", "path": "test.pdf", "id": "001"}]
        parsed_high = db._parse_electrochemistry_from_text(results_high)
        assert parsed_high["confidence"] == "high"

    def test_semantic_search_fallback(self):
        """Test fallback to semantic search when YAML misses"""
        def mock_search(query, top_k=5):
            return [
                {
                    "text": "For this reaction, anodic Tafel slope ba = 0.070 V/decade",
                    "source": "Handbook",
                    "path": "handbook.pdf",
                    "id": "chunk_001",
                }
            ]

        with patch('builtins.open', mock_open(read_data=MOCK_YAML)):
            with patch('pathlib.Path.exists', return_value=True):
                db = ElectrochemistryDatabase(semantic_search_function=mock_search)

                # Request unknown reaction
                result = db.get_tafel_slopes(
                    material="Unknown Material",
                    reaction="unknown_reaction",
                    electrolyte="brine",
                )

        assert result["provenance"]["method"] == "semantic_search"
        assert result["ba_V_per_decade"] is not None

    def test_empty_result_handling(self):
        """Test handling when no data is found"""
        def mock_search_empty(query, top_k=5):
            return []

        with patch('builtins.open', mock_open(read_data=MOCK_YAML)):
            with patch('pathlib.Path.exists', return_value=True):
                db = ElectrochemistryDatabase(semantic_search_function=mock_search_empty)

                result = db.get_tafel_slopes(
                    material="Unknown",
                    reaction="unknown",
                    electrolyte="unknown",
                )

        assert result["ba_V_per_decade"] is None
        assert result["bc_V_per_decade"] is None
        assert result["i0_A_per_m2"] is None
        assert result["provenance"]["method"] == "none"

    def test_three_tier_lookup_priority(self):
        """Test that lookup priority is: YAML → Butler-Volmer → Semantic Search"""
        def mock_search(query, top_k=5):
            # Should not be called if YAML or Butler-Volmer succeed
            pytest.fail("Semantic search should not be called")

        with patch('builtins.open', mock_open(read_data=MOCK_YAML)):
            with patch('pathlib.Path.exists', return_value=True):
                db = ElectrochemistryDatabase(semantic_search_function=mock_search)

                # This should hit YAML (highest priority)
                result = db.get_tafel_slopes(
                    material="Carbon Steel",
                    reaction="Fe_oxidation",
                    electrolyte="seawater",
                )

                assert result["provenance"]["method"] == "yaml_lookup"

    def test_cache_behavior_electrochemistry(self):
        """Test that results are cached"""
        with patch('builtins.open', mock_open(read_data=MOCK_YAML)):
            with patch('pathlib.Path.exists', return_value=True):
                db = ElectrochemistryDatabase()

                # First call
                result1 = db.get_tafel_slopes(
                    material="Carbon Steel",
                    reaction="Fe_oxidation",
                    electrolyte="seawater",
                )

                # Second call should hit cache
                result2 = db.get_tafel_slopes(
                    material="Carbon Steel",
                    reaction="Fe_oxidation",
                    electrolyte="seawater",
                )

                assert result1 is result2

    def test_list_available_reactions(self):
        """Test listing all available reactions in YAML"""
        with patch('builtins.open', mock_open(read_data=MOCK_YAML)):
            with patch('pathlib.Path.exists', return_value=True):
                db = ElectrochemistryDatabase()
                available = db.list_available_reactions()

        assert "carbon_steel_fe_oxidation_seawater" in available

    def test_provenance_metadata_structure_electrochemistry(self):
        """Test that provenance metadata has all required fields"""
        with patch('builtins.open', mock_open(read_data=MOCK_YAML)):
            with patch('pathlib.Path.exists', return_value=True):
                db = ElectrochemistryDatabase()
                result = db.get_tafel_slopes(
                    material="Carbon Steel",
                    reaction="Fe_oxidation",
                    electrolyte="seawater",
                )

        provenance = result["provenance"]

        # Check all required fields
        assert "method" in provenance
        assert "confidence" in provenance
        assert "extraction_date" in provenance
        assert "source_document" in provenance
        assert "source_repo" in provenance
        assert "quality_note" in provenance


class TestButlerVolmerPhysics:
    """Test Butler-Volmer physics calculations"""

    def test_tafel_slope_temperature_dependency(self):
        """Test that Tafel slopes increase with temperature"""
        R = 8.314
        F = 96485
        alpha = 0.5
        n = 2

        # Calculate at two temperatures
        T1 = 298.15  # 25°C
        T2 = 333.15  # 60°C

        ba1 = 2.303 * (R * T1) / (alpha * n * F)
        ba2 = 2.303 * (R * T2) / (alpha * n * F)

        # Higher temperature should give higher Tafel slope
        assert ba2 > ba1

    def test_exchange_current_arrhenius(self):
        """Test Arrhenius temperature correction for i0"""
        i0_ref = 1.0e-5  # A/m² at 25°C
        Ea = 40000  # J/mol
        R = 8.314  # J/mol·K
        T_ref = 298.15  # 25°C
        T_test = 333.15  # 60°C

        # Arrhenius: i0(T) = i0_ref × exp(-Ea/R × (1/T - 1/T_ref))
        i0_test = i0_ref * math.exp(-(Ea / R) * (1 / T_test - 1 / T_ref))

        # i0 at 60°C should be higher than at 25°C
        assert i0_test > i0_ref


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
