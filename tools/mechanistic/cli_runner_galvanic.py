#!/usr/bin/env python3
"""
CLI runner for galvanic corrosion calculation.

Executes predict_galvanic_corrosion() in isolated subprocess to avoid MCP overhead.
Accepts JSON parameters via stdin, returns JSON result to stdout.

Usage:
    echo '{"anode_material": "HY80", "cathode_material": "SS316", ...}' | python cli_runner_galvanic.py
"""

import sys
import json
from pathlib import Path

# Add project root to sys.path to enable imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from tools.mechanistic.predict_galvanic_corrosion import predict_galvanic_corrosion


def downsample_array(arr, target_points=100):
    """Downsample array to target number of points."""
    if len(arr) <= target_points:
        return arr

    # Use uniform stride to select points
    stride = len(arr) // target_points
    return arr[::stride][:target_points]


def main():
    try:
        # Read JSON parameters from stdin
        input_data = json.load(sys.stdin)

        # Extract parameters
        anode_material = input_data['anode_material']
        cathode_material = input_data['cathode_material']
        temperature_C = input_data['temperature_C']
        pH = input_data['pH']
        chloride_mg_L = input_data['chloride_mg_L']
        area_ratio_cathode_to_anode = input_data.get('area_ratio_cathode_to_anode', 1.0)
        velocity_m_s = input_data.get('velocity_m_s', 0.0)
        dissolved_oxygen_mg_L = input_data.get('dissolved_oxygen_mg_L', None)

        # Execute calculation
        result = predict_galvanic_corrosion(
            anode_material=anode_material,
            cathode_material=cathode_material,
            temperature_C=temperature_C,
            pH=pH,
            chloride_mg_L=chloride_mg_L,
            area_ratio_cathode_to_anode=area_ratio_cathode_to_anode,
            velocity_m_s=velocity_m_s,
            dissolved_oxygen_mg_L=dissolved_oxygen_mg_L,
        )

        # Downsample polarization curves to reduce response size
        if 'polarization_curves' in result:
            pol_curves = result['polarization_curves']
            if 'potential_VSCE' in pol_curves:
                target_points = 100
                pol_curves['potential_VSCE'] = downsample_array(pol_curves['potential_VSCE'], target_points)

                # Downsample corresponding current arrays
                if 'anode' in pol_curves and 'current_density_A_cm2' in pol_curves['anode']:
                    pol_curves['anode']['current_density_A_cm2'] = downsample_array(
                        pol_curves['anode']['current_density_A_cm2'], target_points
                    )

                if 'cathode' in pol_curves and 'current_density_A_cm2' in pol_curves['cathode']:
                    pol_curves['cathode']['current_density_A_cm2'] = downsample_array(
                        pol_curves['cathode']['current_density_A_cm2'], target_points
                    )

        # Write result to stdout as JSON
        json.dump(result, sys.stdout)
        sys.exit(0)

    except Exception as e:
        # Write error to stderr and return error JSON to stdout
        error_result = {
            'error': str(e),
            'error_type': type(e).__name__,
        }
        json.dump(error_result, sys.stdout)
        sys.exit(1)


if __name__ == '__main__':
    main()
