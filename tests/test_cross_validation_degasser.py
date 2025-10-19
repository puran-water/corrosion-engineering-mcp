"""
Cross-validation with degasser-design-mcp PHREEQC implementation.

Tests that our PHREEQC backend produces consistent results with the
degasser-design-mcp water chemistry module.

Test cases:
- Municipal water composition
- Brackish water composition
- Seawater composition
- pH calculations
- Ionic strength calculations
"""

import pytest
import json
from pathlib import Path
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.chemistry_backend import PHREEQCBackend
from tools.chemistry.run_speciation import run_phreeqc_speciation


class TestDegasserCrossValidation:
    """Cross-validation tests with degasser-design-mcp water chemistry"""

    def test_municipal_water_comparison(self):
        """
        Test municipal water composition matches degasser-design-mcp defaults.

        From degasser-design-mcp/utils/water_chemistry.py:
        "municipal": {
            "Na+": 50.0,
            "Ca2+": 40.0,
            "Mg2+": 10.0,
            "K+": 5.0,
            "Cl-": 60.0,
            "SO4-2": 30.0,
            "HCO3-": 120.0,
            "NO3-": 10.0,
        }
        """
        ions_json = json.dumps({
            "Na+": 50.0,
            "Ca2+": 40.0,
            "Mg2+": 10.0,
            "K+": 5.0,
            "Cl-": 60.0,
            "SO4-2": 30.0,
            "HCO3-": 120.0,
            "NO3-": 10.0,
        })

        result = run_phreeqc_speciation(ions_json, temperature_C=25.0)

        # Municipal water should have near-neutral pH
        assert 7.0 <= result["pH"] <= 8.5

        # Low ionic strength
        assert result["ionic_strength_M"] < 0.01

        # Charge balance should be reasonable (degasser template has ~7% imbalance)
        assert abs(result["charge_balance_percent"]) < 10.0

    def test_brackish_water_comparison(self):
        """
        Test brackish water composition matches degasser-design-mcp defaults.

        From degasser-design-mcp/utils/water_chemistry.py:
        "brackish": {
            "Na+": 1000.0,
            "Ca2+": 100.0,
            "Mg2+": 50.0,
            "K+": 20.0,
            "Cl-": 1500.0,
            "SO4-2": 200.0,
            "HCO3-": 200.0,
        }
        """
        ions_json = json.dumps({
            "Na+": 1000.0,
            "Ca2+": 100.0,
            "Mg2+": 50.0,
            "K+": 20.0,
            "Cl-": 1500.0,
            "SO4-2": 200.0,
            "HCO3-": 200.0,
        })

        result = run_phreeqc_speciation(ions_json, temperature_C=25.0)

        # Brackish water should have near-neutral pH
        assert 6.5 <= result["pH"] <= 8.0

        # Moderate ionic strength
        assert 0.01 < result["ionic_strength_M"] < 0.1

        # Charge balance should be good
        assert abs(result["charge_balance_percent"]) < 5.0

    def test_seawater_comparison(self):
        """
        Test seawater composition matches degasser-design-mcp defaults.

        From degasser-design-mcp/utils/water_chemistry.py:
        "seawater": {
            "Na+": 10770.0,
            "Mg2+": 1290.0,
            "Ca2+": 412.0,
            "K+": 399.0,
            "Sr2+": 7.9,
            "Cl-": 19350.0,
            "SO4-2": 2712.0,
            "HCO3-": 142.0,
            "Br-": 67.0,
            "B(OH)4-": 4.5,
            "F-": 1.3,
        }
        """
        ions_json = json.dumps({
            "Na+": 10770.0,
            "Mg2+": 1290.0,
            "Ca2+": 412.0,
            "K+": 399.0,
            "Sr2+": 7.9,
            "Cl-": 19350.0,
            "SO4-2": 2712.0,
            "HCO3-": 142.0,
            "Br-": 67.0,
            "B(OH)4-": 4.5,
            "F-": 1.3,
        })

        result = run_phreeqc_speciation(ions_json, temperature_C=25.0)

        # Seawater pH can vary
        assert 6.5 <= result["pH"] <= 8.5

        # High ionic strength (~0.7 M)
        assert 0.5 <= result["ionic_strength_M"] <= 0.9

        # Charge balance should be excellent
        assert abs(result["charge_balance_percent"]) < 2.0

    def test_ion_mapping_consistency(self):
        """Test that ion mappings match degasser-design-mcp"""
        backend = PHREEQCBackend()

        # Test a few key mappings from degasser ION_MAPPING
        ions = {
            "Na+": 1000.0,
            "Ca2+": 100.0,
            "Cl-": 1500.0,
            "SO4-2": 200.0,
            "HCO3-": 200.0,
        }

        phreeqc_solution = backend.convert_to_phreeqc_solution(ions)

        # Check mappings match degasser-design-mcp
        assert phreeqc_solution["Na"] == 1000.0  # ("Na", 1.0)
        assert phreeqc_solution["Ca"] == 100.0  # ("Ca", 1.0)
        assert phreeqc_solution["Cl"] == 1500.0  # ("Cl", 1.0)

        # Sulfate: Convert SO4-2 to S(6)
        # degasser: ("S(6)", 96.06 / 32.07)
        expected_s6 = 200.0 / (96.06 / 32.07)
        assert abs(phreeqc_solution["S(6)"] - expected_s6) < 0.1

        # Bicarbonate: Alkalinity
        # NOTE: degasser-design-mcp uses ("Alkalinity", 1.0) which is incorrect
        # per Codex review (BUG-006). We fixed it to convert HCO3- → CaCO3 equivalents.
        # Correct conversion: 200.0 / (61.02 / 50.0) = 163.9 mg/L as CaCO3
        expected_alkalinity = 200.0 / (61.02 / 50.0)
        assert abs(phreeqc_solution["Alkalinity"] - expected_alkalinity) < 1.0

    def test_charge_balance_calculation(self):
        """Test charge balance calculation matches degasser-design-mcp logic"""
        from core.chemistry_backend import calculate_charge_balance

        # Test with balanced NaCl solution
        # Na+: 1000 mg/L / 22.99 g/mol * 1 = 43.5 meq/L
        # Cl-: 1545 mg/L / 35.45 g/mol * 1 = 43.6 meq/L
        # Balance should be near zero
        ions = {
            "Na+": 1000.0,
            "Cl-": 1545.0,
        }

        balance = calculate_charge_balance(ions)
        assert abs(balance) < 1.0

        # Test with imbalanced solution
        ions_imbalanced = {
            "Na+": 2000.0,  # Excess cations
            "Cl-": 1000.0,
        }

        balance_imbalanced = calculate_charge_balance(ions_imbalanced)
        assert balance_imbalanced > 10.0  # Should be significantly positive


class TestPHREEQCConsistency:
    """Test that PHREEQC produces consistent results"""

    def test_repeated_calls_consistency(self):
        """Test that repeated calls produce identical results"""
        ions_json = json.dumps({
            "Na+": 1000.0,
            "Ca2+": 100.0,
            "Cl-": 1500.0,
            "HCO3-": 200.0,
        })

        # Run speciation 3 times
        result1 = run_phreeqc_speciation(ions_json, temperature_C=25.0)
        result2 = run_phreeqc_speciation(ions_json, temperature_C=25.0)
        result3 = run_phreeqc_speciation(ions_json, temperature_C=25.0)

        # pH should be identical
        assert abs(result1["pH"] - result2["pH"]) < 1e-6
        assert abs(result2["pH"] - result3["pH"]) < 1e-6

        # Ionic strength should be identical
        assert abs(result1["ionic_strength_M"] - result2["ionic_strength_M"]) < 1e-9
        assert abs(result2["ionic_strength_M"] - result3["ionic_strength_M"]) < 1e-9

    def test_temperature_sensitivity(self):
        """Test that temperature affects results appropriately"""
        ions_json = json.dumps({
            "Ca2+": 120.0,
            "HCO3-": 250.0,
            "Cl-": 150.0,
            "Na+": 100.0,
        })

        result_25C = run_phreeqc_speciation(ions_json, temperature_C=25.0)
        result_60C = run_phreeqc_speciation(ions_json, temperature_C=60.0)

        # Temperature should affect saturation indices
        # (Retrograde solubility of CaCO3)
        si_25C = result_25C["saturation_indices"].get("Calcite", -999)
        si_60C = result_60C["saturation_indices"].get("Calcite", -999)

        # Higher temperature should increase SI for calcite
        assert si_60C > si_25C


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
