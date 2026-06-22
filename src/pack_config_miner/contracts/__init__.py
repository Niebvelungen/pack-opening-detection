"""Versioned, ``pydantic``-validated data contracts — the only "API" of the miner.

These mirror the section 3 schemas in ``plan.md``. Every pipeline stage boundary parses and
serializes through these models. See M0 in ``implementation-plan.md``.
"""
