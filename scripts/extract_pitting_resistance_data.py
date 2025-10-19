#!/usr/bin/env python3
"""
Pitting Resistance Data Extraction Script

Generates PREN (Pitting Resistance Equivalent Number) and CPT (Critical Pitting
Temperature) data for stainless steels and nickel alloys.

PREN Formula: PREN = %Cr + 3.3×%Mo + 16×%N

Usage:
    python scripts/extract_pitting_resistance_data.py

Output:
    databases/pitting_resistance.json (with PREN, CPT, risk bands)

Notes:
    - Uses composition data from AuthoritativeMaterialDatabase
    - Calculates PREN from standard alloy compositions
    - Estimates CPT using empirical correlations
    - Generates risk bands for material selection guidance
"""

import sys
from pathlib import Path
from datetime import datetime
import json

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


# Standard compositions (from ASTM/UNS standards)
STANDARD_COMPOSITIONS = {
    "304": {
        "name": "304 Stainless Steel",
        "UNS": "S30400",
        "composition": {"Fe": 68.0, "Cr": 19.0, "Ni": 10.0, "C": 0.08, "N": 0.08},
        "typical_applications": "General purpose austenitic SS",
    },
    "316L": {
        "name": "316L Stainless Steel",
        "UNS": "S31603",
        "composition": {"Fe": 66.0, "Cr": 17.0, "Ni": 12.0, "Mo": 2.5, "C": 0.03, "N": 0.03},
        "typical_applications": "Low-carbon variant with improved weldability",
    },
    "duplex_2205": {
        "name": "Duplex 2205",
        "UNS": "S32205",
        "composition": {"Fe": 63.0, "Cr": 22.0, "Ni": 5.5, "Mo": 3.0, "N": 0.17},
        "typical_applications": "Duplex (austenite + ferrite) for high-chloride environments",
    },
    "super_duplex": {
        "name": "Super Duplex (SAF 2507)",
        "UNS": "S32750",
        "composition": {"Fe": 62.0, "Cr": 25.0, "Ni": 7.0, "Mo": 4.0, "N": 0.27},
        "typical_applications": "Super duplex for very aggressive chloride environments",
    },
    "alloy_20": {
        "name": "Alloy 20 (Carpenter 20Cb-3)",
        "UNS": "N08020",
        "composition": {"Fe": 38.0, "Cr": 20.0, "Ni": 33.0, "Mo": 2.5, "Cu": 3.5, "Cb": 1.0, "N": 0.05},
        "typical_applications": "Nickel-iron-chromium alloy for sulfuric acid",
    },
    "254SMO": {
        "name": "254 SMO (6Mo Superaustenitic)",
        "UNS": "S31254",
        "composition": {"Fe": 55.0, "Cr": 20.0, "Ni": 18.0, "Mo": 6.0, "N": 0.20},
        "typical_applications": "6Mo superaustenitic for seawater and brine",
    },
    "C276": {
        "name": "Hastelloy C-276",
        "UNS": "N10276",
        "composition": {"Ni": 57.0, "Cr": 16.0, "Mo": 16.0, "W": 4.0, "Fe": 5.0, "N": 0.01},
        "typical_applications": "Nickel-molybdenum alloy for extreme corrosion resistance",
    },
    "C22": {
        "name": "Hastelloy C-22",
        "UNS": "N06022",
        "composition": {"Ni": 56.0, "Cr": 22.0, "Mo": 13.0, "W": 3.0, "Fe": 3.0, "N": 0.015},
        "typical_applications": "Improved chloride pitting resistance vs C-276",
    },
    "825": {
        "name": "Incoloy 825",
        "UNS": "N08825",
        "composition": {"Ni": 42.0, "Fe": 30.0, "Cr": 21.5, "Mo": 3.0, "Cu": 2.2, "N": 0.02},
        "typical_applications": "Nickel-iron-chromium alloy for sulfuric/phosphoric acid",
    },
}


def calculate_pren(composition: dict) -> float:
    """
    Calculate PREN = %Cr + 3.3×%Mo + 16×%N

    Args:
        composition: Dictionary with element percentages (wt%)

    Returns:
        PREN value
    """
    Cr = composition.get("Cr", 0)
    Mo = composition.get("Mo", 0)
    N = composition.get("N", 0)

    pren = Cr + 3.3 * Mo + 16 * N

    return round(pren, 1)


def estimate_cpt(pren: float) -> float:
    """
    Estimate Critical Pitting Temperature from PREN.

    Empirical correlation (simplified):
    CPT ≈ PREN - 10  (rough approximation)

    More accurate correlations exist but require experimental validation.

    Args:
        pren: PREN value

    Returns:
        Estimated CPT in °C
    """
    # Simplified linear correlation
    # TODO: Replace with more accurate model using experimental data
    cpt = pren - 10

    return round(cpt, 0)


def assign_risk_band(pren: float, cpt: float) -> str:
    """
    Assign pitting risk band based on PREN and CPT.

    Risk bands for seawater (35 g/L Cl⁻):
    - LOW: PREN < 25, CPT < 15°C (avoid for seawater)
    - MEDIUM: PREN 25-35, CPT 15-25°C (acceptable for moderate temps)
    - HIGH: PREN 35-45, CPT 25-35°C (good for seawater up to 80°C)
    - VERY HIGH: PREN > 45, CPT > 35°C (excellent pitting resistance)

    Args:
        pren: PREN value
        cpt: CPT in °C

    Returns:
        Risk band description
    """
    if pren < 25:
        return "LOW (chloride <200 mg/L, T<40°C only)"
    elif pren < 35:
        return "MEDIUM (chloride <1000 mg/L, T<60°C)"
    elif pren < 45:
        return "HIGH (seawater suitable, T<80°C)"
    else:
        return "VERY HIGH (seawater/brine, high temperature)"


def extract_pitting_resistance_data():
    """Extract PREN/CPT data for all standard alloys"""

    extracted_data = {
        "metadata": {
            "extraction_method": "PREN calculation from standard compositions",
            "extraction_date": datetime.now().strftime("%Y-%m-%d"),
            "pren_formula": "PREN = %Cr + 3.3×%Mo + 16×%N",
            "cpt_formula": "CPT ≈ PREN - 10 (simplified empirical)",
            "source_standards": ["ASTM A240", "UNS designations", "ASME SB-625"],
            "parser_version": "1.0",
            "notes": (
                "PREN (Pitting Resistance Equivalent Number) is an empirical index "
                "correlating with pitting corrosion resistance in chloride environments.\n\n"
                "CPT (Critical Pitting Temperature) is estimated from PREN using a "
                "simplified correlation. For critical applications, use experimental CPT "
                "values from ASTM G48 Method E testing.\n\n"
                "Risk bands are for seawater (35 g/L Cl⁻) and should be adjusted for "
                "other chloride concentrations and pH values.\n"
            ),
        },
        "stainless_steels": {},
        "nickel_alloys": {},
    }

    print("Calculating PREN and CPT for standard alloys...\n")

    for alloy_id, alloy_data in STANDARD_COMPOSITIONS.items():
        composition = alloy_data["composition"]
        pren = calculate_pren(composition)
        cpt = estimate_cpt(pren)
        risk_band = assign_risk_band(pren, cpt)

        entry = {
            "name": alloy_data["name"],
            "UNS": alloy_data.get("UNS"),
            "composition": composition,
            "PREN": pren,
            "CPT_estimate_C": cpt,
            "risk_band": risk_band,
            "typical_applications": alloy_data["typical_applications"],
            "source": "Calculated from standard composition",
        }

        # Categorize by material type
        if "Ni" in composition and composition["Ni"] > 30:
            # Nickel alloys (Ni > 30%)
            extracted_data["nickel_alloys"][alloy_id] = entry
            category = "Nickel Alloy"
        else:
            # Stainless steels
            extracted_data["stainless_steels"][alloy_id] = entry
            category = "Stainless Steel"

        print(f"{alloy_data['name']} ({category})")
        print(f"  PREN: {pren}")
        print(f"  CPT estimate: {cpt}°C")
        print(f"  Risk band: {risk_band}")
        print()

    return extracted_data


def main():
    """Main extraction workflow"""
    print("="*70)
    print("Pitting Resistance Data Extraction")
    print("="*70)
    print()

    # Extract data
    extracted_data = extract_pitting_resistance_data()

    total_entries = (
        len(extracted_data["stainless_steels"]) +
        len(extracted_data["nickel_alloys"])
    )

    print("="*70)
    print(f"Extracted {total_entries} alloy entries")
    print(f"  - Stainless steels: {len(extracted_data['stainless_steels'])}")
    print(f"  - Nickel alloys: {len(extracted_data['nickel_alloys'])}")
    print()

    # Write to file
    output_path = Path(__file__).parent.parent / "databases" / "pitting_resistance.json"
    print(f"Writing to {output_path}...")

    with open(output_path, 'w') as f:
        json.dump(extracted_data, f, indent=2)

    print("✅ File written successfully")
    print()
    print("="*70)
    print("Extraction Complete!")
    print("="*70)
    print(f"\nOutput: {output_path}")
    print("\nNext steps:")
    print("1. Review the generated JSON file")
    print("2. Validate PREN calculations against published data")
    print("3. Replace CPT estimates with experimental values (ASTM G48)")
    print("4. Add more alloys as needed (super duplex variants, 6Mo, etc.)")
    print("5. Commit to Git with provenance documentation")


if __name__ == "__main__":
    main()
