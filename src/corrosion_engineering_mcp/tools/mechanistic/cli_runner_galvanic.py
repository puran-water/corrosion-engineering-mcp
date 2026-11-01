#!/usr/bin/env python3
"""Fail-closed CLI tombstone for the withdrawn galvanic predictor.

Accepts the legacy JSON request on stdin and returns a nonzero, typed withdrawal
error. No numerical result can be emitted through this alternate accessor.

Usage:
    echo '{"anode_material": "HY80", "cathode_material": "SS316", ...}' | python cli_runner_galvanic.py
"""

import sys
import json
from pathlib import Path

# Add project root to sys.path to enable imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from corrosion_engineering_mcp.core.errors import CorrosionPublicError
from corrosion_engineering_mcp.tools.mechanistic.predict_galvanic_corrosion import GalvanicModelWithdrawnError, predict_galvanic_corrosion


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
        allow_auto_orient = input_data.get('allow_auto_orient', False)
        sulfide_mg_L = input_data.get('sulfide_mg_L', 0.0)

        # The predictor is also fail-closed. The explicit raise after it protects
        # this sibling accessor if that lower-level invariant ever regresses.
        predict_galvanic_corrosion(
            anode_material=anode_material,
            cathode_material=cathode_material,
            temperature_C=temperature_C,
            pH=pH,
            chloride_mg_L=chloride_mg_L,
            area_ratio_cathode_to_anode=area_ratio_cathode_to_anode,
            velocity_m_s=velocity_m_s,
            dissolved_oxygen_mg_L=dissolved_oxygen_mg_L,
            allow_auto_orient=allow_auto_orient,
            sulfide_mg_L=sulfide_mg_L,
        )
        raise GalvanicModelWithdrawnError()

    except CorrosionPublicError as e:
        error_result = {
            'error': str(e),
            'error_type': type(e).__name__,
            'error_code': e.code,
            'details': e.details,
        }
        json.dump(error_result, sys.stdout)
        sys.exit(1)
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
