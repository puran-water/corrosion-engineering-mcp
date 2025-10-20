"""
Tests for mass transfer calculations (utils/mass_transfer.py)

Validates:
- Dimensionless number calculations (Re, Sc)
- Sherwood correlations (laminar/turbulent pipe, flat plate)
- Mass transfer coefficient
- Limiting current density for ORR
- End-to-end workflow

All tests use textbook benchmark values with ±10% tolerance for empirical correlations.
"""

import pytest
from utils.mass_transfer import (
    calculate_reynolds_number,
    calculate_schmidt_number,
    calculate_kinematic_viscosity,
    calculate_sherwood_number_laminar_pipe,
    calculate_sherwood_number_turbulent_pipe,
    calculate_sherwood_number_flat_plate,
    calculate_sherwood_number,
    calculate_mass_transfer_coefficient,
    calculate_limiting_current_density,
    calculate_limiting_current_from_flow,
    FARADAY_CONSTANT,
)


# ============================================================================
# Test Dimensionless Numbers
# ============================================================================

class TestReynoldsNumber:
    """Test Reynolds number calculations"""

    def test_laminar_flow(self):
        """Laminar flow in pipe: Re < 2300"""
        # Water at 20°C in small pipe
        Re = calculate_reynolds_number(
            velocity_m_s=0.1,  # 10 cm/s
            length_m=0.01,  # 1 cm diameter
            density_kg_m3=1000.0,
            viscosity_Pa_s=0.001,
        )
        assert Re == pytest.approx(1000.0, rel=0.01)
        assert Re < 2300, "Should be laminar"

    def test_turbulent_flow(self):
        """Turbulent flow in pipe: Re > 4000"""
        # Water at 20°C, typical pipe flow
        Re = calculate_reynolds_number(
            velocity_m_s=1.0,
            length_m=0.05,  # 5 cm diameter
            density_kg_m3=1000.0,
            viscosity_Pa_s=0.001,
        )
        assert Re == pytest.approx(50000.0, rel=0.01)
        assert Re > 4000, "Should be turbulent"

    def test_seawater_pipe(self):
        """Seawater in industrial pipe"""
        Re = calculate_reynolds_number(
            velocity_m_s=2.0,
            length_m=0.10,  # 10 cm diameter
            density_kg_m3=1025.0,  # Seawater density
            viscosity_Pa_s=0.0011,  # Seawater at 25°C
        )
        assert Re == pytest.approx(186363.6, rel=0.01)


class TestSchmidtNumber:
    """Test Schmidt number calculations"""

    def test_oxygen_in_water_25C(self):
        """Oxygen in water at 25°C: Sc ≈ 500-600"""
        # Typical values from literature
        nu_water = 1.0e-6  # m²/s (kinematic viscosity)
        D_O2 = 2.0e-9  # m²/s (O2 diffusivity at 25°C)

        Sc = calculate_schmidt_number(nu_water, D_O2)
        assert Sc == pytest.approx(500.0, rel=0.01)
        assert 400 < Sc < 700, "Typical range for dissolved gases"

    def test_gas_phase(self):
        """Gases have Sc ≈ 1"""
        Sc = calculate_schmidt_number(1.5e-5, 2.0e-5)
        assert Sc == pytest.approx(0.75, abs=0.5)

    def test_high_sc_liquid(self):
        """High Sc for large molecules in liquid"""
        Sc = calculate_schmidt_number(1.0e-6, 1.0e-10)
        assert Sc == pytest.approx(10000.0, rel=0.01)


class TestKinematicViscosity:
    """Test kinematic viscosity calculation"""

    def test_water_20C(self):
        """Water at 20°C: ν ≈ 1.0×10⁻⁶ m²/s"""
        nu = calculate_kinematic_viscosity(0.001, 1000.0)
        assert nu == pytest.approx(1.0e-6, rel=0.01)

    def test_seawater_25C(self):
        """Seawater at 25°C"""
        nu = calculate_kinematic_viscosity(0.0011, 1025.0)
        assert nu == pytest.approx(1.073e-6, rel=0.02)


# ============================================================================
# Test Sherwood Number Correlations
# ============================================================================

class TestSherwoodLaminarPipe:
    """Test laminar pipe Sherwood correlations"""

    def test_fully_developed_laminar(self):
        """Fully developed laminar: Sh = 3.66 (constant wall concentration)"""
        # Very long pipe to suppress Graetz entrance effects
        # Gz = (D/L)*Re*Sc = (0.05/5000)*1000*600 = 6 << 10
        Sh = calculate_sherwood_number_laminar_pipe(
            Re=1000,
            Sc=600,
            length_m=5000.0,  # Very long pipe (5 km) to reach fully developed
            diameter_m=0.05,
            entry_effects=True,
        )
        assert Sh == pytest.approx(3.66, abs=0.5)

    def test_fully_developed_laminar_no_entry(self):
        """Fully developed laminar with entry_effects=False"""
        # Short pipe but disable entrance effects
        Sh = calculate_sherwood_number_laminar_pipe(
            Re=1000,
            Sc=600,
            length_m=10.0,
            diameter_m=0.05,
            entry_effects=False,  # Force fully developed
        )
        assert Sh == pytest.approx(3.66, abs=0.1)

    def test_developing_flow_graetz(self):
        """Developing laminar flow with Graetz effects"""
        # Use parameters that give Gz in valid range (10 < Gz < 2000)
        # Target Gz ≈ 1000 for strong entrance effects
        # Gz = (D/L)*Re*Sc → L = D*Re*Sc/Gz = 0.05*1200*600/1000 = 36 m
        Sh = calculate_sherwood_number_laminar_pipe(
            Re=1200,
            Sc=600,
            length_m=36.0,  # Long enough to keep Gz in valid range
            diameter_m=0.05,
            entry_effects=True,
        )
        # Graetz: Gz = (0.05/36) * 1200 * 600 = 1000 (within valid range 10-2000)
        # Sh ≈ 1.86 * (1000)^(1/3) ≈ 18.6
        assert Sh > 10, "Entrance effects should increase Sh significantly"
        assert Sh == pytest.approx(18.6, rel=0.15)


class TestSherwoodTurbulentPipe:
    """Test turbulent pipe Sherwood correlations"""

    def test_turbulent_pipe_chilton_colburn(self):
        """Turbulent pipe: Sh = 0.023 * Re^0.8 * Sc^(1/3) from ht library"""
        Re = 50000
        Sc = 600
        Sh = calculate_sherwood_number_turbulent_pipe(Re, Sc)

        # Expected from ht.turbulent_Colburn(Re=50000, Pr=600)
        Sh_expected = 1114.18
        assert Sh == pytest.approx(Sh_expected, rel=0.01)

    def test_seawater_turbulent(self):
        """Seawater turbulent flow (Re=100k, Sc=600)"""
        Sh = calculate_sherwood_number_turbulent_pipe(100000, 600)
        # Expected from ht.turbulent_Colburn(Re=100000, Pr=600)
        assert Sh == pytest.approx(1939.90, rel=0.01)


class TestSherwoodFlatPlate:
    """Test flat plate boundary layer correlations"""

    def test_laminar_boundary_layer(self):
        """Laminar flat plate: Sh = 0.664 * Re^0.5 * Sc^(1/3)"""
        Re = 10000
        Sc = 600
        Sh = calculate_sherwood_number_flat_plate(Re, Sc, regime="laminar")

        # Expected: 0.664 * (10000^0.5) * (600^(1/3)) = 560.04
        assert Sh == pytest.approx(560.04, rel=0.01)

    def test_turbulent_boundary_layer(self):
        """Turbulent flat plate: Sh = 0.037 * Re^0.8 * Sc^(1/3)"""
        Re = 1e6
        Sc = 600
        Sh = calculate_sherwood_number_flat_plate(Re, Sc, regime="turbulent")

        # Expected: 0.037 * (1e6^0.8) * (600^(1/3)) = 19690.29
        assert Sh == pytest.approx(19690.29, rel=0.01)


class TestSherwoodGeneral:
    """Test general Sherwood calculator with auto regime detection"""

    def test_auto_laminar_pipe(self):
        """Automatic laminar detection for pipe"""
        Sh = calculate_sherwood_number(
            Re=1000, Sc=600, geometry="pipe", length_m=5000.0, diameter_m=0.05
        )
        # Long pipe suppresses Graetz effects: Gz = (0.05/5000)*1000*600 = 6 << 10
        assert Sh == pytest.approx(3.66, abs=1.0)

    def test_auto_turbulent_pipe(self):
        """Automatic turbulent detection for pipe"""
        Sh = calculate_sherwood_number(Re=50000, Sc=600, geometry="pipe")
        # Should use turbulent correlation: ~1114
        assert Sh == pytest.approx(1114.18, rel=0.02)

    def test_auto_laminar_plate(self):
        """Automatic laminar detection for flat plate"""
        Sh = calculate_sherwood_number(Re=1e4, Sc=600, geometry="plate")
        assert Sh == pytest.approx(560.04, rel=0.01)

    def test_transitional_regime(self):
        """Transitional regime (2300 < Re < 10000): uses laminar correlation"""
        Sh = calculate_sherwood_number(
            Re=5000, Sc=600, geometry="pipe", length_m=1000.0, diameter_m=0.05
        )
        # Transitional now uses laminar correlation (conservative for corrosion)
        # turbulent_Colburn not valid below Re=10,000
        # Gz = (0.05/1000)*5000*600 = 150 → Sh ≈ 1.86*150^(1/3) ≈ 9.9
        assert Sh > 3.66  # Should be > fully developed
        assert Sh < 20  # But not too high (Graetz limited)


# ============================================================================
# Test Mass Transfer Coefficient
# ============================================================================

class TestMassTransferCoefficient:
    """Test mass transfer coefficient calculations"""

    def test_k_L_from_Sh(self):
        """Convert Sh → k_L"""
        Sh = 100
        D = 2.0e-9  # m²/s (O2 in water)
        L = 0.05  # m

        k_L = calculate_mass_transfer_coefficient(Sh, D, L)
        expected = 100 * 2.0e-9 / 0.05  # = 4.0e-6 m/s
        assert k_L == pytest.approx(expected, rel=0.01)
        assert k_L == pytest.approx(4.0e-6, rel=0.01)

    def test_typical_seawater_turbulent(self):
        """Typical k_L for turbulent seawater"""
        Sh = 500
        D = 2.1e-9  # m²/s (O2 at 25°C)
        L = 0.10  # 10 cm diameter pipe

        k_L = calculate_mass_transfer_coefficient(Sh, D, L)
        assert 1e-5 < k_L < 1e-4, "Typical range for turbulent flow"


# ============================================================================
# Test Limiting Current Density
# ============================================================================

class TestLimitingCurrentDensity:
    """Test limiting current calculations for ORR"""

    def test_i_lim_basic(self):
        """Basic limiting current: i_lim = n*F*k_L*c_O2"""
        k_L = 5.0e-5  # m/s
        c_O2 = 0.20  # mol/m³ (typical seawater DO)
        n = 4  # electrons for ORR

        i_lim = calculate_limiting_current_density(k_L, c_O2, n_electrons=n)
        expected = 4 * FARADAY_CONSTANT * 5.0e-5 * 0.20
        assert i_lim == pytest.approx(expected, rel=0.01)
        assert i_lim == pytest.approx(3.86, rel=0.01)  # A/m²

    def test_seawater_typical(self):
        """Typical seawater: DO=6.5 mg/L ≈ 0.203 mol/m³"""
        k_L = 3.0e-5  # m/s (moderate turbulence)
        c_O2 = 0.203  # mol/m³

        i_lim = calculate_limiting_current_density(k_L, c_O2)
        assert i_lim == pytest.approx(2.35, rel=0.10)  # A/m² (23.5 mA/dm²)

    def test_low_do_stagnant(self):
        """Low DO, stagnant conditions"""
        k_L = 1.0e-6  # m/s (very low mass transfer)
        c_O2 = 0.10  # mol/m³ (low DO)

        i_lim = calculate_limiting_current_density(k_L, c_O2)
        assert i_lim < 0.1, "Low limiting current for stagnant conditions"


# ============================================================================
# Test End-to-End Workflow
# ============================================================================

class TestLimitingCurrentFromFlow:
    """Test integrated workflow: flow → i_lim"""

    def test_turbulent_seawater_pipe(self):
        """End-to-end: turbulent seawater in pipe"""
        result = calculate_limiting_current_from_flow(
            velocity_m_s=1.0,
            diameter_m=0.05,
            length_m=1.0,
            density_kg_m3=1025.0,
            viscosity_Pa_s=0.0011,
            diffusivity_m2_s=2.1e-9,
            oxygen_concentration_mol_m3=0.20,
            temperature_C=25.0,
            geometry="pipe",
        )

        assert result["Re"] > 10000, "Should be turbulent"
        assert result["regime"] == "turbulent"
        assert result["Sc"] == pytest.approx(524.0, rel=0.10)
        assert result["Sh"] > 300, "High Sh for turbulent flow"
        assert result["k_L_m_s"] > 1e-5, "Significant mass transfer"
        assert result["i_lim_A_m2"] > 1.0, "Practical limiting current"

    def test_laminar_pipe(self):
        """End-to-end: laminar flow"""
        result = calculate_limiting_current_from_flow(
            velocity_m_s=0.05,
            diameter_m=0.01,
            length_m=1000.0,  # Long pipe to suppress Graetz effects
            density_kg_m3=1000.0,
            viscosity_Pa_s=0.001,
            diffusivity_m2_s=2.0e-9,
            oxygen_concentration_mol_m3=0.25,
            temperature_C=20.0,
        )

        assert result["Re"] == pytest.approx(500.0, rel=0.01)
        assert result["regime"] == "laminar"
        # Gz = (0.01/1000)*500*1000 = 5 << 10, so Sh ≈ 3.66
        assert result["Sh"] == pytest.approx(3.66, abs=1.0)

    def test_output_structure(self):
        """Verify output dictionary structure"""
        result = calculate_limiting_current_from_flow(
            velocity_m_s=1.0,
            diameter_m=0.05,
            length_m=1.0,
            density_kg_m3=1000.0,
            viscosity_Pa_s=0.001,
            diffusivity_m2_s=2.0e-9,
            oxygen_concentration_mol_m3=0.20,
        )

        # Check all required keys present
        assert "Re" in result
        assert "Sc" in result
        assert "Sh" in result
        assert "k_L_m_s" in result
        assert "i_lim_A_m2" in result
        assert "regime" in result
        assert "temperature_C" in result

        # Check types
        assert isinstance(result["Re"], float)
        assert isinstance(result["regime"], str)

    def test_flat_plate_laminar(self):
        """End-to-end: laminar flat plate boundary layer"""
        result = calculate_limiting_current_from_flow(
            velocity_m_s=0.1,
            length_m=0.5,  # Plate length (characteristic length for plate)
            geometry="plate",
            density_kg_m3=1000.0,
            viscosity_Pa_s=0.001,
            diffusivity_m2_s=2.0e-9,
            oxygen_concentration_mol_m3=0.20,
            temperature_C=25.0,
        )

        # Re = V*L/nu = 0.1*0.5/(0.001/1000) = 50,000 → laminar plate
        assert result["Re"] == pytest.approx(50000.0, rel=0.01)
        assert result["regime"] == "laminar"
        # Laminar plate: Sh = 0.664*Re^0.5*Sc^(1/3)
        # Sc = 0.001/1000 / 2e-9 = 500
        # Sh = 0.664*50000^0.5*500^(1/3) ≈ 1102
        assert result["Sh"] > 1000
        assert result["Sh"] < 1200

    def test_flat_plate_missing_length_error(self):
        """Flat plate requires length_m parameter"""
        with pytest.raises(ValueError, match="length_m is required for geometry='plate'"):
            calculate_limiting_current_from_flow(
                velocity_m_s=1.0,
                diameter_m=0.05,
                geometry="plate",  # Plate geometry but no length_m provided
                density_kg_m3=1000.0,
                viscosity_Pa_s=0.001,
                diffusivity_m2_s=2.0e-9,
                oxygen_concentration_mol_m3=0.20,
            )


# ============================================================================
# Test Physical Sanity Checks
# ============================================================================

class TestPhysicalSanity:
    """Sanity checks for physical realism"""

    def test_increasing_velocity_increases_i_lim(self):
        """Higher velocity → higher i_lim"""
        velocities = [0.1, 0.5, 1.0, 2.0]
        i_lims = []

        for v in velocities:
            result = calculate_limiting_current_from_flow(
                velocity_m_s=v,
                diameter_m=0.05,
                length_m=1.0,
                density_kg_m3=1000.0,
                viscosity_Pa_s=0.001,
                diffusivity_m2_s=2.0e-9,
                oxygen_concentration_mol_m3=0.20,
            )
            i_lims.append(result["i_lim_A_m2"])

        # i_lim should increase monotonically with velocity
        for i in range(len(i_lims) - 1):
            assert i_lims[i] < i_lims[i + 1], \
                f"i_lim should increase: {i_lims[i]:.2f} < {i_lims[i+1]:.2f}"

    def test_higher_do_higher_i_lim(self):
        """Higher DO → higher i_lim (linear relationship)"""
        result_low = calculate_limiting_current_from_flow(
            velocity_m_s=1.0,
            diameter_m=0.05,
            length_m=1.0,
            density_kg_m3=1000.0,
            viscosity_Pa_s=0.001,
            diffusivity_m2_s=2.0e-9,
            oxygen_concentration_mol_m3=0.10,  # Low DO
        )

        result_high = calculate_limiting_current_from_flow(
            velocity_m_s=1.0,
            diameter_m=0.05,
            length_m=1.0,
            density_kg_m3=1000.0,
            viscosity_Pa_s=0.001,
            diffusivity_m2_s=2.0e-9,
            oxygen_concentration_mol_m3=0.30,  # High DO
        )

        # i_lim should scale linearly with DO
        ratio = result_high["i_lim_A_m2"] / result_low["i_lim_A_m2"]
        assert ratio == pytest.approx(3.0, rel=0.01), "i_lim ∝ c_O2"

    def test_turbulent_higher_than_laminar(self):
        """Turbulent Sh > Laminar Sh"""
        Sh_laminar = calculate_sherwood_number(Re=1000, Sc=600, geometry="pipe")
        Sh_turbulent = calculate_sherwood_number(Re=50000, Sc=600, geometry="pipe")

        assert Sh_turbulent > Sh_laminar * 10, \
            "Turbulent mass transfer should be much higher"


# ============================================================================
# Test Error Handling
# ============================================================================

class TestErrorHandling:
    """Test error cases and warnings"""

    def test_invalid_geometry(self):
        """Invalid geometry should raise ValueError"""
        with pytest.raises(ValueError, match="Unknown geometry"):
            calculate_sherwood_number(Re=5000, Sc=600, geometry="sphere")

    def test_negative_values_caught(self):
        """Negative physical values should still compute (though unphysical)"""
        # Library should not crash, but may produce nonsense results
        Re = calculate_reynolds_number(-1.0, 0.05, 1000.0, 0.001)
        assert Re < 0  # Nonsense but computable
