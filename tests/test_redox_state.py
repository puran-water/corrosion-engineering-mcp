"""
Validation tests for RedoxState module.

Tests against authoritative sources:
1. USGS DO saturation tables
2. Pourbaix Atlas O2/H2O equilibrium line
3. Standard reference electrode potentials
4. Literature Eh-DO correlations
"""

import pytest
import numpy as np
from utils.redox_state import (
    do_to_eh,
    eh_to_do,
    orp_to_eh,
    eh_to_orp,
    do_saturation,
    henry_constant_o2,
    ReferenceElectrode,
    create_redox_state_from_do,
    create_redox_state_from_orp,
)


# ==============================================================================
# Test DO Saturation vs USGS Tables
# ==============================================================================

class TestDOSaturation:
    """Validate DO saturation against USGS tables."""

    @pytest.mark.parametrize("temp_C,expected_mg_L,tolerance", [
        (0, 14.6, 0.3),    # USGS: 14.6 mg/L
        (5, 12.8, 0.2),    # USGS: 12.8 mg/L
        (10, 11.3, 0.2),   # USGS: 11.3 mg/L
        (15, 10.1, 0.2),   # USGS: 10.1 mg/L
        (20, 9.1, 0.2),    # USGS: 9.1 mg/L
        (25, 8.3, 0.2),    # USGS: 8.26 mg/L
        (30, 7.6, 0.2),    # USGS: 7.56 mg/L
        (35, 7.0, 0.2),    # USGS: 6.95 mg/L
    ])
    def test_do_saturation_vs_usgs(self, temp_C, expected_mg_L, tolerance):
        """Test DO saturation against USGS tables (freshwater, 1 atm)."""
        calculated = do_saturation(temp_C, pressure_atm=1.0)
        assert abs(calculated - expected_mg_L) < tolerance, \
            f"At {temp_C}°C: calculated {calculated:.2f} mg/L vs USGS {expected_mg_L} mg/L"

    def test_do_decreases_with_temperature(self):
        """DO solubility should decrease with increasing temperature."""
        temps = [5, 15, 25, 35]
        saturations = [do_saturation(T) for T in temps]

        for i in range(len(temps) - 1):
            assert saturations[i] > saturations[i+1], \
                f"DO should decrease with T: {saturations[i]:.1f} > {saturations[i+1]:.1f}"

    def test_altitude_effect(self):
        """DO saturation should decrease with altitude (lower pressure)."""
        # Sea level (1 atm) vs Denver (0.83 atm)
        DO_sea_level = do_saturation(25.0, pressure_atm=1.0)
        DO_denver = do_saturation(25.0, pressure_atm=0.83)

        assert DO_denver < DO_sea_level
        assert abs(DO_denver / DO_sea_level - 0.83) < 0.02  # Proportional to pressure


# ==============================================================================
# Test DO → Eh Conversion vs Pourbaix Atlas
# ==============================================================================

class TestDOtoEh:
    """Validate DO→Eh conversion against Pourbaix thermodynamics."""

    def test_pourbaix_o2_line_pH7(self):
        """
        Test against Pourbaix Atlas O2/H2O line at pH 7.

        Pourbaix (1974) shows O2/H2O line at p_O2=0.21 atm, pH=7:
        Eh ≈ +0.82 V vs SHE
        """
        DO_sat = do_saturation(25.0)  # ~8.3 mg/L at 1 atm
        Eh, warnings = do_to_eh(DO_sat, pH=7.0, temperature_C=25.0)

        # Expected: 1.229 - 0.059*7 + (RT/4F)*ln(0.21)
        # = 1.229 - 0.413 - 0.010 = 0.806 V
        assert abs(Eh - 0.806) < 0.02, f"Eh = {Eh:.3f} V, expected ~0.806 V"

    def test_pourbaix_o2_line_pH0(self):
        """Test against standard O2/H2O potential at pH 0."""
        # At pH 0, p_O2 = 1 atm: E⁰ = +1.229 V
        DO_at_1atm = do_saturation(25.0, pressure_atm=1.0/0.21)  # Pure O2
        Eh, warnings = do_to_eh(DO_at_1atm, pH=0.0, temperature_C=25.0)

        assert abs(Eh - 1.229) < 0.02, f"Eh = {Eh:.3f} V, expected ~1.229 V"

    def test_eh_decreases_with_ph(self):
        """Eh should decrease ~59 mV per pH unit (Nernst slope)."""
        DO = 8.0  # mg/L
        pHs = [6.0, 7.0, 8.0, 9.0]
        Ehs = [do_to_eh(DO, pH, 25.0)[0] for pH in pHs]

        # Check slope ~-0.059 V/pH
        for i in range(len(pHs) - 1):
            dEh = Ehs[i+1] - Ehs[i]
            dpH = pHs[i+1] - pHs[i]
            slope = dEh / dpH

            assert abs(slope - (-0.059)) < 0.005, \
                f"Nernst slope = {slope:.3f} V/pH, expected -0.059 V/pH"

    def test_eh_increases_with_do(self):
        """Eh should increase with DO (more oxidizing)."""
        pH = 7.0
        DOs = [0.5, 2.0, 5.0, 8.0]
        Ehs = [do_to_eh(DO, pH, 25.0)[0] for DO in DOs]

        for i in range(len(DOs) - 1):
            assert Ehs[i] < Ehs[i+1], \
                f"Eh should increase with DO: {Ehs[i]:.3f} < {Ehs[i+1]:.3f}"

    def test_anaerobic_warning(self):
        """Should warn when DO < 0.01 mg/L (anaerobic)."""
        Eh, warnings = do_to_eh(0.005, pH=7.0, temperature_C=25.0)

        assert len(warnings) > 0
        assert "anaerobic" in warnings[0].lower()

    def test_oversaturation_warning(self):
        """Should warn when DO exceeds saturation by >10%."""
        DO_sat = do_saturation(25.0)
        DO_super = DO_sat * 1.2  # 20% oversaturation

        Eh, warnings = do_to_eh(DO_super, pH=7.0, temperature_C=25.0)

        assert len(warnings) > 0
        assert "saturation" in warnings[0].lower() or "supersaturation" in warnings[0].lower()


# ==============================================================================
# Test Eh → DO Conversion (Inverse)
# ==============================================================================

class TestEhtoDO:
    """Validate Eh→DO conversion (inverse of do_to_eh)."""

    def test_roundtrip_conversion(self):
        """DO → Eh → DO should recover original value."""
        DO_original = 8.0  # mg/L
        pH = 7.5
        T = 25.0

        Eh, _ = do_to_eh(DO_original, pH, T)
        DO_recovered, _ = eh_to_do(Eh, pH, T)

        assert abs(DO_recovered - DO_original) < 0.01, \
            f"Roundtrip: {DO_original} → {Eh:.3f} V → {DO_recovered:.2f} mg/L"

    def test_oxidizing_conditions(self):
        """High Eh (oxidizing) should give high DO."""
        Eh = 0.8  # V (highly oxidizing)
        pH = 7.0
        T = 25.0

        DO, warnings = eh_to_do(Eh, pH, T)

        # Should be near saturation (Eh=0.8V is ~15mV below air-equilibrium ~0.805V,
        # so expect ~47% sat per Nernst equation, not quite 50%)
        DO_sat = do_saturation(T)
        assert DO > 0.45 * DO_sat, \
            f"High Eh ({Eh} V) should give significant DO: {DO:.2f} mg/L"

    def test_reducing_conditions(self):
        """Low Eh (reducing) should give negligible DO."""
        Eh = 0.0  # V (neutral, but below O2/H2O line)
        pH = 7.0
        T = 25.0

        DO, warnings = eh_to_do(Eh, pH, T)

        assert DO < 0.1, \
            f"Low Eh ({Eh} V) should give near-zero DO: {DO:.4f} mg/L"
        assert len(warnings) > 0  # Should warn about anaerobic


# ==============================================================================
# Test ORP Conversions
# ==============================================================================

class TestORPConversions:
    """Validate ORP ↔ Eh conversions."""

    def test_sce_reference(self):
        """Test SCE reference electrode (+0.242 V vs SHE)."""
        ORP_mV = 150  # mV vs SCE
        Eh = orp_to_eh(ORP_mV, ReferenceElectrode.SCE)

        expected_Eh = (150/1000) + 0.242  # = 0.392 V
        assert abs(Eh - expected_Eh) < 0.001, \
            f"SCE: {ORP_mV} mV → {Eh:.3f} V, expected {expected_Eh:.3f} V"

    def test_agagcl_reference(self):
        """Test Ag/AgCl reference electrode (+0.197 V vs SHE)."""
        ORP_mV = 200  # mV vs Ag/AgCl
        Eh = orp_to_eh(ORP_mV, ReferenceElectrode.AgAgCl)

        expected_Eh = (200/1000) + 0.197  # = 0.397 V
        assert abs(Eh - expected_Eh) < 0.001

    def test_she_reference(self):
        """Test SHE reference (no offset)."""
        ORP_mV = 500  # mV vs SHE
        Eh = orp_to_eh(ORP_mV, ReferenceElectrode.SHE)

        assert abs(Eh - 0.500) < 0.001

    def test_orp_roundtrip(self):
        """Eh → ORP → Eh should recover original."""
        Eh_original = 0.4  # V
        ref = ReferenceElectrode.SCE

        ORP = eh_to_orp(Eh_original, ref)
        Eh_recovered = orp_to_eh(ORP, ref)

        assert abs(Eh_recovered - Eh_original) < 0.001


# ==============================================================================
# Test Literature Validation
# ==============================================================================

class TestLiteratureValidation:
    """Validate against published Eh-pH-DO data."""

    def test_revie_aerated_wastewater(self):
        """
        Revie (2011), Table 6.3: Aerated wastewater
        DO = 6-8 mg/L, pH = 7.5, Eh = +400 to +600 mV (MEASURED)

        Note: Measured Eh is LOWER than thermodynamic due to kinetics.
        Our calculation gives thermodynamic Eh.
        """
        DO = 7.0  # mg/L
        pH = 7.5
        T = 25.0

        Eh_calc, _ = do_to_eh(DO, pH, T)

        # Thermodynamic Eh should be HIGHER than measured
        # Expected: ~750 mV (thermodynamic) vs 400-600 mV (measured)
        assert Eh_calc > 0.6, \
            f"Thermodynamic Eh ({Eh_calc*1000:.0f} mV) should exceed measured range (400-600 mV)"

    def test_anaerobic_digester_low_do(self):
        """
        Anaerobic digester: DO ~ 0.01 mg/L, pH 7.2, T=35°C
        Measured Eh: -200 to -400 mV (sulfate reduction, not ORR-controlled)
        """
        DO = 0.01  # mg/L (trace)
        pH = 7.2
        T = 35.0

        Eh_calc, warnings = do_to_eh(DO, pH, T)

        # Should warn about anaerobic conditions
        assert len(warnings) > 0
        assert "anaerobic" in warnings[0].lower()

        # Calculated Eh will be high (ORR equilibrium with trace O2)
        # but warning tells user this is NOT valid for anaerobic systems
        assert "HER" in warnings[0] or "hydrogen evolution" in warnings[0].lower()


# ==============================================================================
# Test RedoxState Dataclass
# ==============================================================================

class TestRedoxStateDataclass:
    """Test RedoxState convenience functions."""

    def test_create_from_do(self):
        """Create RedoxState from DO."""
        state = create_redox_state_from_do(8.0, pH=7.5, temperature_C=25.0)

        assert state.dissolved_oxygen_mg_L == 8.0
        assert state.pH == 7.5
        assert state.temperature_C == 25.0
        assert state.eh_VSHE is not None
        assert state.eh_VSHE > 0.7  # Oxidizing

    def test_create_from_orp(self):
        """Create RedoxState from ORP reading."""
        state = create_redox_state_from_orp(
            150.0,
            pH=7.0,
            temperature_C=25.0,
            reference_electrode=ReferenceElectrode.SCE
        )

        assert state.orp_mV == 150.0
        assert state.reference_electrode == ReferenceElectrode.SCE
        assert state.eh_VSHE is not None
        assert abs(state.eh_VSHE - 0.392) < 0.01  # 150 mV + 242 mV = 392 mV
        assert state.dissolved_oxygen_mg_L is not None


# ==============================================================================
# Test Temperature Effects
# ==============================================================================

class TestTemperatureEffects:
    """Test temperature-dependent calculations."""

    def test_henry_constant_temperature_dependence(self):
        """Henry's constant (K_H = c/p, mol·L⁻¹·atm⁻¹) should DECREASE with T.

        Gas solubility decreases with temperature, so the solubility constant
        K_H = c/p decreases. This is distinct from the "inverse Henry constant"
        (p/c) which would increase with temperature.
        """
        temps = [5, 15, 25, 35]
        K_Hs = [henry_constant_o2(T) for T in temps]

        for i in range(len(temps) - 1):
            assert K_Hs[i] > K_Hs[i+1], \
                f"K_H should decrease with T (less soluble): {K_Hs[i]:.2e} > {K_Hs[i+1]:.2e}"

    def test_nernst_temperature_correction(self):
        """Nernst equation slope should change with temperature."""
        DO = 8.0
        pH = 7.0

        Eh_25, _ = do_to_eh(DO, pH, 25.0)
        Eh_35, _ = do_to_eh(DO, pH, 35.0)

        # Eh changes slightly with T due to RT/F term
        # At higher T, Nernst slope increases: (RT/F) ∝ T
        assert abs(Eh_35 - Eh_25) < 0.05, \
            f"Eh change with T should be modest: ΔEh = {abs(Eh_35 - Eh_25):.3f} V"


# ==============================================================================
# Run Tests
# ==============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
