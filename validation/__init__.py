"""
Validation dataset registry and benchmark testing framework.

This module provides:
- Registry of validation datasets (NORSOK, Ohio U, NRL, DNV)
- Automated benchmark testing against literature data
- Accuracy tracking and reporting
- Regression test suite

Design Philosophy (from Codex review):
- "Validation data pipeline unspecified; establish benchmark set (Ohio U datasets,
   NRL galvanic experiments) and automate regression tests before layering Monte
   Carlo outputs."
"""

from .norsok_benchmarks import NORSOKValidation
from .ohio_u_datasets import OhioUValidation
from .nrl_experiments import NRLValidation
from .run_validation import run_all_validations, ValidationReport

__all__ = [
    "NORSOKValidation",
    "OhioUValidation",
    "NRLValidation",
    "run_all_validations",
    "ValidationReport",
]
