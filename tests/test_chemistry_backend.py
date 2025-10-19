"""
Unit tests for PHREEQC Chemistry Backend

Tests unit conversions, charge balance validation, thread safety,
and PHREEQC integration for aqueous speciation and scaling prediction.

Target coverage: ≥85% (per Codex guidance)
"""

import pytest
import threading
import json
from pathlib import Path
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.chemistry_backend import (
    PHREEQCBackend,
    mg_L_to_mol_L,
    mol_L_to_mg_L,
    mg_L_to_meq_L,
    calculate_charge_balance,
    validate_water_chemistry,
    VALID_IONS,
    ION_TO_PHREEQC,
)


class TestUnitConversions:
    """Test unit conversion helper functions"""

    def test_mg_L_to_mol_L_sodium(self):
        """Test mg/L to mol/L conversion for sodium"""
        # 1000 mg/L Na+ with MW=22.99 g/mol
        # = 1000 / 22.99 / 1000 = 0.0435 mol/L
        result = mg_L_to_mol_L(1000.0, 22.99)
        assert abs(result - 0.0435) < 0.001

    def test_mol_L_to_mg_L_chloride(self):
        """Test mol/L to mg/L conversion for chloride"""
        # 0.01 mol/L Cl- with MW=35.45 g/mol
        # = 0.01 * 35.45 * 1000 = 354.5 mg/L
        result = mol_L_to_mg_L(0.01, 35.45)
        assert abs(result - 354.5) < 0.1

    def test_mg_L_to_meq_L_calcium(self):
        """Test mg/L to meq/L conversion for calcium"""
        # 100 mg/L Ca2+ with MW=40.08 g/mol, charge=2
        # = (100 / 40.08 / 1000) * 2 * 1000 = 4.99 meq/L
        result = mg_L_to_meq_L(100.0, 40.08, 2)
        assert abs(result - 4.99) < 0.01

    def test_roundtrip_conversion(self):
        """Test roundtrip mg/L → mol/L → mg/L"""
        original_mg_L = 500.0
        mw = 96.06  # Sulfate

        mol_L = mg_L_to_mol_L(original_mg_L, mw)
        final_mg_L = mol_L_to_mg_L(mol_L, mw)

        assert abs(final_mg_L - original_mg_L) < 1e-6


class TestChargeBalance:
    """Test charge balance calculations"""

    def test_charge_balance_perfect(self):
        """Test perfectly balanced water (Na+ Cl- solution)"""
        # 1000 mg/L NaCl
        # Na+: 1000 mg/L / 22.99 g/mol * 1 = 43.5 meq/L
        # Cl-: 1500 mg/L / 35.45 g/mol * 1 = 42.3 meq/L
        # Close to balanced
        ions = {
            "Na+": 1000.0,
            "Cl-": 1545.0,  # Adjusted for perfect balance
        }

        balance = calculate_charge_balance(ions)
        assert abs(balance) < 1.0  # Within ±1%

    def test_charge_balance_excess_cations(self):
        """Test water with excess cations"""
        ions = {
            "Na+": 2000.0,  # High sodium
            "Cl-": 1000.0,
        }

        balance = calculate_charge_balance(ions)
        assert balance > 10.0  # Significant excess cations

    def test_charge_balance_seawater(self):
        """Test charge balance for seawater composition"""
        ions = {
            "Na+": 10770.0,
            "Mg2+": 1290.0,
            "Ca2+": 412.0,
            "K+": 399.0,
            "Cl-": 19350.0,
            "SO4-2": 2712.0,
            "HCO3-": 142.0,
        }

        balance = calculate_charge_balance(ions)
        assert abs(balance) < 5.0  # Seawater should be well-balanced

    def test_validate_water_chemistry_pass(self):
        """Test validation passes for balanced water"""
        ions = {
            "Na+": 1000.0,
            "Cl-": 1545.0,
        }

        # Should not raise
        validate_water_chemistry(ions, max_imbalance=5.0)

    def test_validate_water_chemistry_fail(self):
        """Test validation fails for highly imbalanced water"""
        ions = {
            "Na+": 5000.0,  # Huge excess cations
            "Cl-": 500.0,
        }

        # Should raise ValueError
        with pytest.raises(ValueError, match="Charge imbalance"):
            validate_water_chemistry(ions, max_imbalance=5.0)


class TestIonMappings:
    """Test ion to PHREEQC keyword mappings"""

    def test_valid_ions_coverage(self):
        """Test that VALID_IONS dictionary is comprehensive"""
        # Check major ions are present
        required_ions = ["Na+", "Ca2+", "Mg2+", "Cl-", "SO4-2", "HCO3-"]

        for ion in required_ions:
            assert ion in VALID_IONS
            assert "charge" in VALID_IONS[ion]
            assert "mw" in VALID_IONS[ion]
            assert "name" in VALID_IONS[ion]

    def test_ion_to_phreeqc_mapping(self):
        """Test ION_TO_PHREEQC mapping is correct"""
        # Sodium: 1:1 mapping
        assert ION_TO_PHREEQC["Na+"] == ("Na", 1.0)

        # Sulfate: Convert to sulfur basis
        keyword, conversion = ION_TO_PHREEQC["SO4-2"]
        assert keyword == "S(6)"
        assert abs(conversion - (96.06 / 32.07)) < 0.01

        # Bicarbonate: Convert HCO3- to CaCO3 equivalents (per Codex fix BUG-006)
        keyword, conversion = ION_TO_PHREEQC["HCO3-"]
        assert keyword == "Alkalinity"
        assert abs(conversion - (61.02 / 50.0)) < 0.01  # HCO3- to CaCO3

    def test_ion_to_phreeqc_coverage(self):
        """Test that all major ions have PHREEQC mappings"""
        major_ions = ["Na+", "Ca2+", "Mg2+", "K+", "Cl-", "SO4-2", "HCO3-"]

        for ion in major_ions:
            assert ion in ION_TO_PHREEQC


class TestPHREEQCBackend:
    """Test PHREEQC backend integration"""

    def test_backend_initialization(self):
        """Test that backend initializes successfully"""
        backend = PHREEQCBackend()
        assert backend.database == "phreeqc.dat"

    def test_convert_to_phreeqc_solution(self):
        """Test ion dictionary conversion to PHREEQC format"""
        backend = PHREEQCBackend()

        ions = {
            "Na+": 1000.0,
            "Cl-": 1500.0,
            "Ca2+": 100.0,
            "HCO3-": 200.0,
        }

        phreeqc_sol = backend.convert_to_phreeqc_solution(ions)

        assert "Na" in phreeqc_sol
        assert phreeqc_sol["Na"] == 1000.0  # 1:1 conversion

        assert "Cl" in phreeqc_sol
        assert phreeqc_sol["Cl"] == 1500.0

        assert "Ca" in phreeqc_sol
        assert phreeqc_sol["Ca"] == 100.0

        assert "Alkalinity" in phreeqc_sol
        # HCO3- converted to CaCO3 equivalents (per Codex fix BUG-006)
        expected_alkalinity = 200.0 / (61.02 / 50.0)  # ~163.9
        assert abs(phreeqc_sol["Alkalinity"] - expected_alkalinity) < 1.0

    def test_run_speciation_simple(self):
        """Test basic speciation calculation"""
        backend = PHREEQCBackend()

        ions = {
            "Na+": 1000.0,
            "Cl-": 1545.0,  # Balanced
        }

        result = backend.run_speciation(ions, temperature_C=25.0)

        # Check basic properties
        assert 5.0 <= result.pH <= 9.0  # Reasonable pH range
        assert result.temperature_C == 25.0
        assert result.ionic_strength_M > 0.0
        assert result.charge_balance_percent < 5.0

    def test_run_speciation_with_pH(self):
        """Test speciation with specified pH"""
        backend = PHREEQCBackend()

        ions = {
            "Na+": 1000.0,
            "Cl-": 1545.0,
        }

        result = backend.run_speciation(ions, temperature_C=25.0, pH=7.5)

        # pH should be close to specified value
        assert abs(result.pH - 7.5) < 0.1

    def test_run_speciation_seawater(self):
        """Test speciation for seawater composition"""
        backend = PHREEQCBackend()

        ions = {
            "Na+": 10770.0,
            "Mg2+": 1290.0,
            "Ca2+": 412.0,
            "K+": 399.0,
            "Cl-": 19350.0,
            "SO4-2": 2712.0,
            "HCO3-": 142.0,
        }

        result = backend.run_speciation(ions, temperature_C=25.0)

        # Seawater pH can vary, but should be slightly acidic to neutral
        # (CO2 equilibration without atmosphere gives ~7.0-8.3)
        assert 6.5 <= result.pH <= 8.5

        # Ionic strength should be ~0.7 M
        assert 0.5 <= result.ionic_strength_M <= 0.9

        # Should have saturation indices
        assert "Calcite" in result.saturation_indices

    def test_run_speciation_hard_water(self):
        """Test speciation for hard water (high Ca, HCO3)"""
        backend = PHREEQCBackend()

        ions = {
            "Ca2+": 150.0,
            "Mg2+": 50.0,
            "HCO3-": 250.0,
            "SO4-2": 80.0,
            "Cl-": 50.0,
            "Na+": 100.0,
        }

        result = backend.run_speciation(ions, temperature_C=25.0)

        # Hard water should have positive SI for calcite
        si_calcite = result.saturation_indices.get("Calcite", -999)
        assert si_calcite > -1.0  # Should be close to saturation or supersaturated

    def test_calculate_langelier_index(self):
        """Test LSI calculation"""
        backend = PHREEQCBackend()

        ions = {
            "Ca2+": 120.0,
            "HCO3-": 250.0,
            "Cl-": 150.0,
            "Na+": 100.0,
        }

        lsi = backend.calculate_langelier_index(ions, temperature_C=25.0, pH=7.8)

        # LSI should be reasonable (-3 to +3)
        assert -3.0 <= lsi <= 3.0

    def test_predict_scaling_tendency(self):
        """Test scaling prediction with multiple indices"""
        backend = PHREEQCBackend()

        ions = {
            "Ca2+": 120.0,
            "Mg2+": 30.0,
            "HCO3-": 250.0,
            "Cl-": 150.0,
            "SO4-2": 80.0,
            "Na+": 100.0,
        }

        result, speciation = backend.predict_scaling_tendency(ions, temperature_C=25.0, pH=7.8)

        # Check all indices are present
        assert hasattr(result, "lsi")
        assert hasattr(result, "rsi")
        assert hasattr(result, "puckorius_index")
        assert hasattr(result, "larson_ratio")
        assert hasattr(result, "interpretation")

        # LSI and RSI should be inversely related
        # High LSI → Low RSI (scaling)
        # Low LSI → High RSI (corrosive)
        assert result.lsi + result.rsi > 0  # Basic sanity check

        # Larson ratio should be positive
        assert result.larson_ratio >= 0.0

    def test_thread_safety(self):
        """Test that multiple threads can use backend simultaneously"""
        backend = PHREEQCBackend()

        ions = {
            "Na+": 1000.0,
            "Cl-": 1545.0,
        }

        results = []

        def run_speciation_thread():
            result = backend.run_speciation(ions, temperature_C=25.0)
            results.append(result.pH)

        # Create 5 threads
        threads = [threading.Thread(target=run_speciation_thread) for _ in range(5)]

        # Start all threads
        for t in threads:
            t.start()

        # Wait for all threads to complete
        for t in threads:
            t.join()

        # All results should be similar (same inputs)
        assert len(results) == 5
        avg_pH = sum(results) / len(results)

        for pH in results:
            assert abs(pH - avg_pH) < 0.01  # All threads get same result


class TestSpeciationResult:
    """Test SpeciationResult dataclass"""

    def test_speciation_result_structure(self):
        """Test that speciation result has all required fields"""
        backend = PHREEQCBackend()

        ions = {
            "Na+": 1000.0,
            "Cl-": 1545.0,
        }

        result = backend.run_speciation(ions, temperature_C=25.0)

        # Check all required fields
        assert hasattr(result, "pH")
        assert hasattr(result, "pe")
        assert hasattr(result, "temperature_C")
        assert hasattr(result, "ionic_strength_M")
        assert hasattr(result, "alkalinity_mg_L_CaCO3")
        assert hasattr(result, "species")
        assert hasattr(result, "saturation_indices")
        assert hasattr(result, "charge_balance_percent")
        assert hasattr(result, "raw_solution")

        # Check types
        assert isinstance(result.pH, float)
        assert isinstance(result.species, dict)
        assert isinstance(result.saturation_indices, dict)


class TestScalingResult:
    """Test ScalingResult dataclass"""

    def test_scaling_result_structure(self):
        """Test that scaling result has all required fields"""
        backend = PHREEQCBackend()

        ions = {
            "Ca2+": 120.0,
            "HCO3-": 250.0,
            "Cl-": 150.0,
            "SO4-2": 80.0,
            "Na+": 100.0,
        }

        result, speciation = backend.predict_scaling_tendency(ions, temperature_C=25.0, pH=7.8)

        # Check all required fields
        assert hasattr(result, "lsi")
        assert hasattr(result, "rsi")
        assert hasattr(result, "puckorius_index")
        assert hasattr(result, "larson_ratio")
        assert hasattr(result, "interpretation")

        # Check types
        assert isinstance(result.lsi, float)
        assert isinstance(result.rsi, float)
        assert isinstance(result.interpretation, str)


class TestEdgeCases:
    """Test edge cases and error handling"""

    def test_empty_ions_dict(self):
        """Test behavior with empty ions dictionary"""
        # Empty dict has no ions to validate, so charge balance is 0/0 = 0%
        # This is technically "balanced" but meaningless
        # The function should handle this gracefully
        balance = calculate_charge_balance({})
        assert balance == 0.0  # No ions = no imbalance

    def test_unknown_ion(self):
        """Test handling of unknown ion in charge balance"""
        ions = {
            "Na+": 1000.0,
            "Cl-": 1545.0,
            "UnknownIon": 100.0,  # Not in VALID_IONS
        }

        # Should not crash, but log warning
        balance = calculate_charge_balance(ions)
        assert isinstance(balance, float)

    def test_very_dilute_solution(self):
        """Test speciation for very dilute solution"""
        backend = PHREEQCBackend()

        ions = {
            "Na+": 10.0,  # Very dilute
            "Cl-": 15.45,
        }

        result = backend.run_speciation(ions, temperature_C=25.0)

        # Should have very low ionic strength
        assert result.ionic_strength_M < 0.001

    def test_very_concentrated_solution(self):
        """Test speciation for highly concentrated solution"""
        backend = PHREEQCBackend()

        ions = {
            "Na+": 50000.0,  # Highly concentrated
            "Cl-": 77250.0,
        }

        result = backend.run_speciation(ions, temperature_C=25.0)

        # Should have high ionic strength
        assert result.ionic_strength_M > 1.0

    def test_temperature_effects(self):
        """Test that temperature affects speciation"""
        backend = PHREEQCBackend()

        ions = {
            "Ca2+": 120.0,
            "HCO3-": 250.0,
            "Cl-": 150.0,
            "Na+": 100.0,
        }

        result_25C = backend.run_speciation(ions, temperature_C=25.0, pH=7.5)
        result_60C = backend.run_speciation(ions, temperature_C=60.0, pH=7.5)

        # Higher temperature should change saturation indices
        si_calcite_25C = result_25C.saturation_indices.get("Calcite", 0.0)
        si_calcite_60C = result_60C.saturation_indices.get("Calcite", 0.0)

        # Calcite solubility decreases with temperature (retrograde solubility)
        # So SI should increase at higher temperature
        assert si_calcite_60C > si_calcite_25C


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
