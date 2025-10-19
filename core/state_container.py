"""
State container for persisting chemistry/geometry context across tool chains.

Design Philosophy (from Codex review):
- Introduce a state container that persists intermediate chemistry/geometry
  context across chained tool calls—right now each tool implicitly recomputes
  speciation from scratch, inflating latency at the Tier 2 Monte Carlo passes.

Benefits:
- Avoid redundant PHREEQC calculations
- Enable efficient tool chaining (e.g., speciation → multiple rate predictions)
- Support Monte Carlo sampling without recomputing chemistry each iteration
- Provide context for AI agents to understand multi-step workflows
"""

from typing import Any, Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json


@dataclass
class CachedSpeciation:
    """Cached speciation result with metadata"""
    result: Dict[str, Any]
    inputs: Dict[str, Any]
    timestamp: datetime
    backend: str  # "phreeqpython", "reaktoro", etc.
    cache_key: str


class CorrosionContext:
    """
    State container for corrosion engineering workflows.

    Maintains caches for:
    - Chemistry speciation results
    - Geometry/process conditions
    - Material properties
    - Intermediate calculation results

    Usage:
        context = CorrosionContext()

        # Store speciation result
        spec_key = context.cache_speciation(inputs, result, backend="phreeqpython")

        # Later: retrieve without recomputation
        cached = context.get_speciation(spec_key)
        if cached:
            print(f"pH = {cached.result['pH']}")
    """

    def __init__(self):
        """Initialize empty state container"""
        self._speciation_cache: Dict[str, CachedSpeciation] = {}
        self._geometry_cache: Dict[str, Dict[str, Any]] = {}
        self._material_cache: Dict[str, Dict[str, Any]] = {}
        self._metadata: Dict[str, Any] = {
            "created_at": datetime.now(),
            "tool_chain": [],
        }

    # ========================================================================
    # Speciation Cache
    # ========================================================================

    def cache_speciation(
        self,
        inputs: Dict[str, Any],
        result: Dict[str, Any],
        backend: str = "phreeqpython",
    ) -> str:
        """
        Cache a speciation result.

        Args:
            inputs: Speciation input parameters (T, P, water, gases, ions)
            result: Speciation result dictionary
            backend: Chemistry backend used

        Returns:
            Cache key (hash of inputs) for later retrieval
        """
        cache_key = self._compute_cache_key(inputs)

        self._speciation_cache[cache_key] = CachedSpeciation(
            result=result,
            inputs=inputs,
            timestamp=datetime.now(),
            backend=backend,
            cache_key=cache_key,
        )

        return cache_key

    def get_speciation(self, cache_key: str) -> Optional[CachedSpeciation]:
        """
        Retrieve cached speciation result.

        Args:
            cache_key: Key returned from cache_speciation()

        Returns:
            CachedSpeciation object, or None if not found
        """
        return self._speciation_cache.get(cache_key)

    def get_speciation_by_inputs(self, inputs: Dict[str, Any]) -> Optional[CachedSpeciation]:
        """
        Retrieve cached speciation by input parameters.

        Args:
            inputs: Speciation input parameters

        Returns:
            CachedSpeciation object, or None if not found
        """
        cache_key = self._compute_cache_key(inputs)
        return self.get_speciation(cache_key)

    def clear_speciation_cache(self):
        """Clear all cached speciation results"""
        self._speciation_cache.clear()

    # ========================================================================
    # Geometry/Process Conditions Cache
    # ========================================================================

    def cache_geometry(self, geometry_id: str, geometry: Dict[str, Any]):
        """
        Cache geometry/process conditions.

        Useful for multi-component systems where geometry is shared
        across multiple corrosion calculations.

        Args:
            geometry_id: Unique identifier (e.g., "pipe_section_A")
            geometry: Geometry parameters (d_m, L_m, thickness_mm, etc.)
        """
        self._geometry_cache[geometry_id] = {
            **geometry,
            "cached_at": datetime.now(),
        }

    def get_geometry(self, geometry_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached geometry"""
        return self._geometry_cache.get(geometry_id)

    def clear_geometry_cache(self):
        """Clear all cached geometry"""
        self._geometry_cache.clear()

    # ========================================================================
    # Material Properties Cache
    # ========================================================================

    def cache_material(self, material_id: str, properties: Dict[str, Any]):
        """
        Cache material properties.

        Args:
            material_id: Material identifier (e.g., "316L", "CS")
            properties: Material properties (composition, PREN, cost, etc.)
        """
        self._material_cache[material_id] = {
            **properties,
            "cached_at": datetime.now(),
        }

    def get_material(self, material_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached material properties"""
        return self._material_cache.get(material_id)

    def clear_material_cache(self):
        """Clear all cached material properties"""
        self._material_cache.clear()

    # ========================================================================
    # Tool Chain Tracking
    # ========================================================================

    def record_tool_call(self, tool_name: str, inputs: Dict[str, Any], outputs: Dict[str, Any]):
        """
        Record a tool call in the workflow chain.

        Useful for AI agents to understand multi-step workflows and
        for debugging/auditing.

        Args:
            tool_name: MCP tool name (e.g., "chem.speciation_phreeqc.run")
            inputs: Tool inputs
            outputs: Tool outputs
        """
        self._metadata["tool_chain"].append({
            "tool": tool_name,
            "timestamp": datetime.now(),
            "inputs_summary": self._summarize_inputs(inputs),
            "outputs_summary": self._summarize_outputs(outputs),
        })

    def get_tool_chain(self) -> list:
        """Get complete tool call chain"""
        return self._metadata["tool_chain"]

    # ========================================================================
    # Utilities
    # ========================================================================

    def _compute_cache_key(self, inputs: Dict[str, Any]) -> str:
        """
        Compute deterministic cache key from inputs.

        Uses SHA256 hash of sorted JSON to ensure consistent keys
        regardless of dict ordering.
        """
        # Sort keys for deterministic hashing
        sorted_inputs = json.dumps(inputs, sort_keys=True)
        return hashlib.sha256(sorted_inputs.encode()).hexdigest()[:16]

    def _summarize_inputs(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Create compact summary of inputs for logging"""
        # For now, just return subset of keys
        # Can be made more sophisticated later
        summary = {}
        for key, value in inputs.items():
            if isinstance(value, dict):
                summary[key] = f"<dict with {len(value)} keys>"
            elif isinstance(value, list):
                summary[key] = f"<list with {len(value)} items>"
            else:
                summary[key] = value
        return summary

    def _summarize_outputs(self, outputs: Dict[str, Any]) -> Dict[str, Any]:
        """Create compact summary of outputs for logging"""
        return self._summarize_inputs(outputs)

    def clear_all(self):
        """Clear all caches"""
        self.clear_speciation_cache()
        self.clear_geometry_cache()
        self.clear_material_cache()
        self._metadata["tool_chain"].clear()

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        return {
            "speciation_entries": len(self._speciation_cache),
            "geometry_entries": len(self._geometry_cache),
            "material_entries": len(self._material_cache),
            "tool_calls": len(self._metadata["tool_chain"]),
            "created_at": self._metadata["created_at"],
        }


# ============================================================================
# Global Context Instance (Optional)
# ============================================================================

# For simple use cases, provide a global context instance
# More complex applications should manage their own instances
_global_context = None


def get_global_context() -> CorrosionContext:
    """Get or create global context instance"""
    global _global_context
    if _global_context is None:
        _global_context = CorrosionContext()
    return _global_context


def reset_global_context():
    """Reset global context (useful for testing)"""
    global _global_context
    _global_context = None
