"""
Test suite for corrosion engineering MCP server.

Organization:
- test_plugin_contracts.py - Test abstract interfaces
- test_state_container.py - Test context caching
- test_handbook_lookup.py - Test Tier 0 tools
- test_chemistry.py - Test Tier 1 PHREEQC integration
- test_electrochemistry.py - Test Tier 2 corrosion models
- test_transport.py - Test mass transfer and coating models
- test_barriers.py - Test coating transport
- test_cui.py - Test CUI prediction
- test_mic.py - Test MIC risk assessment
- test_uncertainty.py - Test Monte Carlo UQ

Run with:
    pytest tests/
    pytest tests/ --cov=core --cov=tools --cov=utils
"""
