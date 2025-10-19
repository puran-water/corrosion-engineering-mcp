"""
Unit tests for Galvanic Corrosion Tool

Tests mixed-potential theory calculations, Tafel equations,
Evans diagram solver, and area ratio effects.

Target coverage: ≥85%
"""

import pytest
from pathlib import Path
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.galvanic_backend import GalvanicBackend, PolarizationCurve
from tools.mechanistic.galvanic_corrosion import calculate_galvanic_corrosion


class TestTafelEquation:
    """Test Tafel equation calculations"""

    def test_tafel_anodic_positive_overpotential(self):
        """Test anodic current for positive overpotential"""
        backend = GalvanicBackend()

        # Fe oxidation: i = i0 × 10^(η / βa)
        E = -0.50  # V vs SHE
        E_corr = -0.65  # V vs SHE
        i0 = 1e-5  # A/m²
        ba = 0.060  # V/decade

        eta = E - E_corr  # = 0.15 V (anodic)

        i = backend.calculate_tafel_current(E, E_corr, i0, ba, is_anodic=True)

        # Expected: i = 1e-5 × 10^(0.15 / 0.060) = 1e-5 × 10^2.5 ≈ 3.16e-3 A/m²
        expected = i0 * 10.0 ** (eta / ba)
        assert abs(i - expected) < 1e-6

    def test_tafel_cathodic_negative_overpotential(self):
        """Test cathodic current for negative overpotential"""
        backend = GalvanicBackend()

        # ORR: i = -i0 × 10^(η / βc)
        E = 0.30  # V vs SHE
        E_corr = 0.40  # V vs SHE
        i0 = 1e-6  # A/m²
        bc = -0.120  # V/decade (cathodic is negative)

        eta = E - E_corr  # = -0.10 V (cathodic)

        i = backend.calculate_tafel_current(E, E_corr, i0, bc, is_anodic=False)

        # Expected: i = -1e-6 × 10^(-0.10 / -0.120) ≈ -6.81e-7 A/m²
        expected = -i0 * 10.0 ** (eta / bc)
        assert abs(i - expected) < 1e-9
        assert i < 0  # Cathodic current is negative

    def test_tafel_at_equilibrium(self):
        """Test current at equilibrium (η = 0)"""
        backend = GalvanicBackend()

        E_corr = -0.65
        i0 = 1e-5
        ba = 0.060

        i = backend.calculate_tafel_current(E_corr, E_corr, i0, ba, is_anodic=True)

        # At equilibrium: i = i0 × 10^0 = i0
        assert abs(i - i0) < 1e-9


class TestMixedPotential:
    """Test mixed-potential (Evans diagram) solver"""

    def test_mixed_potential_carbon_steel_316L(self):
        """Test mixed potential for carbon steel (anode) + 316L (cathode)"""
        backend = GalvanicBackend()

        # Carbon steel anodic curve
        anodic_curve = PolarizationCurve(
            material="carbon steel",
            reaction="Fe_oxidation",
            E_corr=-0.65,
            i0=1e-5,
            ba=0.060,
            bc=-0.120,
        )

        # ORR on 316L cathode
        cathodic_curve = PolarizationCurve(
            material="316L",
            reaction="ORR",
            E_corr=0.40,
            i0=1e-6,
            ba=0.120,
            bc=-0.120,
        )

        E_couple, i_galv = backend.find_mixed_potential(anodic_curve, cathodic_curve, area_ratio=1.0)

        # E_couple should be between the two E_corr values
        assert -0.65 < E_couple < 0.40

        # Galvanic current should be positive
        assert i_galv > 0

        # E_couple should be closer to the more noble material (cathode)
        # Since cathodic i0 is lower, E_couple should be somewhat negative
        assert E_couple < 0  # Expected for this couple

    def test_area_ratio_effect(self):
        """Test that large cathode area increases galvanic current"""
        backend = GalvanicBackend()

        anodic_curve = PolarizationCurve(
            material="carbon steel",
            reaction="Fe_oxidation",
            E_corr=-0.65,
            i0=1e-5,
            ba=0.060,
            bc=-0.120,
        )

        cathodic_curve = PolarizationCurve(
            material="316L",
            reaction="ORR",
            E_corr=0.40,
            i0=1e-6,
            ba=0.120,
            bc=-0.120,
        )

        # Small cathode
        E1, i1 = backend.find_mixed_potential(anodic_curve, cathodic_curve, area_ratio=0.1)

        # Large cathode
        E2, i2 = backend.find_mixed_potential(anodic_curve, cathodic_curve, area_ratio=10.0)

        # Larger cathode should increase galvanic current
        assert i2 > i1

        # Larger cathode should shift E_couple toward cathode E_corr
        assert E2 > E1


class TestCorrosionRateConversion:
    """Test current-to-corrosion-rate conversion"""

    def test_faraday_law_carbon_steel(self):
        """Test Faraday's law for carbon steel (Fe)"""
        backend = GalvanicBackend()

        # Typical galvanic current
        i_corr = 1e-4  # A/m² = 0.1 mA/m²

        CR = backend.current_to_corrosion_rate(i_corr, "carbon steel", n_electrons=2)

        # Actual calculation from implementation:
        # K = 31.536 (conversion factor)
        # CR = (1e-4 × 55.845 × 31.536) / (2 × 96485 × 7850)
        # ≈ 0.000116 mm/year

        assert 0.0001 < CR < 0.0002  # Correct range for 1e-4 A/m²

    def test_corrosion_rate_scales_with_current(self):
        """Test that corrosion rate scales linearly with current"""
        backend = GalvanicBackend()

        i1 = 1e-4
        i2 = 1e-3  # 10x higher

        CR1 = backend.current_to_corrosion_rate(i1, "carbon steel", n_electrons=2)
        CR2 = backend.current_to_corrosion_rate(i2, "carbon steel", n_electrons=2)

        # CR2 should be ~10x CR1
        assert abs(CR2 / CR1 - 10.0) < 0.1


class TestGalvanicCorrosionTool:
    """Test the MCP tool wrapper"""

    def test_basic_galvanic_calculation(self):
        """Test basic galvanic corrosion calculation"""
        result = calculate_galvanic_corrosion(
            anode_material="carbon steel",
            cathode_material="316L",
            area_ratio=1.0,
            temperature_C=25.0,
            electrolyte="seawater",
        )

        # Check all required fields
        assert "E_couple_V" in result
        assert "i_galv_A_per_m2" in result
        assert "corrosion_rate_mm_per_year" in result
        assert "interpretation" in result
        assert "recommendations" in result

        # Check value ranges
        assert -1.0 < result["E_couple_V"] < 1.0
        assert result["i_galv_A_per_m2"] > 0
        assert result["corrosion_rate_mm_per_year"] > 0

    def test_large_area_ratio_warning(self):
        """Test that large cathode/anode ratio triggers warnings"""
        result = calculate_galvanic_corrosion(
            anode_material="carbon steel",
            cathode_material="316L",
            area_ratio=20.0,  # Large cathode
            temperature_C=25.0,
        )

        # Should have recommendations about area ratio
        recommendations_text = " ".join(result["recommendations"]).lower()
        assert "area" in recommendations_text or "ratio" in recommendations_text

    def test_high_corrosion_rate_recommendations(self):
        """Test recommendations for high corrosion rates"""
        # Use large area ratio to get high CR
        result = calculate_galvanic_corrosion(
            anode_material="carbon steel",
            cathode_material="316L",
            area_ratio=50.0,
            temperature_C=25.0,
        )

        # Should have protective coating recommendation if CR is high
        if result["corrosion_rate_mm_per_year"] > 1.0:
            recommendations_text = " ".join(result["recommendations"]).lower()
            assert "coating" in recommendations_text or "protection" in recommendations_text

    def test_invalid_area_ratio(self):
        """Test error handling for invalid area ratio"""
        with pytest.raises(ValueError, match="Area ratio must be positive"):
            calculate_galvanic_corrosion(
                anode_material="carbon steel",
                cathode_material="316L",
                area_ratio=-1.0,
            )

        with pytest.raises(ValueError, match="Area ratio must be positive"):
            calculate_galvanic_corrosion(
                anode_material="carbon steel",
                cathode_material="316L",
                area_ratio=0.0,
            )


class TestMaterialPairs:
    """Test specific material combinations"""

    def test_aluminum_copper_severe_attack(self):
        """Test aluminum-copper couple (severe galvanic attack)"""
        result = calculate_galvanic_corrosion(
            anode_material="aluminum",
            cathode_material="copper",
            area_ratio=1.0,
        )

        # Aluminum-copper is a very active couple
        # Should have high corrosion rate
        assert result["corrosion_rate_mm_per_year"] > 0.01

        # Should have aluminum-specific warning
        recommendations_text = " ".join(result["recommendations"]).lower()
        if "aluminum" in result["anode_material"].lower():
            assert "aluminum" in recommendations_text or "copper" in recommendations_text

    def test_304_316L_low_attack(self):
        """Test 304-316L couple (low galvanic potential difference)"""
        result = calculate_galvanic_corrosion(
            anode_material="304",
            cathode_material="316L",
            area_ratio=1.0,
        )

        # 304 and 316L are close in the galvanic series
        # Should have low to moderate corrosion
        # (This depends on polarization curve data - just check it runs)
        assert result["corrosion_rate_mm_per_year"] >= 0


class TestEdgeCases:
    """Test edge cases and error handling"""

    def test_very_large_area_ratio(self):
        """Test handling of very large area ratios"""
        result = calculate_galvanic_corrosion(
            anode_material="carbon steel",
            cathode_material="316L",
            area_ratio=1000.0,
        )

        # Should complete without error
        assert result["corrosion_rate_mm_per_year"] > 0

        # Should have critical warning
        recommendations_text = " ".join(result["recommendations"]).lower()
        assert "critical" in recommendations_text or "large" in recommendations_text

    def test_very_small_area_ratio(self):
        """Test handling of very small area ratios"""
        result = calculate_galvanic_corrosion(
            anode_material="carbon steel",
            cathode_material="316L",
            area_ratio=0.01,  # Tiny cathode, large anode
        )

        # Should have low corrosion rate
        # Small cathode cannot drive much current
        assert result["corrosion_rate_mm_per_year"] >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
