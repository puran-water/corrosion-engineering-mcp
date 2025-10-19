"""
Unit tests for AuthoritativeMaterialDatabase

Tests lazy loading, multi-source data merging, PREN calculation,
composition provenance, and fallback logic.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import pandas as pd
from pathlib import Path
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.material_database import AuthoritativeMaterialDatabase


class TestAuthoritativeMaterialDatabase:
    """Test suite for AuthoritativeMaterialDatabase"""

    def test_lazy_loading_not_triggered_on_init(self):
        """Test that lazy loading doesn't occur during initialization"""
        db = AuthoritativeMaterialDatabase(use_cache=False)

        assert db._loaded is False
        assert db._galvanic_data is None
        assert db._kittycad_stainless is None

    @patch('utils.material_database.pd.read_xml')
    def test_lazy_loading_triggered_on_first_access(self, mock_read_xml):
        """Test that lazy loading triggers on first property access"""
        # Mock XML data
        mock_galvanic_df = pd.DataFrame({
            'Material': ['Carbon Steel', '316L'],
            'Potential': [-0.6, -0.1]
        })
        mock_read_xml.return_value = mock_galvanic_df

        with patch('utils.material_database.requests.get') as mock_get:
            # Mock KittyCAD JSON responses
            mock_response = Mock()
            mock_response.json.return_value = {}
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response

            db = AuthoritativeMaterialDatabase(use_cache=False)
            properties = db.get_material_properties("316L")

            # Verify lazy loading occurred
            assert db._loaded is True
            assert db._galvanic_data is not None

    def test_pren_calculation_316l(self):
        """Test PREN calculation for 316L (from ASTM A240 CSV data)"""
        db = AuthoritativeMaterialDatabase(use_cache=False)

        # 316L per ASTM A240: Cr=16.5, Mo=2.0, N=0.1
        # PREN = 16.5 + 3.3*2.0 + 16*0.1 = 16.5 + 6.6 + 1.6 = 24.7
        pren = db.calculate_pren("316L")

        assert pren is not None
        assert 24.0 <= pren <= 25.0  # ASTM A240 composition

    def test_pren_calculation_duplex_2205(self):
        """Test PREN calculation for Duplex 2205 (now using CSV key: 2205)"""
        db = AuthoritativeMaterialDatabase(use_cache=False)

        # Duplex 2205 per ASTM A240: Cr=22, Mo=3.0, N=0.17
        # PREN = 22 + 3.3*3.0 + 16*0.17 = 22 + 9.9 + 2.72 = 34.62
        pren = db.calculate_pren("2205")  # CSV uses "2205" not "duplex_2205"

        assert pren is not None
        assert 34.0 <= pren <= 35.5

    def test_cpt_estimation_from_pren(self):
        """Test CPT estimation from PREN"""
        db = AuthoritativeMaterialDatabase(use_cache=False)

        # CPT ≈ PREN - 10 (simplified correlation)
        # 316L PREN ~25.7 → CPT ~15-16°C
        cpt = db.estimate_cpt("316L")

        assert cpt is not None
        assert 14.0 <= cpt <= 17.0

    def test_composition_provenance_metadata(self):
        """Test that composition provenance is tagged as fallback"""
        db = AuthoritativeMaterialDatabase(use_cache=False)

        properties = db.get_material_properties("316L")

        # Verify provenance metadata exists (now using CSV-backed data)
        assert "composition_provenance" in properties
        assert properties["composition_provenance"]["method"] == "CSV_loader"
        assert properties["composition_provenance"]["authoritative"] is True
        assert "authoritative" in properties["composition_provenance"]["quality"]

    def test_fallback_galvanic_potential(self):
        """Test fallback galvanic potential values"""
        db = AuthoritativeMaterialDatabase(use_cache=False)

        # Test fallback values
        assert db._fallback_galvanic_potential("CS") == -0.6
        assert db._fallback_galvanic_potential("316L") == -0.1
        assert db._fallback_galvanic_potential("duplex_2205") == -0.05

    def test_cost_factor_retrieval(self):
        """Test cost factor relative to carbon steel"""
        db = AuthoritativeMaterialDatabase(use_cache=False)

        # Carbon steel = 1.0 (reference)
        assert db._get_cost_factor("CS") == 1.0

        # 316L more expensive
        cost_316l = db._get_cost_factor("316L")
        assert cost_316l > 1.0

        # Super duplex very expensive
        cost_super = db._get_cost_factor("super_duplex")
        assert cost_super > cost_316l

    def test_material_name_mapping(self):
        """Test full material name retrieval"""
        db = AuthoritativeMaterialDatabase(use_cache=False)

        assert db._get_full_name("CS") == "Carbon Steel"
        assert db._get_full_name("316L") == "316L Stainless Steel"
        assert db._get_full_name("duplex_2205") == "Duplex 2205"
        assert db._get_full_name("C276") == "Hastelloy C-276"

    @patch('utils.material_database.pd.read_xml')
    def test_network_failure_fallback(self, mock_read_xml):
        """Test that network failures trigger fallback to hard-coded values"""
        # Simulate network failure
        mock_read_xml.side_effect = Exception("Network error")

        with patch('utils.material_database.requests.get') as mock_get:
            mock_get.side_effect = Exception("Network error")

            db = AuthoritativeMaterialDatabase(use_cache=False)
            properties = db.get_material_properties("316L")

            # Should still return data via fallback
            assert properties is not None
            assert "material_id" in properties
            assert properties["material_id"] == "316L"

            # Galvanic potential should use fallback
            galvanic = db._get_galvanic_potential("316L")
            assert galvanic == -0.1  # Fallback value

    def test_cache_behavior(self):
        """Test that caching prevents redundant lookups"""
        db = AuthoritativeMaterialDatabase(use_cache=True)

        # First call
        props1 = db.get_material_properties("316L")

        # Second call should hit cache
        props2 = db.get_material_properties("316L")

        # Should be same object reference (from cache)
        assert props1 is props2

    def test_no_cache_behavior(self):
        """Test that cache can be disabled"""
        db = AuthoritativeMaterialDatabase(use_cache=False)

        # First call
        props1 = db.get_material_properties("316L")

        # Second call creates new object
        props2 = db.get_material_properties("316L")

        # Should be different object references
        assert props1 is not props2

    def test_unknown_material_handling(self):
        """Test handling of unknown material IDs"""
        db = AuthoritativeMaterialDatabase(use_cache=False)

        properties = db.get_material_properties("UNKNOWN_ALLOY")

        # Should return basic structure with minimal data
        assert properties["material_id"] == "UNKNOWN_ALLOY"
        assert properties["name"] == "UNKNOWN_ALLOY"  # Falls back to ID

        # Composition should be None
        composition = db._get_composition("UNKNOWN_ALLOY")
        assert composition is None

    def test_pren_with_missing_composition(self):
        """Test PREN calculation with missing composition data"""
        db = AuthoritativeMaterialDatabase(use_cache=False)

        # Unknown material has no composition
        pren = db.calculate_pren("UNKNOWN_ALLOY")

        assert pren is None

    def test_multi_source_data_merging(self):
        """Test that properties from multiple sources are merged correctly"""
        with patch('utils.material_database.pd.read_xml') as mock_xml:
            with patch('utils.material_database.requests.get') as mock_get:
                # Mock USNRL galvanic data
                mock_galvanic_df = pd.DataFrame({
                    'Material': ['Stainless Steel 316'],
                    'Potential': [-0.08]
                })
                mock_xml.return_value = mock_galvanic_df

                # Mock KittyCAD data
                mock_response = Mock()
                mock_response.json.return_value = {
                    "316L": {
                        "density": 8000,
                        "yield_strength": 290,
                    }
                }
                mock_response.raise_for_status = Mock()
                mock_get.return_value = mock_response

                db = AuthoritativeMaterialDatabase(use_cache=False)
                properties = db.get_material_properties("316L")

                # Should have data from multiple sources
                assert "material_id" in properties
                assert "composition" in properties  # From fallback
                assert "PREN" in properties  # Calculated
                assert "cost_factor" in properties  # From internal DB


class TestPRENCalculations:
    """Focused tests for PREN calculation accuracy"""

    @pytest.mark.parametrize("material_id,expected_range", [
        ("304", (19.0, 20.0)),      # Cr=19, Mo=0, N=0.08
        ("316L", (24.0, 25.0)),     # Cr=16.5, Mo=2.0, N=0.1 (ASTM A240)
        ("2205", (34.0, 36.0)),  # Cr=22, Mo=3, N=0.17 (CSV key is "2205")
        ("2507", (41.0, 43.0)), # Cr=25, Mo=4, N=0.27 (CSV key is "2507")
    ])
    def test_pren_ranges(self, material_id, expected_range):
        """Test PREN values for various stainless steels"""
        db = AuthoritativeMaterialDatabase(use_cache=False)
        pren = db.calculate_pren(material_id)

        assert pren is not None
        assert expected_range[0] <= pren <= expected_range[1]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
