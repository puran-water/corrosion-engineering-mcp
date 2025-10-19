"""
Unit tests for Tier 1 Chemistry MCP Tools

Tests the three chemistry tools:
- run_phreeqc_speciation
- predict_scaling_tendency
- calculate_langelier_index

Target coverage: ≥85%
"""

import pytest
import json
from pathlib import Path
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.chemistry.run_speciation import run_phreeqc_speciation
from tools.chemistry.predict_scaling import predict_scaling_tendency
from tools.chemistry.langelier_index import calculate_langelier_index


class TestRunPhreeqcSpeciation:
    """Test run_phreeqc_speciation MCP tool"""

    def test_basic_speciation(self):
        """Test basic speciation with simple NaCl solution"""
        ions_json = json.dumps({
            "Na+": 1000.0,
            "Cl-": 1545.0,
        })

        result = run_phreeqc_speciation(ions_json, temperature_C=25.0)

        # Check all required fields
        assert "pH" in result
        assert "pe" in result
        assert "temperature_C" in result
        assert "ionic_strength_M" in result
        assert "alkalinity_mg_L_CaCO3" in result
        assert "species" in result
        assert "saturation_indices" in result
        assert "charge_balance_percent" in result
        assert "interpretation" in result

        # Check value ranges
        assert 5.0 <= result["pH"] <= 9.0
        assert result["temperature_C"] == 25.0
        assert result["ionic_strength_M"] > 0.0

    def test_speciation_with_pH(self):
        """Test speciation with specified pH"""
        ions_json = json.dumps({
            "Na+": 1000.0,
            "Cl-": 1545.0,
        })

        result = run_phreeqc_speciation(ions_json, temperature_C=25.0, pH=7.5)

        # pH should be close to specified value
        assert abs(result["pH"] - 7.5) < 0.2

    def test_speciation_seawater(self):
        """Test speciation for seawater composition"""
        ions_json = json.dumps({
            "Na+": 10770.0,
            "Mg2+": 1290.0,
            "Ca2+": 412.0,
            "K+": 399.0,
            "Cl-": 19350.0,
            "SO4-2": 2712.0,
            "HCO3-": 142.0,
        })

        result = run_phreeqc_speciation(ions_json, temperature_C=25.0)

        # Seawater pH can vary (~7.0-8.3 without atmosphere equilibration)
        assert 6.5 <= result["pH"] <= 8.5

        # High ionic strength
        assert result["ionic_strength_M"] > 0.5

        # Should have interpretation
        assert len(result["interpretation"]) > 0
        assert "saline" in result["interpretation"].lower()

    def test_invalid_json(self):
        """Test error handling for invalid JSON"""
        with pytest.raises(ValueError, match="Invalid JSON"):
            run_phreeqc_speciation("not valid json", temperature_C=25.0)

    def test_non_dict_json(self):
        """Test error handling for non-dict JSON"""
        with pytest.raises(ValueError, match="must be a JSON object"):
            run_phreeqc_speciation("[1, 2, 3]", temperature_C=25.0)

    def test_charge_balance_validation(self):
        """Test charge balance validation catches imbalanced water"""
        ions_json = json.dumps({
            "Na+": 10000.0,  # Huge excess cations
            "Cl-": 100.0,
        })

        # Should still run but log warning
        result = run_phreeqc_speciation(
            ions_json,
            temperature_C=25.0,
            validate_charge_balance=True,
            max_imbalance=5.0
        )

        # Check charge balance is reported
        assert abs(result["charge_balance_percent"]) > 10.0

    def test_interpretation_acidic(self):
        """Test interpretation for acidic water"""
        ions_json = json.dumps({
            "Na+": 100.0,
            "Cl-": 154.5,
        })

        result = run_phreeqc_speciation(ions_json, temperature_C=25.0, pH=4.0)

        # Should indicate corrosive
        assert "acidic" in result["interpretation"].lower() or "corrosive" in result["interpretation"].lower()

    def test_interpretation_scaling(self):
        """Test interpretation for scaling water"""
        ions_json = json.dumps({
            "Ca2+": 200.0,
            "Mg2+": 80.0,
            "HCO3-": 400.0,
            "Cl-": 100.0,
            "Na+": 50.0,
        })

        result = run_phreeqc_speciation(ions_json, temperature_C=25.0, pH=8.5)

        # Should indicate scaling risk
        assert "scaling" in result["interpretation"].lower() or "alkaline" in result["interpretation"].lower()


class TestPredictScalingTendency:
    """Test predict_scaling_tendency MCP tool"""

    def test_basic_scaling_prediction(self):
        """Test basic scaling prediction"""
        ions_json = json.dumps({
            "Ca2+": 120.0,
            "Mg2+": 30.0,
            "HCO3-": 250.0,
            "Cl-": 150.0,
            "SO4-2": 80.0,
            "Na+": 100.0,
        })

        result = predict_scaling_tendency(ions_json, temperature_C=25.0, pH=7.8)

        # Check all required fields
        assert "lsi" in result
        assert "rsi" in result
        assert "puckorius_index" in result
        assert "larson_ratio" in result
        assert "pH" in result
        assert "pH_saturation" in result
        assert "ionic_strength_M" in result
        assert "interpretation" in result
        assert "recommendations" in result

        # Check value ranges
        assert -3.0 <= result["lsi"] <= 3.0
        assert 4.0 <= result["rsi"] <= 10.0
        assert result["larson_ratio"] >= 0.0

    def test_scaling_water(self):
        """Test prediction for scaling water (high LSI)"""
        ions_json = json.dumps({
            "Ca2+": 200.0,
            "Mg2+": 80.0,
            "HCO3-": 400.0,
            "Cl-": 100.0,
            "Na+": 50.0,
        })

        result = predict_scaling_tendency(ions_json, temperature_C=25.0, pH=8.5)

        # Should have positive LSI
        assert result["lsi"] > 0.0

        # Should have recommendations
        assert len(result["recommendations"]) > 0

        # Should mention scaling in interpretation
        assert "scaling" in result["interpretation"].lower()

    def test_corrosive_water(self):
        """Test prediction for corrosive water (negative LSI)"""
        ions_json = json.dumps({
            "Ca2+": 20.0,
            "HCO3-": 30.0,
            "Cl-": 200.0,
            "SO4-2": 150.0,
            "Na+": 100.0,
        })

        result = predict_scaling_tendency(ions_json, temperature_C=25.0, pH=6.5)

        # Should have negative LSI
        assert result["lsi"] < 0.0

        # Should have high RSI
        assert result["rsi"] > 7.0

        # Should have recommendations
        assert any("corrosion" in rec.lower() for rec in result["recommendations"])

    def test_high_larson_ratio(self):
        """Test prediction for water with high Larson ratio"""
        ions_json = json.dumps({
            "Cl-": 500.0,  # High chloride
            "SO4-2": 300.0,  # High sulfate
            "HCO3-": 50.0,  # Low bicarbonate
            "Na+": 400.0,
            "Ca2+": 50.0,
        })

        result = predict_scaling_tendency(ions_json, temperature_C=25.0, pH=7.0)

        # Larson ratio should be high
        assert result["larson_ratio"] > 1.0

        # Should have corrosivity warning
        assert any("larson" in rec.lower() for rec in result["recommendations"])

    def test_balanced_water(self):
        """Test prediction for near-equilibrium water"""
        ions_json = json.dumps({
            "Ca2+": 80.0,
            "Mg2+": 20.0,
            "HCO3-": 180.0,
            "Cl-": 100.0,
            "SO4-2": 60.0,
            "Na+": 80.0,
        })

        result = predict_scaling_tendency(ions_json, temperature_C=25.0, pH=7.5)

        # LSI should be near zero
        assert -0.5 <= result["lsi"] <= 0.5

        # Interpretation should mention equilibrium
        assert "equilibrium" in result["interpretation"].lower()


class TestCalculateLangelierIndex:
    """Test calculate_langelier_index MCP tool"""

    def test_basic_lsi_calculation(self):
        """Test basic LSI calculation"""
        ions_json = json.dumps({
            "Ca2+": 120.0,
            "HCO3-": 250.0,
            "Cl-": 150.0,
            "Na+": 100.0,
        })

        result = calculate_langelier_index(ions_json, temperature_C=25.0, pH=7.8)

        # Check all required fields
        assert "lsi" in result
        assert "pH" in result
        assert "pH_saturation" in result
        assert "si_calcite" in result
        assert "temperature_C" in result
        assert "interpretation" in result
        assert "action_required" in result

        # Check value ranges
        assert -3.0 <= result["lsi"] <= 3.0
        assert result["temperature_C"] == 25.0

    def test_lsi_positive_scaling(self):
        """Test LSI for scaling water"""
        ions_json = json.dumps({
            "Ca2+": 200.0,
            "HCO3-": 400.0,
            "Cl-": 100.0,
            "Na+": 50.0,
        })

        result = calculate_langelier_index(ions_json, temperature_C=25.0, pH=8.5)

        # Should have positive LSI
        assert result["lsi"] > 0.0

        # Interpretation should mention scaling
        assert "scaling" in result["interpretation"].lower()

        # Action should mention treatment
        assert "action" in result["action_required"].lower() or "monitor" in result["action_required"].lower()

    def test_lsi_negative_corrosive(self):
        """Test LSI for corrosive water"""
        ions_json = json.dumps({
            "Ca2+": 30.0,
            "HCO3-": 50.0,
            "Cl-": 200.0,
            "Na+": 100.0,
        })

        result = calculate_langelier_index(ions_json, temperature_C=25.0, pH=6.5)

        # Should have negative LSI
        assert result["lsi"] < 0.0

        # Interpretation should mention corrosive
        assert "corrosive" in result["interpretation"].lower()

    def test_lsi_temperature_effects(self):
        """Test that temperature affects LSI"""
        ions_json = json.dumps({
            "Ca2+": 120.0,
            "HCO3-": 250.0,
            "Cl-": 150.0,
            "Na+": 100.0,
        })

        result_25C = calculate_langelier_index(ions_json, temperature_C=25.0, pH=7.8)
        result_40C = calculate_langelier_index(ions_json, temperature_C=40.0, pH=7.8)

        # Higher temperature should increase scaling tendency
        # (retrograde solubility of CaCO3)
        assert result_40C["lsi"] > result_25C["lsi"]

        # Should have note about elevated temperature
        if "note" in result_40C:
            assert "temperature" in result_40C["note"].lower()

    def test_lsi_missing_calcium(self):
        """Test error handling when calcium is missing"""
        ions_json = json.dumps({
            "Na+": 1000.0,
            "Cl-": 1545.0,
        })

        with pytest.raises(ValueError, match="calcium"):
            calculate_langelier_index(ions_json, temperature_C=25.0)

    def test_lsi_missing_bicarbonate(self):
        """Test error handling when bicarbonate is missing"""
        ions_json = json.dumps({
            "Ca2+": 120.0,
            "Cl-": 200.0,
            "Na+": 100.0,
        })

        with pytest.raises(ValueError, match="bicarbonate|carbonate"):
            calculate_langelier_index(ions_json, temperature_C=25.0)

    def test_lsi_severe_scaling(self):
        """Test interpretation for severe scaling (LSI > 2.0)"""
        ions_json = json.dumps({
            "Ca2+": 300.0,
            "Mg2+": 100.0,
            "HCO3-": 500.0,
            "Cl-": 100.0,
            "Na+": 50.0,
        })

        result = calculate_langelier_index(ions_json, temperature_C=25.0, pH=9.0)

        # Should have very positive LSI
        assert result["lsi"] > 1.0

        # Interpretation should mention severe scaling
        assert "severe" in result["interpretation"].lower() or "moderate" in result["interpretation"].lower()

        # Action should be immediate
        assert "immediate" in result["action_required"].lower() or "action" in result["action_required"].lower()


class TestCrossValidation:
    """Cross-validation tests comparing results between tools"""

    def test_lsi_consistency(self):
        """Test that LSI is consistent between predict_scaling and calculate_langelier"""
        ions_json = json.dumps({
            "Ca2+": 120.0,
            "Mg2+": 30.0,
            "HCO3-": 250.0,
            "Cl-": 150.0,
            "SO4-2": 80.0,
            "Na+": 100.0,
        })

        lsi_result = calculate_langelier_index(ions_json, temperature_C=25.0, pH=7.8)
        scaling_result = predict_scaling_tendency(ions_json, temperature_C=25.0, pH=7.8)

        # LSI should be identical
        assert abs(lsi_result["lsi"] - scaling_result["lsi"]) < 0.01

        # pH should be identical
        assert abs(lsi_result["pH"] - scaling_result["pH"]) < 0.01

    def test_speciation_ph_consistency(self):
        """Test that pH is consistent when specified vs calculated"""
        ions_json = json.dumps({
            "Na+": 1000.0,
            "Cl-": 1545.0,
        })

        # First run: let PHREEQC calculate pH
        result1 = run_phreeqc_speciation(ions_json, temperature_C=25.0)

        # Second run: specify the calculated pH
        result2 = run_phreeqc_speciation(ions_json, temperature_C=25.0, pH=result1["pH"])

        # pH should be very close
        assert abs(result1["pH"] - result2["pH"]) < 0.05


class TestRealWorldScenarios:
    """Test with real-world water compositions"""

    def test_cooling_tower_water(self):
        """Test typical cooling tower water"""
        ions_json = json.dumps({
            "Ca2+": 150.0,
            "Mg2+": 50.0,
            "Na+": 120.0,
            "HCO3-": 280.0,
            "SO4-2": 120.0,
            "Cl-": 180.0,
        })

        # Run all three tools
        speciation = run_phreeqc_speciation(ions_json, temperature_C=35.0)
        scaling = predict_scaling_tendency(ions_json, temperature_C=35.0)
        lsi = calculate_langelier_index(ions_json, temperature_C=35.0)

        # All should complete successfully
        assert speciation["pH"] > 0
        assert "lsi" in scaling
        assert "lsi" in lsi

    def test_boiler_feedwater(self):
        """Test typical boiler feedwater (low hardness)"""
        ions_json = json.dumps({
            "Na+": 50.0,
            "Ca2+": 2.0,  # Very low hardness
            "Mg2+": 0.5,
            "HCO3-": 30.0,
            "Cl-": 25.0,
            "SO4-2": 10.0,
        })

        result = run_phreeqc_speciation(ions_json, temperature_C=25.0)

        # Should have low ionic strength
        assert result["ionic_strength_M"] < 0.01

        # LSI should be negative (corrosive to CaCO3)
        lsi_result = calculate_langelier_index(ions_json, temperature_C=25.0)
        assert lsi_result["lsi"] < 0.0

    def test_brackish_groundwater(self):
        """Test brackish groundwater composition"""
        ions_json = json.dumps({
            "Na+": 800.0,
            "Ca2+": 150.0,
            "Mg2+": 80.0,
            "K+": 20.0,
            "HCO3-": 350.0,
            "SO4-2": 250.0,
            "Cl-": 1200.0,
        })

        result = run_phreeqc_speciation(ions_json, temperature_C=25.0)

        # Should have moderate ionic strength
        assert 0.01 < result["ionic_strength_M"] < 0.1

        # Should complete without errors
        assert "interpretation" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
