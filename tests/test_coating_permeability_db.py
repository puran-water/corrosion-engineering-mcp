"""
Unit tests for CoatingPermeabilityDatabase

Tests hybrid YAML/semantic search approach, provenance tracking,
regex parsing, and fallback behavior.
"""

import pytest
from unittest.mock import Mock, patch, mock_open
from pathlib import Path
import sys
import yaml

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.coating_permeability_db import CoatingPermeabilityDatabase


# Mock YAML data
MOCK_YAML = """
metadata:
  extraction_method: "Semantic search + manual verification"
  extraction_date: "2025-10-18"
  source_repo: "corrosion_kb"

coatings:
  epoxy:
    moisture_transmission_mg_per_24hr_per_sqin: 8.5
    oxygen_permeability_cm3_mil_per_100sqin_24hr_atm: 0.15
    diffusion_constant_cm2_per_s: 1.2e-9
    source: "The Corrosion Handbook, Table 1"
    source_document: "The Corrosion Handbook"
    extraction_date: "2025-10-18"
    quality_note: "Typical value for cured epoxy coating"

  polyurethane:
    moisture_transmission_mg_per_24hr_per_sqin: 12.0
    oxygen_permeability_cm3_mil_per_100sqin_24hr_atm: 0.25
    diffusion_constant_cm2_per_s: 1.8e-9
    source: "The Corrosion Handbook"
    source_document: "The Corrosion Handbook"
    extraction_date: "2025-10-18"
    quality_note: "Moderate permeability"
"""


class TestCoatingPermeabilityDatabase:
    """Test suite for CoatingPermeabilityDatabase"""

    def test_yaml_lookup_epoxy(self):
        """Test YAML lookup for epoxy coating"""
        mock_yaml_data = yaml.safe_load(MOCK_YAML)

        with patch('builtins.open', mock_open(read_data=MOCK_YAML)):
            with patch('pathlib.Path.exists', return_value=True):
                db = CoatingPermeabilityDatabase(semantic_search_function=None)
                result = db.get_permeability("epoxy", temperature_C=25.0)

        assert result["coating_type"] == "epoxy"
        assert result["moisture_transmission_mg_per_24hr_per_sqin"] == 8.5
        assert result["oxygen_permeability_cm3_mil_per_100sqin_24hr_atm"] == 0.15
        assert result["diffusion_constant_cm2_per_s"] == 1.2e-9
        assert result["provenance"]["method"] == "yaml_lookup"
        assert result["provenance"]["confidence"] == "high"

    def test_coating_name_normalization(self):
        """Test that coating names are normalized correctly"""
        db = CoatingPermeabilityDatabase()

        # Test various normalization cases
        assert db._normalize_coating_name("Epoxy") == "epoxy"
        assert db._normalize_coating_name("Fusion-Bonded Epoxy") == "fbe"
        assert db._normalize_coating_name("fusion_bonded_epoxy") == "fbe"
        assert db._normalize_coating_name("Polyvinyl Acetate") == "pva"
        assert db._normalize_coating_name("polyurethane") == "pu"

    def test_cache_behavior(self):
        """Test that results are cached"""
        with patch('builtins.open', mock_open(read_data=MOCK_YAML)):
            with patch('pathlib.Path.exists', return_value=True):
                db = CoatingPermeabilityDatabase()

                # First call
                result1 = db.get_permeability("epoxy", temperature_C=25.0)

                # Second call should hit cache
                result2 = db.get_permeability("epoxy", temperature_C=25.0)

                # Should be same object
                assert result1 is result2

    def test_semantic_search_fallback(self):
        """Test fallback to semantic search when YAML misses"""
        # Mock semantic search function
        def mock_search(query, top_k=5):
            return [
                {
                    "text": "FBE coating: moisture transmission 6.5 mg per 24hr per sq in",
                    "source": "Zargarnezhad 2022",
                    "path": "papers/zargarnezhad.pdf",
                    "id": "chunk_001",
                }
            ]

        with patch('builtins.open', mock_open(read_data=MOCK_YAML)):
            with patch('pathlib.Path.exists', return_value=True):
                db = CoatingPermeabilityDatabase(semantic_search_function=mock_search)

                # Request coating not in YAML
                result = db.get_permeability("fbe", temperature_C=25.0)

        assert result["provenance"]["method"] == "semantic_search"
        assert result["moisture_transmission_mg_per_24hr_per_sqin"] == 6.5
        assert "chunk_001" in result["provenance"]["vector_chunk_ids"]

    def test_regex_parsing_moisture_transmission(self):
        """Test regex parsing of moisture transmission values"""
        db = CoatingPermeabilityDatabase()

        test_texts = [
            "moisture transmission: 8.5 mg per 24hr per sq in",
            "115 mg H2O per 24 hr per sq in",
            "Moisture transmission rate: 12.0 mg/day/sq in",
        ]

        for text in test_texts:
            results = [{"text": text, "source": "Test", "path": "test.pdf", "id": "001"}]
            parsed = db._parse_permeability_from_text(results)

            assert parsed["moisture_transmission"] is not None
            assert parsed["moisture_transmission"] > 0

    def test_regex_parsing_oxygen_permeability(self):
        """Test regex parsing of oxygen permeability"""
        db = CoatingPermeabilityDatabase()

        text = "oxygen permeability: 0.15 cm³·mil per 100 sq in·24 hr·atm"
        results = [{"text": text, "source": "Test", "path": "test.pdf", "id": "001"}]

        parsed = db._parse_permeability_from_text(results)

        assert parsed["oxygen_permeability"] is not None
        assert 0.1 <= parsed["oxygen_permeability"] <= 0.2

    def test_regex_parsing_diffusion_constant(self):
        """Test regex parsing of diffusion constants"""
        db = CoatingPermeabilityDatabase()

        test_cases = [
            ("diffusion constant: 1.2e-9 cm²/s", 1.2e-9),
            ("D = 3.5E-9 cm²/s", 3.5e-9),
            ("diffusivity 8.0e-10 cm²/s", 8.0e-10),
        ]

        for text, expected_value in test_cases:
            results = [{"text": text, "source": "Test", "path": "test.pdf", "id": "001"}]
            parsed = db._parse_permeability_from_text(results)

            assert parsed["diffusion_constant"] is not None
            assert abs(parsed["diffusion_constant"] - expected_value) < 1e-10

    def test_confidence_scoring(self):
        """Test that confidence increases with more found values"""
        db = CoatingPermeabilityDatabase()

        # Low confidence: only one value
        text_low = "moisture transmission: 8.5 mg per 24hr per sq in"
        results_low = [{"text": text_low, "source": "Test", "path": "test.pdf", "id": "001"}]
        parsed_low = db._parse_permeability_from_text(results_low)
        assert parsed_low["confidence"] == "medium"  # 1 value found

        # High confidence: multiple values
        text_high = (
            "moisture transmission: 8.5 mg per 24hr per sq in, "
            "oxygen permeability: 0.15 cm³·mil, "
            "diffusion constant: 1.2e-9 cm²/s"
        )
        results_high = [{"text": text_high, "source": "Test", "path": "test.pdf", "id": "001"}]
        parsed_high = db._parse_permeability_from_text(results_high)
        assert parsed_high["confidence"] == "high"  # 3 values found

    def test_empty_result_handling(self):
        """Test handling when no data is found"""
        def mock_search_empty(query, top_k=5):
            return []

        with patch('builtins.open', mock_open(read_data=MOCK_YAML)):
            with patch('pathlib.Path.exists', return_value=True):
                db = CoatingPermeabilityDatabase(semantic_search_function=mock_search_empty)

                result = db.get_permeability("unknown_coating", temperature_C=25.0)

        assert result["coating_type"] == "unknown_coating"
        assert result["moisture_transmission_mg_per_24hr_per_sqin"] is None
        assert result["provenance"]["method"] == "none"
        assert result["provenance"]["confidence"] == "none"
        assert "No data found" in result["provenance"]["quality_note"]

    def test_provenance_metadata_structure(self):
        """Test that provenance metadata has all required fields"""
        with patch('builtins.open', mock_open(read_data=MOCK_YAML)):
            with patch('pathlib.Path.exists', return_value=True):
                db = CoatingPermeabilityDatabase()
                result = db.get_permeability("epoxy", temperature_C=25.0)

        provenance = result["provenance"]

        # Check all required fields
        assert "method" in provenance
        assert "confidence" in provenance
        assert "extraction_date" in provenance
        assert "source_document" in provenance
        assert "source_repo" in provenance
        assert "quality_note" in provenance

    def test_list_available_coatings(self):
        """Test listing all available coatings in YAML"""
        with patch('builtins.open', mock_open(read_data=MOCK_YAML)):
            with patch('pathlib.Path.exists', return_value=True):
                db = CoatingPermeabilityDatabase()
                available = db.list_available_coatings()

        assert "epoxy" in available
        assert "polyurethane" in available
        assert len(available) == 2

    def test_yaml_missing_file(self):
        """Test behavior when YAML file doesn't exist"""
        with patch('pathlib.Path.exists', return_value=False):
            db = CoatingPermeabilityDatabase(semantic_search_function=None)

            yaml_data = db._load_yaml()

            # Should return empty dict
            assert yaml_data == {}

    def test_semantic_search_with_no_function_configured(self):
        """Test fallback when semantic search not configured"""
        with patch('builtins.open', mock_open(read_data=MOCK_YAML)):
            with patch('pathlib.Path.exists', return_value=True):
                db = CoatingPermeabilityDatabase(semantic_search_function=None)

                # Request unknown coating
                result = db.get_permeability("unknown_coating", temperature_C=25.0)

        # Should return empty result since no semantic search available
        assert result["provenance"]["method"] == "none"
        assert "No data available" in result["source"]


class TestCoatingAliases:
    """Test coating name alias handling"""

    @pytest.mark.parametrize("input_name,normalized", [
        ("Fusion Bonded Epoxy", "fbe"),
        ("FBE", "fbe"),
        ("fusion_bonded_epoxy", "fbe"),
        ("Polyvinyl Acetate", "pva"),
        ("PVA", "pva"),
        ("Polyurethane", "pu"),
        ("PU", "pu"),
    ])
    def test_coating_aliases(self, input_name, normalized):
        """Test various coating name aliases"""
        db = CoatingPermeabilityDatabase()
        assert db._normalize_coating_name(input_name) == normalized


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
