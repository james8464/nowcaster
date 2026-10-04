# Copy-readiness integrity implementation

1. Confirm current prospective evidence and keep the frozen collector source and study untouched.
2. Add failing tests for malformed or stale forward periods in `tests/unit/test_live_readiness.py`.
3. Add a fail-closed observation-integrity gate in `src/trading/readiness.py`; preserve the existing public receipt contract.
4. Document that the gate is necessary but insufficient for real-money copying, then verify and push the same branch.
