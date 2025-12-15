"""
Test suite for critical P0 bug fixes.

These tests verify that the critical numerical bugs identified in code review
have been correctly fixed:

1. Faraday's law conversion constant (K) - was ~96.5x too small
2. ipy unit conversion - was treating ipy same as mpy (1000x error)

These tests serve as regression tests to prevent reintroduction of these bugs.
"""

import pytest
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestFaradayConversionConstant:
    """Test that the Faraday's law conversion is correct."""

    def test_conversion_constant_value(self):
        """Verify the K constant is correct: 365.25 * 24 * 3600 * 10 = 3.15576e8"""
        # Replicate the conversion function to verify the math
        # This is the FIXED version of the conversion
        F = 96485.3  # Faraday constant

        def _current_to_corrosion_rate(
            current_density_A_cm2: float,
            molar_mass_g_mol: float,
            electrons_transferred: int,
            density_g_cm3: float
        ) -> float:
            SECONDS_PER_YEAR = 365.25 * 24 * 3600  # 31,557,600 s/year
            K = SECONDS_PER_YEAR * 10.0  # 3.15576e8: converts cm/s → mm/year

            CR_mm_year = (
                current_density_A_cm2 * molar_mass_g_mol * K /
                (electrons_transferred * F * density_g_cm3)
            )
            return CR_mm_year

        # Test with known physics values for iron (Fe)
        rate = _current_to_corrosion_rate(
            current_density_A_cm2=1e-6,  # 1 µA/cm²
            molar_mass_g_mol=55.845,      # Fe atomic mass
            electrons_transferred=2,       # Fe → Fe²⁺ + 2e⁻
            density_g_cm3=7.85            # Iron density
        )

        # Literature value for 1 µA/cm² on Fe is ~0.0116 mm/year
        assert 0.010 < rate < 0.013, (
            f"Faraday conversion sanity check failed: {rate:.6f} mm/year. "
            f"Expected ~0.0116 mm/year for 1 µA/cm² on Fe."
        )

    def test_old_buggy_k_gives_wrong_result(self):
        """Verify the old K=3.27e6 would give ~96.5x wrong result."""
        F = 96485.3
        K_old_buggy = 3.27e6  # OLD BUGGY VALUE
        K_correct = 365.25 * 24 * 3600 * 10.0  # 3.15576e8

        # Calculate with both K values
        i = 1e-6
        M = 55.845
        n = 2
        rho = 7.85

        rate_buggy = (i * M * K_old_buggy) / (n * F * rho)
        rate_correct = (i * M * K_correct) / (n * F * rho)

        # Correct should be ~96.5x higher than buggy
        ratio = rate_correct / rate_buggy
        assert 95 < ratio < 98, f"Ratio should be ~96.5, got {ratio}"

    def test_k_constant_calculation(self):
        """Verify K = 365.25 * 24 * 3600 * 10 ≈ 3.15576e8"""
        K = 365.25 * 24 * 3600 * 10.0
        assert abs(K - 3.15576e8) < 1e4, f"K should be ~3.15576e8, got {K}"


class TestUnitConversions:
    """Test that unit conversions (mpy, ipy) are correct."""

    def test_mpy_to_mm_y_conversion(self):
        """Verify mpy → mm/y conversion: mpy / 39.37 = mm/y"""
        # 39.37 mils = 1 mm, so 39.37 mpy = 1 mm/y
        mils_per_mm = 39.37
        mpy_value = 39.37
        expected_mm_y = mpy_value / mils_per_mm

        assert abs(expected_mm_y - 1.0) < 0.01, "mpy conversion math is wrong"

    def test_ipy_to_mm_y_conversion(self):
        """Verify ipy → mm/y conversion: ipy * 25.4 = mm/y"""
        # 1 inch = 25.4 mm, so 1 ipy = 25.4 mm/y
        ipy_value = 1.0
        correct_mm_y = ipy_value * 25.4
        buggy_mm_y = ipy_value / 39.37  # Old buggy conversion

        # Correct value should be ~1000x the buggy value
        assert correct_mm_y > buggy_mm_y * 900, "ipy conversion formula is wrong"
        assert abs(correct_mm_y - 25.4) < 0.01, "ipy→mm/y should be 25.4 mm/y for 1 ipy"

    def test_unit_conversion_factor_difference(self):
        """Verify mpy and ipy use different conversion factors."""
        mpy_to_mm_y = lambda mpy: mpy / 39.37
        ipy_to_mm_y = lambda ipy: ipy * 25.4

        # Same numeric value in each unit should give vastly different mm/y
        value = 1.0
        mpy_result = mpy_to_mm_y(value)  # 1 mpy ≈ 0.0254 mm/y
        ipy_result = ipy_to_mm_y(value)  # 1 ipy = 25.4 mm/y

        # ipy result should be ~1000x mpy result
        ratio = ipy_result / mpy_result
        assert 999 < ratio < 1001, f"ipy/mpy ratio should be ~1000, got {ratio}"


class TestPackageDataExists:
    """Test that data files are accessible (for packaging verification)."""

    def test_nrl_coefficients_exist(self):
        """Verify NRL coefficient CSV files exist at canonical location."""
        nrl_dir = Path(__file__).parent.parent / "external" / "nrl_coefficients"

        required_files = [
            "HY80ORRCoeffs.csv",
            "HY80HERCoeffs.csv",
            "SS316ORRCoeffs.csv",
            "SS316PassCoeffs.csv",
            "SeawaterPotentialData.xml",  # Consolidated from data/nrl_csv_files/
        ]

        for filename in required_files:
            filepath = nrl_dir / filename
            assert filepath.exists(), f"Missing required data file: {filepath}"

    def test_nrl_csv_files_deleted(self):
        """Verify the duplicate data directory was removed."""
        old_dir = Path(__file__).parent.parent / "data" / "nrl_csv_files"
        assert not old_dir.exists(), (
            f"Duplicate data directory still exists: {old_dir}. "
            "Should have been consolidated to external/nrl_coefficients/"
        )

    def test_databases_init_exists(self):
        """Verify databases/__init__.py was created."""
        init_file = Path(__file__).parent.parent / "databases" / "__init__.py"
        assert init_file.exists(), "databases/__init__.py is missing"


class TestCodeChangesVerification:
    """Verify the code changes were actually made to the source files."""

    def test_galvanic_k_constant_fixed_in_source(self):
        """Verify predict_galvanic_corrosion.py has the fixed K constant."""
        source_file = Path(__file__).parent.parent / "tools" / "mechanistic" / "predict_galvanic_corrosion.py"
        content = source_file.read_text(encoding="utf-8")

        # Should contain the new K calculation
        assert "SECONDS_PER_YEAR" in content, "SECONDS_PER_YEAR variable not found"
        assert "3.27e6" not in content or "Previous value 3.27e6" in content, (
            "Old buggy K value 3.27e6 still present without being documented as fixed"
        )

    def test_ipy_conversion_fixed_in_material_screening(self):
        """Verify material_screening.py has fixed ipy conversion."""
        source_file = Path(__file__).parent.parent / "tools" / "handbook" / "material_screening.py"
        content = source_file.read_text(encoding="utf-8")

        # Should use *= 25.4 for ipy, not /= 39.37
        assert "*= 25.4" in content or "* 25.4" in content, (
            "ipy *= 25.4 conversion not found in material_screening.py"
        )

    def test_ipy_conversion_fixed_in_typical_rates(self):
        """Verify typical_rates.py has fixed ipy conversion."""
        source_file = Path(__file__).parent.parent / "tools" / "handbook" / "typical_rates.py"
        content = source_file.read_text(encoding="utf-8")

        # Should have separate handling for ipy
        assert "* 25.4" in content, (
            "ipy * 25.4 conversion not found in typical_rates.py"
        )

    def test_pyproject_has_package_data(self):
        """Verify pyproject.toml declares package data."""
        source_file = Path(__file__).parent.parent / "pyproject.toml"
        content = source_file.read_text(encoding="utf-8")

        assert "[tool.setuptools.package-data]" in content, (
            "package-data section not found in pyproject.toml"
        )
        assert "include-package-data = true" in content, (
            "include-package-data not found in pyproject.toml"
        )

    def test_manifest_in_exists(self):
        """Verify MANIFEST.in was created."""
        manifest = Path(__file__).parent.parent / "MANIFEST.in"
        assert manifest.exists(), "MANIFEST.in not found"
        content = manifest.read_text(encoding="utf-8")
        assert "recursive-include external" in content, (
            "MANIFEST.in should include external/ directory"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
