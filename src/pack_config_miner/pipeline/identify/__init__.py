"""Identification stage [3]. Tier 1 (vision-LLM) and Tier 2 (local CV) behind one interface.

``base.Identifier`` is the seam: ``identify(frame) -> list[Detection]``. No other stage knows
the concrete model. See ``plan.md`` section 8.
"""
