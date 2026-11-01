"""Single executable sour-service threshold shared by all public tools.

ISO 15156-2 Figure 1 begins at 0.3 kPa H2S partial pressure.  The licensed
standard remains the controlling source; these constants prevent local unit
conversions and legacy 0.05-psi shorthand from drifting between tools.
"""

SOUR_SERVICE_PH2S_KPA = 0.3
SOUR_SERVICE_PH2S_BAR = SOUR_SERVICE_PH2S_KPA / 100.0

