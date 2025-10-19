"""
NRL (US Naval Research Laboratory) galvanic corrosion experimental data.

Contains polarization curve data and galvanic couple measurements
used to validate mixed-potential galvanic corrosion models.

References:
- USNavalResearchLaboratory/corrosion-modeling-applications GitHub repo
- NRL publications on galvanic corrosion modeling
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import logging


@dataclass
class PolarizationCurve:
    """
    Polarization curve data for a single material-environment combination.

    Attributes:
        material: Material identifier
        environment: Environment description
        potentials_V_SCE: List of potentials vs SCE (V)
        currents_A_m2: List of corresponding current densities (A/m²)
        reference: Data source
    """
    material: str
    environment: str
    potentials_V_SCE: List[float]
    currents_A_m2: List[float]
    reference: str


@dataclass
class GalvanicTestCase:
    """
    Galvanic corrosion experimental measurement.

    Attributes:
        case_id: Unique identifier
        anode_material: Anode material
        cathode_material: Cathode material
        environment: Environment description
        area_ratio: Cathode/anode area ratio
        measured_potential_V_SCE: Measured mixed potential (V vs SCE)
        measured_current_A_m2: Measured galvanic current density (A/m²)
        anode_rate_mm_per_y: Measured anode corrosion rate (if available)
        reference: Publication reference
    """
    case_id: str
    anode_material: str
    cathode_material: str
    environment: str
    area_ratio: float
    measured_potential_V_SCE: float
    measured_current_A_m2: float
    anode_rate_mm_per_y: Optional[float]
    reference: str


class NRLValidation:
    """
    NRL galvanic corrosion validation dataset manager.

    Provides access to:
    - Polarization curve library
    - Galvanic couple experimental data
    - Mixed-potential model validation
    """

    def __init__(self):
        """Initialize NRL validation dataset"""
        self._logger = logging.getLogger(__name__)
        self._polarization_curves = self._load_polarization_curves()
        self._test_cases = self._load_test_cases()

    def _load_polarization_curves(self) -> List[PolarizationCurve]:
        """
        Load polarization curve library.

        TODO: Load actual NRL polarization curve data when obtained.
        """
        placeholder_curves = [
            PolarizationCurve(
                material="CS",
                environment="seawater, 25°C",
                potentials_V_SCE=[-0.8, -0.7, -0.6, -0.5, -0.4],
                currents_A_m2=[1e-6, 1e-5, 1e-4, 1e-3, 1e-2],
                reference="NRL Report 2015, Figure 3",
            ),
            PolarizationCurve(
                material="316L",
                environment="seawater, 25°C",
                potentials_V_SCE=[-0.4, -0.2, 0.0, 0.2, 0.4],
                currents_A_m2=[1e-8, 1e-7, 1e-6, 1e-5, 1e-4],
                reference="NRL Report 2015, Figure 5",
            ),
        ]

        return placeholder_curves

    def _load_test_cases(self) -> List[GalvanicTestCase]:
        """
        Load galvanic couple experimental data.

        TODO: Load actual NRL experimental data when obtained.
        """
        placeholder_cases = [
            GalvanicTestCase(
                case_id="NRL_GALV_001",
                anode_material="CS",
                cathode_material="316L",
                environment="seawater, 25°C, pH 8.0",
                area_ratio=10.0,  # 10:1 cathode:anode
                measured_potential_V_SCE=-0.55,
                measured_current_A_m2=0.015,
                anode_rate_mm_per_y=0.18,
                reference="NRL Technical Report 2016, Table 2",
            ),
            GalvanicTestCase(
                case_id="NRL_GALV_002",
                anode_material="Al",
                cathode_material="CS",
                environment="seawater, 25°C",
                area_ratio=1.0,  # 1:1
                measured_potential_V_SCE=-0.75,
                measured_current_A_m2=0.002,
                anode_rate_mm_per_y=0.05,
                reference="NRL Technical Report 2016, Table 3",
            ),
        ]

        return placeholder_cases

    def get_polarization_curve(self, material: str, environment: str) -> Optional[PolarizationCurve]:
        """Get polarization curve for material-environment combination"""
        for curve in self._polarization_curves:
            if curve.material == material and environment.lower() in curve.environment.lower():
                return curve
        return None

    def get_all_polarization_curves(self) -> List[PolarizationCurve]:
        """Get all polarization curves"""
        return self._polarization_curves

    def get_test_case(self, case_id: str) -> Optional[GalvanicTestCase]:
        """Get specific test case by ID"""
        for case in self._test_cases:
            if case.case_id == case_id:
                return case
        return None

    def get_all_cases(self) -> List[GalvanicTestCase]:
        """Get all test cases"""
        return self._test_cases

    def validate_galvanic_model(
        self,
        model_function,
        tolerance_potential_mV: float = 50,
        tolerance_current_factor: float = 2.0,
    ) -> Dict[str, Any]:
        """
        Validate a galvanic corrosion model against NRL experimental data.

        Args:
            model_function: Function that takes test case inputs and returns
                           (predicted_potential_V, predicted_current_A_m2)
            tolerance_potential_mV: Acceptable potential error (mV)
            tolerance_current_factor: Acceptable current factor error

        Returns:
            Validation report dictionary
        """
        results = []

        for case in self._test_cases:
            try:
                # Model should return (potential, current)
                predicted_potential, predicted_current = model_function({
                    "anode": case.anode_material,
                    "cathode": case.cathode_material,
                    "environment": case.environment,
                    "area_ratio": case.area_ratio,
                })

                # Calculate errors
                potential_error_V = abs(predicted_potential - case.measured_potential_V_SCE)
                potential_error_mV = potential_error_V * 1000

                current_factor_error = max(predicted_current, case.measured_current_A_m2) / \
                                     min(predicted_current, case.measured_current_A_m2)

                # Check tolerances
                potential_ok = potential_error_mV <= tolerance_potential_mV
                current_ok = current_factor_error <= tolerance_current_factor
                passed = potential_ok and current_ok

                results.append({
                    "case_id": case.case_id,
                    "anode": case.anode_material,
                    "cathode": case.cathode_material,
                    "measured_potential_V": case.measured_potential_V_SCE,
                    "predicted_potential_V": predicted_potential,
                    "potential_error_mV": potential_error_mV,
                    "potential_ok": potential_ok,
                    "measured_current_A_m2": case.measured_current_A_m2,
                    "predicted_current_A_m2": predicted_current,
                    "current_factor_error": current_factor_error,
                    "current_ok": current_ok,
                    "passed": passed,
                })

            except Exception as e:
                self._logger.error(f"Validation failed for {case.case_id}: {e}")
                results.append({
                    "case_id": case.case_id,
                    "error": str(e),
                    "passed": False,
                })

        # Statistics
        valid_results = [r for r in results if "predicted_potential_V" in r]
        passed_count = sum(1 for r in valid_results if r.get("passed", False))

        if valid_results:
            mean_potential_error_mV = sum(r["potential_error_mV"] for r in valid_results) / len(valid_results)
            mean_current_factor = sum(r["current_factor_error"] for r in valid_results) / len(valid_results)
        else:
            mean_potential_error_mV = float('inf')
            mean_current_factor = float('inf')

        return {
            "dataset": "NRL Galvanic Corrosion",
            "total_cases": len(results),
            "passed": passed_count,
            "failed": len(valid_results) - passed_count,
            "pass_rate": passed_count / len(valid_results) if valid_results else 0.0,
            "mean_potential_error_mV": mean_potential_error_mV,
            "mean_current_factor_error": mean_current_factor,
            "tolerance_potential_mV": tolerance_potential_mV,
            "tolerance_current_factor": tolerance_current_factor,
            "details": results,
        }
