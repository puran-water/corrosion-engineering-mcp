"""Legacy multi-source material backend with unsafe aggregates withdrawn.

The old public aggregate merged uncited fallbacks, remote mechanical data,
composition, PREN, galvanic potential, cost factors and ``CPT ≈ PREN - 10`` as
though they shared one provenance standing.  Both that aggregate and the CPT
estimate now fail typed.  The registered server exposes only the separately
named ``corrosion_resolve_material_identity`` catalog resolver.
"""

from typing import Dict, Any, Optional
import pandas as pd
import requests
import requests_cache
import logging
from pathlib import Path
import os
from corrosion_engineering_mcp.core.interfaces import MaterialDatabase
from corrosion_engineering_mcp.core.errors import CorrosionPublicError

logger = logging.getLogger(__name__)


class MaterialPropertiesAccessorSuppressedError(CorrosionPublicError, RuntimeError):
    """The legacy aggregate cannot defend its merged property payload."""

    def __init__(self) -> None:
        super().__init__(
            "MATERIAL_PROPERTIES_ACCESSOR_SUPPRESSED",
            "MATERIAL_PROPERTIES_ACCESSOR_SUPPRESSED: the legacy aggregate mixed "
            "composition, PREN, galvanic potential, mechanical data, cost and an "
            "unsourced CPT estimate under incompatible provenance. Use "
            "corrosion_resolve_material_identity for alias/canonical identity only.",
            details={
                "reason_code": "MATERIAL_PROPERTIES_ACCESSOR_SUPPRESSED",
                "use_instead": "corrosion_resolve_material_identity",
            },
        )


class UnsourcedCPTEstimateSuppressedError(CorrosionPublicError, RuntimeError):
    """PREN minus ten is not a sourced critical-pitting-temperature model."""

    def __init__(self) -> None:
        super().__init__(
            "UNSOURCED_CPT_ESTIMATE_SUPPRESSED",
            "UNSOURCED_CPT_ESTIMATE_SUPPRESSED: CPT ≈ PREN - 10 has no "
            "primary-source validation for this catalog and is not emitted.",
            details={
                "reason_code": "UNSOURCED_CPT_ESTIMATE_SUPPRESSED",
                "suppressed_rule": "CPT_C = PREN - 10",
            },
        )

# Writable caches live outside immutable, potentially hardlinked package assets.
def _http_cache_dir() -> Path:
    configured = os.environ.get("CORROSION_CACHE_DIR")
    if configured:
        directory = Path(configured).expanduser()
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))).expanduser()
        directory = base / "puran" / "corrosion-engineering"
    if not directory.is_absolute():
        raise ValueError("CORROSION_CACHE_DIR/XDG_CACHE_HOME must be absolute")
    directory = directory.resolve()
    package = Path(__file__).resolve().parents[1]
    if directory == package or package in directory.parents:
        raise ValueError("Corrosion HTTP cache must be outside installed package assets")
    return directory


_CACHE_DIR = _http_cache_dir()
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
requests_cache.install_cache(
    str(_CACHE_DIR / "http_cache"),
    backend="sqlite",
    expire_after=86400,  # 24 hours
)


class AuthoritativeMaterialDatabase(MaterialDatabase):
    """
    Legacy backend retained for isolated source experiments.

    Sources:
    - USNavalResearchLaboratory/corrosion-modeling-applications (MIT)
    - KittyCAD/material-properties (Apache-2.0)
    The aggregate property accessor and CPT estimator are suppressed.  Remote
    and private helpers do not constitute a registered property service.
    """

    # Data source URLs
    USNRL_GALVANIC_URL = (
        "https://raw.githubusercontent.com/USNavalResearchLaboratory/"
        "corrosion-modeling-applications/master/cma/SeawaterPotentialData.xml"
    )

    KITTYCAD_STAINLESS_URL = (
        "https://raw.githubusercontent.com/KittyCAD/material-properties/"
        "main/materials/stainlesssteel.json"
    )

    KITTYCAD_NICKEL_URL = (
        "https://raw.githubusercontent.com/KittyCAD/material-properties/"
        "main/materials/nickelalloys.json"
    )

    def __init__(self, use_cache: bool = True):
        """
        Initialize material database.

        Args:
            use_cache: Whether to cache loaded data (default: True)
        """
        self._cache = {} if use_cache else None
        self._galvanic_data: Optional[pd.DataFrame] = None
        self._kittycad_stainless: Optional[Dict] = None
        self._kittycad_nickel: Optional[Dict] = None
        self._loaded = False

    def _lazy_load(self):
        """Lazy load data on first access"""
        if self._loaded:
            return

        logger.info("Loading authoritative material data from GitHub sources...")

        try:
            # Load USNRL galvanic series
            logger.info(f"Loading USNRL galvanic series from {self.USNRL_GALVANIC_URL}")
            self._galvanic_data = pd.read_xml(self.USNRL_GALVANIC_URL)
            logger.info(f"Loaded {len(self._galvanic_data)} galvanic series entries")

        except Exception as e:
            logger.warning(f"Failed to load USNRL galvanic data: {e}")
            logger.info("Will use fallback hard-coded values")
            self._galvanic_data = None

        try:
            # Load KittyCAD stainless steel data
            logger.info(f"Loading KittyCAD stainless data from {self.KITTYCAD_STAINLESS_URL}")
            response = requests.get(self.KITTYCAD_STAINLESS_URL)
            response.raise_for_status()
            self._kittycad_stainless = response.json()
            logger.info(f"Loaded KittyCAD stainless steel data")

        except Exception as e:
            logger.warning(f"Failed to load KittyCAD stainless data: {e}")
            self._kittycad_stainless = None

        try:
            # Load KittyCAD nickel alloys data
            logger.info(f"Loading KittyCAD nickel data from {self.KITTYCAD_NICKEL_URL}")
            response = requests.get(self.KITTYCAD_NICKEL_URL)
            response.raise_for_status()
            self._kittycad_nickel = response.json()
            logger.info(f"Loaded KittyCAD nickel alloy data")

        except Exception as e:
            logger.warning(f"Failed to load KittyCAD nickel data: {e}")
            self._kittycad_nickel = None

        self._loaded = True
        logger.info("Material database initialization complete")

    def get_material_properties(self, material_id: str) -> Dict[str, Any]:
        """Fail closed; the merged property payload is withdrawn."""
        del material_id
        raise MaterialPropertiesAccessorSuppressedError()

    def calculate_pren(self, material_id: str) -> Optional[float]:
        """
        Calculate PREN = %Cr + 3.3*%Mo + 16*%N

        PREN (Pitting Resistance Equivalent Number) is a measure of
        pitting corrosion resistance in stainless steels.
        """
        composition = self._get_composition(material_id)
        if not composition:
            return None

        try:
            Cr = composition.get("Cr", 0)
            Mo = composition.get("Mo", 0)
            N = composition.get("N", 0)

            pren = Cr + 3.3 * Mo + 16 * N
            return pren

        except Exception as e:
            logger.warning(f"Failed to calculate PREN for {material_id}: {e}")
            return None

    def estimate_cpt(self, material_id: str) -> Optional[float]:
        """Fail closed; no source validates the former PREN-minus-ten rule."""
        del material_id
        raise UnsourcedCPTEstimateSuppressedError()

    def _get_galvanic_potential(self, material_id: str) -> Optional[float]:
        """Get galvanic potential from USNRL data"""
        if self._galvanic_data is None:
            # Fallback to hard-coded values
            return self._fallback_galvanic_potential(material_id)

        # Map material_id to USNRL material names
        # USNRL XML contains alloy names like "Carbon Steel", "Stainless Steel 316", etc.
        usnrl_mapping = {
            "CS": ["Carbon Steel", "Mild Steel"],
            "316L": ["Stainless Steel 316", "SS316", "316L"],
            "304": ["Stainless Steel 304", "SS304", "304"],
            "duplex_2205": ["Duplex 2205", "2205"],
            "C276": ["Hastelloy C-276", "C276", "Alloy C-276"],
            "titanium_gr2": ["Titanium", "Titanium Grade 2"],
        }

        search_names = usnrl_mapping.get(material_id, [])

        try:
            # Search galvanic data for matching material
            for search_name in search_names:
                # Case-insensitive search in material column (assuming 'Material' or 'Alloy' column exists)
                if 'Material' in self._galvanic_data.columns:
                    matches = self._galvanic_data[
                        self._galvanic_data['Material'].str.contains(search_name, case=False, na=False)
                    ]
                elif 'Alloy' in self._galvanic_data.columns:
                    matches = self._galvanic_data[
                        self._galvanic_data['Alloy'].str.contains(search_name, case=False, na=False)
                    ]
                else:
                    # If column names unknown, use fallback
                    return self._fallback_galvanic_potential(material_id)

                if not matches.empty:
                    # Extract potential value (assuming 'Potential' or 'E_SCE' column)
                    if 'Potential' in matches.columns:
                        return float(matches.iloc[0]['Potential'])
                    elif 'E_SCE' in matches.columns:
                        return float(matches.iloc[0]['E_SCE'])
                    elif 'Voltage' in matches.columns:
                        return float(matches.iloc[0]['Voltage'])

        except Exception as e:
            logger.warning(f"Failed to parse USNRL galvanic data for {material_id}: {e}")

        # Fallback to hard-coded values if parsing fails
        return self._fallback_galvanic_potential(material_id)

    def _fallback_galvanic_potential(self, material_id: str) -> Optional[float]:
        """Fallback galvanic potentials (V vs SCE in seawater)"""
        fallback = {
            "CS": -0.6,
            "316L": -0.1,
            "304": -0.15,
            "duplex_2205": -0.05,
            "super_duplex": 0.0,
            "alloy_20": -0.05,
            "C276": 0.1,
            "titanium_gr2": -0.05,
        }
        return fallback.get(material_id)

    def _get_kittycad_properties(self, material_id: str) -> Dict[str, Any]:
        """Get mechanical properties from KittyCAD data"""
        props = {}

        # Map material_id to KittyCAD material names
        kittycad_mapping = {
            "316L": "316L",
            "304": "304",
            "duplex_2205": "2205",  # Duplex stainless
            "C276": "C276",  # Hastelloy (nickel alloys)
        }

        kittycad_name = kittycad_mapping.get(material_id)
        if not kittycad_name:
            return props

        # Try stainless steel data first
        if self._kittycad_stainless:
            material_data = self._kittycad_stainless.get(kittycad_name)
            if material_data:
                props["density_kg_m3"] = material_data.get("density")
                props["yield_strength_MPa"] = material_data.get("yield_strength")
                props["ultimate_tensile_strength_MPa"] = material_data.get("ultimate_tensile_strength")
                props["youngs_modulus_GPa"] = material_data.get("youngs_modulus")
                props["poisson_ratio"] = material_data.get("poisson_ratio")
                props["kittycad_source"] = "stainlesssteel.json"

        # Try nickel alloys data if not found in stainless
        if not props and self._kittycad_nickel:
            material_data = self._kittycad_nickel.get(kittycad_name)
            if material_data:
                props["density_kg_m3"] = material_data.get("density")
                props["yield_strength_MPa"] = material_data.get("yield_strength")
                props["ultimate_tensile_strength_MPa"] = material_data.get("ultimate_tensile_strength")
                props["youngs_modulus_GPa"] = material_data.get("youngs_modulus")
                props["poisson_ratio"] = material_data.get("poisson_ratio")
                props["kittycad_source"] = "nickelalloys.json"

        return props

    def _get_composition(self, material_id: str) -> Optional[Dict[str, float]]:
        """
        Get chemical composition (wt%) from authoritative CSV-backed database.

        Uses data.MATERIALS_DATABASE loaded from materials_compositions.csv
        (ASTM A240, B443, B152, etc.)
        """
        from corrosion_engineering_mcp.data import MATERIALS_DATABASE

        # Try to get material from CSV-backed database
        material_data = MATERIALS_DATABASE.get(material_id)
        if material_data:
            # Build composition dict from MaterialComposition dataclass
            composition = {}
            if material_data.Cr_wt_pct > 0:
                composition["Cr"] = material_data.Cr_wt_pct
            if material_data.Ni_wt_pct > 0:
                composition["Ni"] = material_data.Ni_wt_pct
            if material_data.Mo_wt_pct > 0:
                composition["Mo"] = material_data.Mo_wt_pct
            if material_data.N_wt_pct > 0:
                composition["N"] = material_data.N_wt_pct
            if material_data.Fe_bal:
                # Calculate Fe balance
                total_alloying = sum(composition.values())
                composition["Fe"] = max(0, 100.0 - total_alloying)

            return composition if composition else None

        return None

    def _get_cost_factor(self, material_id: str) -> Optional[float]:
        """
        Get cost factor relative to carbon steel.

        TODO: Load from industry sources or materials pricing APIs
        """
        cost_factors = {
            "CS": 1.0,
            "304": 2.5,
            "316L": 3.5,
            "duplex_2205": 5.0,
            "super_duplex": 8.0,
            "alloy_20": 10.0,
            "C276": 20.0,
            "titanium_gr2": 15.0,
        }

        return cost_factors.get(material_id)

    def _get_full_name(self, material_id: str) -> str:
        """Get full material name"""
        names = {
            "CS": "Carbon Steel",
            "316L": "316L Stainless Steel",
            "304": "304 Stainless Steel",
            "duplex_2205": "Duplex 2205",
            "super_duplex": "Super Duplex (SAF 2507)",
            "alloy_20": "Alloy 20 (Carpenter 20Cb-3)",
            "C276": "Hastelloy C-276",
            "titanium_gr2": "Titanium Grade 2",
        }

        return names.get(material_id, material_id)
