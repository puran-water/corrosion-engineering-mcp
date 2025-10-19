"""
Unit tests for CSV data loaders

Tests that CSV loaders correctly load authoritative data from CSV files.
Validates data integrity, caching, and error handling.
"""

import pytest
from data.csv_loaders import (
    load_materials_from_csv,
    load_cpt_data_from_csv,
    load_galvanic_series_from_csv,
    load_orr_diffusion_limits_from_csv,
    load_chloride_thresholds_from_csv,
    load_temperature_coefficients_from_csv,
    clear_caches,
    MaterialComposition,
)


class TestMaterialsCSVLoader:
    """Test materials_compositions.csv loader"""

    def test_load_materials_returns_dict(self):
        """Test that loader returns a dictionary"""
        materials = load_materials_from_csv()
        assert isinstance(materials, dict)
        assert len(materials) > 0

    def test_material_composition_dataclass(self):
        """Test that loaded materials are MaterialComposition instances"""
        materials = load_materials_from_csv()
        sample_material = next(iter(materials.values()))
        assert isinstance(sample_material, MaterialComposition)

    def test_316L_composition(self):
        """Test specific 316L composition from CSV"""
        materials = load_materials_from_csv()
        assert "316L" in materials

        ss316L = materials["316L"]
        assert ss316L.UNS == "S31603"
        assert 16.0 <= ss316L.Cr_wt_pct <= 18.0  # ASTM A240 range
        assert 10.0 <= ss316L.Ni_wt_pct <= 14.0
        assert 2.0 <= ss316L.Mo_wt_pct <= 3.0
        assert ss316L.Fe_bal is True
        assert ss316L.grade_type == "austenitic"

    def test_duplex_2507_composition(self):
        """Test duplex 2507 composition from CSV"""
        materials = load_materials_from_csv()
        assert "2507" in materials

        duplex = materials["2507"]
        assert duplex.UNS == "S32750"
        assert 24.0 <= duplex.Cr_wt_pct <= 26.0
        assert 3.0 <= duplex.Mo_wt_pct <= 5.0
        assert duplex.grade_type == "super_duplex"

    def test_caching(self):
        """Test that repeated calls use cache"""
        clear_caches()  # Clear cache first

        materials1 = load_materials_from_csv()
        materials2 = load_materials_from_csv()

        # Should return same object (cached)
        assert materials1 is materials2

    def test_cache_clear(self):
        """Test cache clearing"""
        materials1 = load_materials_from_csv()
        clear_caches()
        materials2 = load_materials_from_csv()

        # After clear, should reload (different object)
        assert materials1 is not materials2
        # But content should be identical
        assert materials1.keys() == materials2.keys()


class TestCPTDataCSVLoader:
    """Test astm_g48_cpt_data.csv loader"""

    def test_load_cpt_data_returns_dict(self):
        """Test that loader returns a dictionary"""
        cpt_data = load_cpt_data_from_csv()
        assert isinstance(cpt_data, dict)
        assert len(cpt_data) > 0

    def test_316L_cpt(self):
        """Test 316L CPT from ASTM G48"""
        cpt_data = load_cpt_data_from_csv()
        assert "316L" in cpt_data

        cpt = cpt_data["316L"]["CPT_C"]
        # Per ASTM G48-11, 316L CPT typically 5-15°C
        assert 0 <= cpt <= 20

    def test_2507_cpt(self):
        """Test super duplex 2507 CPT"""
        cpt_data = load_cpt_data_from_csv()
        if "2507" in cpt_data:
            cpt = cpt_data["2507"]["CPT_C"]
            # Super duplex should have high CPT (>40°C)
            assert cpt > 40


class TestGalvanicSeriesCSVLoader:
    """Test astm_g82_galvanic_series.csv loader"""

    def test_load_galvanic_series_returns_dict(self):
        """Test that loader returns a dictionary"""
        galvanic = load_galvanic_series_from_csv()
        assert isinstance(galvanic, dict)
        assert len(galvanic) > 0

    def test_316_stainless_potential(self):
        """Test 316 stainless steel galvanic potential"""
        galvanic = load_galvanic_series_from_csv()

        # 316 stainless should be in the data
        found_316 = False
        for key in galvanic.keys():
            if "316" in key:
                potential = galvanic[key]
                # Should be noble (negative potential in SCE)
                assert -0.5 <= potential <= 0.0
                found_316 = True
                break

        assert found_316, "316 stainless steel not found in galvanic series"

    def test_zinc_potential(self):
        """Test zinc galvanic potential (active metal)"""
        galvanic = load_galvanic_series_from_csv()

        # Zinc should be active (more negative potential)
        found_zinc = False
        for key in galvanic.keys():
            if "zinc" in key.lower() or "Zn" in key:
                potential = galvanic[key]
                # Zinc is active: -0.8 to -1.1 V SCE
                assert -1.2 <= potential <= -0.6
                found_zinc = True
                break

        assert found_zinc, "Zinc not found in galvanic series"


class TestORRDiffusionLimitsCSVLoader:
    """Test orr_diffusion_limits.csv loader"""

    def test_load_orr_limits_returns_dict(self):
        """Test that loader returns a dictionary"""
        orr_limits = load_orr_diffusion_limits_from_csv()
        assert isinstance(orr_limits, dict)
        assert len(orr_limits) > 0

    def test_seawater_25C_limit(self):
        """Test ORR limit in seawater at 25°C"""
        orr_limits = load_orr_diffusion_limits_from_csv()
        assert "seawater_25C" in orr_limits

        i_lim = orr_limits["seawater_25C"]
        # Typical ORR diffusion limit in seawater: 3-7 A/m²
        assert 2.0 <= i_lim <= 10.0

    def test_seawater_40C_higher_than_25C(self):
        """Test that ORR limit increases with temperature"""
        orr_limits = load_orr_diffusion_limits_from_csv()

        if "seawater_25C" in orr_limits and "seawater_40C" in orr_limits:
            i_lim_25C = orr_limits["seawater_25C"]
            i_lim_40C = orr_limits["seawater_40C"]
            # Higher temperature → higher diffusion limit
            assert i_lim_40C > i_lim_25C


class TestChlorideThresholdsCSVLoader:
    """Test iso18070_chloride_thresholds.csv loader"""

    def test_load_chloride_thresholds_returns_dict(self):
        """Test that loader returns a dictionary"""
        thresholds = load_chloride_thresholds_from_csv()
        assert isinstance(thresholds, dict)
        assert len(thresholds) > 0

    def test_304_threshold(self):
        """Test 304 stainless steel chloride threshold"""
        thresholds = load_chloride_thresholds_from_csv()
        assert "304" in thresholds

        threshold = thresholds["304"]
        # 304 has low pitting resistance: 10-100 mg/L
        assert 10 <= threshold <= 150

    def test_316L_higher_than_304(self):
        """Test that 316L has higher chloride threshold than 304"""
        thresholds = load_chloride_thresholds_from_csv()

        if "304" in thresholds and "316L" in thresholds:
            threshold_304 = thresholds["304"]
            threshold_316L = thresholds["316L"]
            # 316L (Mo-bearing) should have higher threshold
            assert threshold_316L > threshold_304

    def test_2507_super_duplex_threshold(self):
        """Test super duplex 2507 chloride threshold"""
        thresholds = load_chloride_thresholds_from_csv()

        if "2507" in thresholds:
            threshold = thresholds["2507"]
            # Super duplex: extreme resistance (>1000 mg/L)
            assert threshold > 500


class TestTemperatureCoefficientsCSVLoader:
    """Test iso18070_temperature_coefficients.csv loader"""

    def test_load_temp_coefficients_returns_dict(self):
        """Test that loader returns a dictionary"""
        coeffs = load_temperature_coefficients_from_csv()
        assert isinstance(coeffs, dict)
        assert len(coeffs) > 0

    def test_austenitic_coefficient(self):
        """Test austenitic temperature coefficient"""
        coeffs = load_temperature_coefficients_from_csv()
        assert "austenitic" in coeffs

        coeff = coeffs["austenitic"]
        # Per ISO 18070: austenitic ~0.05 /°C
        assert 0.04 <= coeff <= 0.06

    def test_duplex_lower_than_austenitic(self):
        """Test that duplex has lower temp coefficient than austenitic"""
        coeffs = load_temperature_coefficients_from_csv()

        if "austenitic" in coeffs and "duplex" in coeffs:
            coeff_austenitic = coeffs["austenitic"]
            coeff_duplex = coeffs["duplex"]
            # Duplex more stable → lower coefficient
            assert coeff_duplex < coeff_austenitic


class TestCacheManagement:
    """Test cache clearing functionality"""

    def test_clear_caches_resets_all_caches(self):
        """Test that clear_caches resets all loader caches"""
        # Load all data
        materials1 = load_materials_from_csv()
        cpt1 = load_cpt_data_from_csv()
        galvanic1 = load_galvanic_series_from_csv()

        # Clear caches
        clear_caches()

        # Reload
        materials2 = load_materials_from_csv()
        cpt2 = load_cpt_data_from_csv()
        galvanic2 = load_galvanic_series_from_csv()

        # All should be different objects (reloaded)
        assert materials1 is not materials2
        assert cpt1 is not cpt2
        assert galvanic1 is not galvanic2

        # But content should be identical
        assert materials1.keys() == materials2.keys()
        assert cpt1.keys() == cpt2.keys()
        assert galvanic1.keys() == galvanic2.keys()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
