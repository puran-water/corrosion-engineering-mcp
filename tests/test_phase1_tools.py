"""
Unit tests for Phase 1 tools:
- run_phreeqc_speciation (Tier 1 chemistry)
- predict_co2_h2s_corrosion (NORSOK M-506)
- predict_aerated_chloride_corrosion (ORR diffusion-limited)

Tests cover:
1. Basic functionality and valid inputs
2. Edge cases and boundary conditions
3. Error handling and validation
4. Physical consistency checks
5. Cross-validation with known benchmarks
"""

import pytest
import json
from pathlib import Path
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.chemistry.run_speciation import run_phreeqc_speciation
from tools.mechanistic.co2_h2s_corrosion import predict_co2_h2s_corrosion
from tools.mechanistic.aerated_chloride_corrosion import predict_aerated_chloride_corrosion


class TestRunPhreeqcSpeciation:
    """Test suite for run_phreeqc_speciation tool"""

    def test_basic_freshwater_speciation(self):
        """Test speciation for typical freshwater composition"""
        ions_json = json.dumps({
            "Na+": 1000.0,
            "Cl-": 1500.0,
            "Ca2+": 100.0,
            "HCO3-": 200.0,
        })

        result = run_phreeqc_speciation(
            ions_json=ions_json,
            temperature_C=25.0,
        )

        # Verify result structure
        assert "pH" in result
        assert "ionic_strength_M" in result
        assert "species" in result
        assert "saturation_indices" in result
        assert "interpretation" in result

        # Verify physical reasonableness
        assert 6.0 <= result["pH"] <= 9.0
        assert result["ionic_strength_M"] > 0

    def test_seawater_speciation(self):
        """Test speciation for seawater composition"""
        ions_json = json.dumps({
            "Na+": 10752.0,
            "Cl-": 19345.0,
            "Mg2+": 1295.0,
            "SO4-2": 2701.0,
            "Ca2+": 412.0,
            "K+": 399.0,
            "HCO3-": 142.0,
        })

        result = run_phreeqc_speciation(
            ions_json=ions_json,
            temperature_C=25.0,
        )

        # Seawater should have pH ~7-8.5 (depends on alkalinity constraints)
        # Note: Truncated ion recipe without full CO₂ equilibrium gives lower pH
        assert 6.8 <= result["pH"] <= 8.5

        # Seawater ionic strength ~0.7 M
        assert 0.5 <= result["ionic_strength_M"] <= 1.0

    def test_acidic_solution_speciation(self):
        """Test speciation for acidic solution"""
        ions_json = json.dumps({
            "Na+": 500.0,
            "Cl-": 800.0,
        })

        result = run_phreeqc_speciation(
            ions_json=ions_json,
            temperature_C=25.0,
            pH=4.5,  # Specify acidic pH
        )

        # Should maintain acidic pH
        assert 4.0 <= result["pH"] <= 5.0
        assert "acidic" in result["interpretation"].lower() or "Acidic" in result["interpretation"]

    def test_high_temperature_speciation(self):
        """Test speciation at elevated temperature"""
        ions_json = json.dumps({
            "Na+": 1000.0,
            "Cl-": 1500.0,
        })

        result = run_phreeqc_speciation(
            ions_json=ions_json,
            temperature_C=80.0,
        )

        # High temperature affects pH (should shift lower)
        assert result["temperature_C"] == 80.0
        assert result["pH"] > 0  # Basic sanity check

    def test_invalid_json_raises_error(self):
        """Test that invalid JSON raises ValueError"""
        with pytest.raises(ValueError, match="Invalid JSON"):
            run_phreeqc_speciation(
                ions_json="not valid json{",
                temperature_C=25.0,
            )

    def test_charge_balance_validation(self):
        """Test charge balance validation"""
        # Unbalanced solution (too much Cl⁻)
        ions_json = json.dumps({
            "Na+": 100.0,
            "Cl-": 10000.0,  # Way too much negative charge
        })

        # Should still run with warning (validate_charge_balance=True)
        result = run_phreeqc_speciation(
            ions_json=ions_json,
            temperature_C=25.0,
            validate_charge_balance=True,
            max_imbalance=50.0,  # Allow large imbalance for test
        )

        assert "pH" in result


class TestPredictCO2H2SCorrosion:
    """Test suite for predict_co2_h2s_corrosion tool (NORSOK M-506)"""

    def test_basic_co2_corrosion(self):
        """Test basic CO₂ corrosion prediction"""
        result = predict_co2_h2s_corrosion(
            temperature_C=60.0,
            pressure_bar=50.0,
            co2_fraction=0.05,
            pH=5.0,
            superficial_gas_velocity_m_s=3.0,
            superficial_liquid_velocity_m_s=1.0,
            pipe_diameter_m=0.15,
        )

        # Verify result structure
        assert "corrosion_rate_mm_y" in result
        assert "corrosion_rate_mpy" in result
        assert "pH_calculated" in result
        assert "mechanism" in result
        assert "severity" in result
        assert "provenance" in result

        # Verify units conversion
        assert abs(result["corrosion_rate_mpy"] - result["corrosion_rate_mm_y"] * 39.37) < 0.1

        # Verify CO₂ partial pressure
        assert result["co2_partial_pressure_bar"] == pytest.approx(0.05 * 50.0, rel=0.01)

        # Verify mechanism
        assert "CO₂" in result["mechanism"]

    def test_zero_co2_gives_zero_corrosion(self):
        """Test that zero CO₂ gives zero corrosion rate"""
        result = predict_co2_h2s_corrosion(
            temperature_C=40.0,
            pressure_bar=10.0,
            co2_fraction=0.0,  # No CO₂
            pH=7.0,
            pipe_diameter_m=0.2,
        )

        assert result["corrosion_rate_mm_y"] == 0.0

    def test_sour_corrosion_with_h2s(self):
        """Test H₂S sour corrosion prediction"""
        result = predict_co2_h2s_corrosion(
            temperature_C=50.0,
            pressure_bar=30.0,
            co2_fraction=0.01,
            h2s_fraction=0.005,  # Significant H₂S
            pH=5.5,
            pipe_diameter_m=0.2,
        )

        # Should detect H₂S
        assert result["h2s_partial_pressure_bar"] > 0

        # Mechanism should mention H₂S or sour
        assert "H₂S" in result["mechanism"] or "sour" in result["mechanism"].lower()

    def test_high_temperature_corrosion(self):
        """Test corrosion at high temperature"""
        result_high_temp = predict_co2_h2s_corrosion(
            temperature_C=120.0,
            pressure_bar=100.0,
            co2_fraction=0.1,
            pH=4.5,
            pipe_diameter_m=0.2,
        )

        # Should have warning about high temperature
        assert any("Temperature" in w or "temperature" in w
                  for w in result_high_temp["provenance"]["warnings"])

    def test_calculated_vs_supplied_pH(self):
        """Test that user-supplied pH is honored"""
        # With user-supplied pH
        result_supplied = predict_co2_h2s_corrosion(
            temperature_C=40.0,
            pressure_bar=10.0,
            co2_fraction=0.05,
            pH=5.0,  # User supplies pH
            pipe_diameter_m=0.2,
        )

        # pH should match supplied value
        assert result_supplied["pH_calculated"] == 5.0

        # With calculated pH
        result_calc = predict_co2_h2s_corrosion(
            temperature_C=40.0,
            pressure_bar=10.0,
            co2_fraction=0.05,
            pH=None,  # Let PHREEQC calculate
            bicarbonate_mg_L=500.0,
            ionic_strength_mg_L=5000.0,
            pipe_diameter_m=0.2,
        )

        # pH should be calculated (not None)
        assert result_calc["pH_calculated"] is not None
        assert result_calc["pH_calculated"] > 0

    def test_temperature_out_of_range_raises_error(self):
        """Test that temperature outside NORSOK range raises ValueError"""
        with pytest.raises(ValueError, match="outside NORSOK M-506 range"):
            predict_co2_h2s_corrosion(
                temperature_C=200.0,  # Too high
                pressure_bar=10.0,
                co2_fraction=0.05,
                pipe_diameter_m=0.2,
            )

    def test_invalid_co2_fraction_raises_error(self):
        """Test that invalid CO₂ fraction raises ValueError"""
        with pytest.raises(ValueError, match="CO₂ fraction.*must be between 0 and 1"):
            predict_co2_h2s_corrosion(
                temperature_C=40.0,
                pressure_bar=10.0,
                co2_fraction=1.5,  # Invalid (>1)
                pipe_diameter_m=0.2,
            )


class TestPredictAeratedChlorideCorrosion:
    """Test suite for predict_aerated_chloride_corrosion tool"""

    def test_basic_freshwater_corrosion(self):
        """Test basic aerated freshwater corrosion"""
        result = predict_aerated_chloride_corrosion(
            temperature_C=25.0,
            dissolved_oxygen_mg_L=8.0,
            chloride_mg_L=100.0,  # Freshwater
            pH=7.0,
        )

        # Verify result structure
        assert "corrosion_rate_mm_y" in result
        assert "corrosion_rate_mpy" in result
        assert "limiting_current_density_A_m2" in result
        assert "dissolved_oxygen_mg_L" in result
        assert "mechanism" in result
        assert "severity" in result

        # Verify physical reasonableness
        assert result["corrosion_rate_mm_y"] > 0
        assert result["limiting_current_density_A_m2"] > 0

        # Freshwater should be in mechanism
        assert "freshwater" in result["mechanism"].lower()

    def test_seawater_corrosion(self):
        """Test aerated seawater corrosion"""
        result = predict_aerated_chloride_corrosion(
            temperature_C=25.0,
            dissolved_oxygen_mg_L=6.5,
            chloride_mg_L=19000.0,  # Seawater
            pH=8.1,
        )

        # Seawater should be identified
        assert "seawater" in result["mechanism"].lower()

        # Corrosion rate should be typical for seawater (very low due to ORR limit)
        # Typical range: 0.005-0.3 mm/y for diffusion-limited corrosion
        assert 0.001 <= result["corrosion_rate_mm_y"] <= 1.0

    def test_brackish_water_corrosion(self):
        """Test brackish water corrosion"""
        result = predict_aerated_chloride_corrosion(
            temperature_C=25.0,
            dissolved_oxygen_mg_L=7.0,
            chloride_mg_L=5000.0,  # Brackish
            pH=7.5,
        )

        # Brackish water should be identified
        assert "brackish" in result["mechanism"].lower()

    def test_air_saturated_assumption(self):
        """Test DO calculation when not provided (air-saturated)"""
        result = predict_aerated_chloride_corrosion(
            temperature_C=25.0,
            dissolved_oxygen_mg_L=None,  # Let tool calculate
            chloride_mg_L=100.0,
            pH=7.0,
        )

        # Should calculate reasonable DO for 25°C (~8-9 mg/L for freshwater)
        assert 6.0 <= result["dissolved_oxygen_mg_L"] <= 10.0

    def test_temperature_effect_on_do(self):
        """Test that temperature affects dissolved oxygen solubility"""
        result_cold = predict_aerated_chloride_corrosion(
            temperature_C=5.0,
            dissolved_oxygen_mg_L=None,  # Air-saturated
            chloride_mg_L=100.0,
            pH=7.0,
        )

        result_hot = predict_aerated_chloride_corrosion(
            temperature_C=60.0,
            dissolved_oxygen_mg_L=None,  # Air-saturated
            chloride_mg_L=100.0,
            pH=7.0,
        )

        # Cold water holds more DO
        assert result_cold["dissolved_oxygen_mg_L"] > result_hot["dissolved_oxygen_mg_L"]

    def test_temperature_dependence_via_do(self):
        """Test that temperature affects corrosion via DO concentration (Codex-approved scaling)"""
        # Test at 25°C (CSV reference point)
        result_ref = predict_aerated_chloride_corrosion(
            temperature_C=25.0,
            dissolved_oxygen_mg_L=None,  # Calculate from Garcia-Benson
            chloride_mg_L=100.0,
            pH=7.0,
        )

        # Test at lower temperature (outside CSV range, uses DO scaling)
        result_cold = predict_aerated_chloride_corrosion(
            temperature_C=5.0,
            dissolved_oxygen_mg_L=None,  # Calculate from Garcia-Benson
            chloride_mg_L=100.0,
            pH=7.0,
        )

        # Cold water has higher DO, so should have higher i_lim and corrosion rate
        assert result_cold["dissolved_oxygen_mg_L"] > result_ref["dissolved_oxygen_mg_L"]
        assert result_cold["limiting_current_density_A_m2"] > result_ref["limiting_current_density_A_m2"]

    def test_low_do_warning(self):
        """Test warning for low dissolved oxygen"""
        result = predict_aerated_chloride_corrosion(
            temperature_C=25.0,
            dissolved_oxygen_mg_L=0.3,  # Very low DO
            chloride_mg_L=100.0,
            pH=7.0,
        )

        # Should have warning about low DO
        assert any("DO" in w or "oxygen" in w.lower()
                  for w in result["provenance"]["warnings"])

    def test_low_ph_warning(self):
        """Test warning for low pH (outside aerated model range)"""
        result = predict_aerated_chloride_corrosion(
            temperature_C=25.0,
            dissolved_oxygen_mg_L=8.0,
            chloride_mg_L=100.0,
            pH=5.5,  # Low pH
        )

        # Should have warning about low pH
        assert any("pH" in w or "acidic" in w.lower()
                  for w in result["provenance"]["warnings"])

    def test_stainless_steel_raises_error(self):
        """Test that stainless steel material raises appropriate error"""
        with pytest.raises(ValueError, match="not valid for aerated corrosion model"):
            predict_aerated_chloride_corrosion(
                temperature_C=25.0,
                dissolved_oxygen_mg_L=8.0,
                chloride_mg_L=19000.0,
                pH=8.1,
                material="stainless_304",  # Invalid for this model
            )

    def test_temperature_out_of_range_raises_error(self):
        """Test that temperature outside range raises ValueError"""
        with pytest.raises(ValueError, match="outside model range"):
            predict_aerated_chloride_corrosion(
                temperature_C=100.0,  # Too high
                dissolved_oxygen_mg_L=8.0,
                chloride_mg_L=100.0,
                pH=7.0,
            )


class TestPhase1Integration:
    """Integration tests for Phase 1 tool interactions"""

    def test_speciation_to_co2_corrosion_workflow(self):
        """Test workflow: run speciation, then use results for CO₂ corrosion"""
        # Step 1: Run speciation on CO₂-saturated water
        ions_json = json.dumps({
            "Na+": 5000.0,
            "Cl-": 8000.0,
            "HCO3-": 500.0,
        })

        speciation_result = run_phreeqc_speciation(
            ions_json=ions_json,
            temperature_C=40.0,
        )

        # Step 2: Use calculated pH for CO₂ corrosion prediction
        corrosion_result = predict_co2_h2s_corrosion(
            temperature_C=40.0,
            pressure_bar=10.0,
            co2_fraction=0.05,
            pH=speciation_result["pH"],  # Use from speciation
            bicarbonate_mg_L=500.0,
            ionic_strength_mg_L=speciation_result["ionic_strength_M"] * 1000,  # Convert to mg/L approx
            pipe_diameter_m=0.2,
        )

        # Both should complete successfully
        assert speciation_result["pH"] > 0
        assert corrosion_result["corrosion_rate_mm_y"] >= 0

    def test_cross_validation_norsok_vs_orr(self):
        """Cross-validate NORSOK CO₂ model vs ORR model (should differ)"""
        # NORSOK CO₂ corrosion
        norsok_result = predict_co2_h2s_corrosion(
            temperature_C=25.0,
            pressure_bar=10.0,
            co2_fraction=0.1,
            pH=5.0,
            pipe_diameter_m=0.2,
        )

        # ORR aerated corrosion
        orr_result = predict_aerated_chloride_corrosion(
            temperature_C=25.0,
            dissolved_oxygen_mg_L=8.0,
            chloride_mg_L=100.0,
            pH=7.0,
        )

        # Mechanisms should be different
        assert norsok_result["mechanism"] != orr_result["mechanism"]

        # Both should be physically reasonable (>0, <100 mm/y)
        assert 0 <= norsok_result["corrosion_rate_mm_y"] < 100
        assert 0 <= orr_result["corrosion_rate_mm_y"] < 100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
