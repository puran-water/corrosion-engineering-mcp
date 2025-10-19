"""
Automated validation runner and reporting.

Executes all validation benchmarks and generates comprehensive reports.
"""

from typing import Dict, Any, List, Callable
from dataclasses import dataclass
import logging
from datetime import datetime

from .norsok_benchmarks import NORSOKValidation
from .ohio_u_datasets import OhioUValidation
from .nrl_experiments import NRLValidation


@dataclass
class ValidationReport:
    """
    Comprehensive validation report across all datasets.

    Attributes:
        timestamp: When validation was run
        datasets: List of dataset names validated
        overall_pass_rate: Aggregate pass rate across all datasets
        details: Per-dataset validation results
        summary: Text summary of validation
    """
    timestamp: datetime
    datasets: List[str]
    overall_pass_rate: float
    details: Dict[str, Any]
    summary: str


def run_all_validations(
    co2_model_function: Callable = None,
    galvanic_model_function: Callable = None,
    tolerance_factor: float = 2.0,
) -> ValidationReport:
    """
    Run all validation benchmarks.

    Args:
        co2_model_function: CO2/H2S corrosion model to validate
        galvanic_model_function: Galvanic corrosion model to validate
        tolerance_factor: Acceptable deviation (default: ±2x)

    Returns:
        ValidationReport with results from all datasets

    Example:
        def my_co2_model(inputs):
            # Your model implementation
            return predicted_rate_mm_per_y

        report = run_all_validations(
            co2_model_function=my_co2_model,
            tolerance_factor=2.0
        )

        print(f"Overall pass rate: {report.overall_pass_rate:.1%}")
        print(report.summary)
    """
    logger = logging.getLogger(__name__)
    logger.info("Starting comprehensive validation...")

    results = {}

    # Run NORSOK M-506 validation
    if co2_model_function:
        logger.info("Validating against NORSOK M-506...")
        norsok = NORSOKValidation()
        results["NORSOK"] = norsok.validate_model(co2_model_function, tolerance_factor)
    else:
        logger.warning("No CO2 model provided - skipping NORSOK validation")

    # Run Ohio U validation
    if co2_model_function:
        logger.info("Validating against Ohio U FREECORP...")
        ohio_u = OhioUValidation()
        results["OhioU"] = ohio_u.validate_model(co2_model_function, tolerance_factor)
    else:
        logger.warning("No CO2 model provided - skipping Ohio U validation")

    # Run NRL validation
    if galvanic_model_function:
        logger.info("Validating against NRL galvanic data...")
        nrl = NRLValidation()
        results["NRL"] = nrl.validate_galvanic_model(
            galvanic_model_function,
            tolerance_potential_mV=50,
            tolerance_current_factor=tolerance_factor,
        )
    else:
        logger.warning("No galvanic model provided - skipping NRL validation")

    # Calculate overall statistics
    total_cases = sum(r.get("total_cases", 0) for r in results.values())
    total_passed = sum(r.get("passed", 0) for r in results.values())
    overall_pass_rate = total_passed / total_cases if total_cases > 0 else 0.0

    # Generate summary
    summary = _generate_summary(results, overall_pass_rate)

    report = ValidationReport(
        timestamp=datetime.now(),
        datasets=list(results.keys()),
        overall_pass_rate=overall_pass_rate,
        details=results,
        summary=summary,
    )

    logger.info(f"Validation complete. Overall pass rate: {overall_pass_rate:.1%}")

    return report


def _generate_summary(results: Dict[str, Any], overall_pass_rate: float) -> str:
    """Generate human-readable validation summary"""
    lines = [
        "=" * 70,
        "CORROSION MODEL VALIDATION REPORT",
        "=" * 70,
        "",
        f"Overall Pass Rate: {overall_pass_rate:.1%}",
        "",
        "Dataset Results:",
        "-" * 70,
    ]

    for dataset, result in results.items():
        lines.append(f"\n{dataset}:")
        lines.append(f"  Total Cases: {result.get('total_cases', 0)}")
        lines.append(f"  Passed: {result.get('passed', 0)}")
        lines.append(f"  Failed: {result.get('failed', 0)}")
        lines.append(f"  Pass Rate: {result.get('pass_rate', 0):.1%}")

        if "mean_absolute_relative_error" in result:
            mare = result["mean_absolute_relative_error"]
            lines.append(f"  Mean Absolute Relative Error: {mare:.2%}")

        if "mean_factor_error" in result:
            mfe = result["mean_factor_error"]
            lines.append(f"  Mean Factor Error: {mfe:.2f}x")

        if "mean_potential_error_mV" in result:
            mpe = result["mean_potential_error_mV"]
            lines.append(f"  Mean Potential Error: {mpe:.1f} mV")

    lines.extend([
        "",
        "=" * 70,
        "Assessment:",
        "-" * 70,
    ])

    # Assessment
    if overall_pass_rate >= 0.9:
        lines.append("✓ EXCELLENT: Model performance exceeds literature expectations")
    elif overall_pass_rate >= 0.75:
        lines.append("✓ GOOD: Model performance within literature expectations (±factor 2)")
    elif overall_pass_rate >= 0.5:
        lines.append("⚠ FAIR: Model performance marginally acceptable - review failure cases")
    else:
        lines.append("✗ POOR: Model performance below acceptable threshold - requires refinement")

    lines.append("")
    lines.append("=" * 70)

    return "\n".join(lines)


def export_validation_report(report: ValidationReport, filepath: str):
    """Export validation report to JSON file"""
    import json

    data = {
        "timestamp": report.timestamp.isoformat(),
        "datasets": report.datasets,
        "overall_pass_rate": report.overall_pass_rate,
        "details": report.details,
        "summary": report.summary,
    }

    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2)

    logging.info(f"Validation report exported to {filepath}")


def print_validation_report(report: ValidationReport):
    """Print validation report to console"""
    print(report.summary)

    # Optionally print detailed results
    print("\nDetailed Results Available:")
    for dataset in report.datasets:
        print(f"  - {dataset}: {len(report.details[dataset].get('details', []))} cases")
