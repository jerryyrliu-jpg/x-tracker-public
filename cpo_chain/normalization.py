"""Shared entity-name normalization primitives.

Single source of truth for placeholder-node filtering and company-name alias
merging, consumed by both cpo_chain/export_universal.py (the /chain runtime
cache) and scripts/update_network_html.py (the D3 dashboard graph). Kept
dependency-light (only re) so it is safely importable from either location.
"""

import re

PLACEHOLDER_NODES: set[str] = {
    "Customer C (全球領先光模組業者)",
    # Low-confidence (0.5), single-relation entity extracted from a
    # counter-drone order/customer tweet -- "Gen5" reads as a program/product
    # generation label, not a real company, and has no other corroborating
    # relations.
    "Gen5",
}

COMPANY_NAME_ALIASES: dict[str, str] = {
    "ASTS": "AST SpaceMobile",
    "FOCI Fiber Optic Communications": "FOCI",
    "Foci": "FOCI",
}


def base_company_name(name: str) -> str:
    """Strip a trailing ` (TICKER)` parenthetical from a company name."""
    return re.sub(r"\s+\([A-Z0-9.\-]+\)$", "", (name or "").strip())


def canonical_company_name(name: str, known_names: set[str] | list[str]) -> str:
    """Collapse alias / duplicate company-name variants to one canonical name.

    The prefix-shortening rule below picks the shortest known name that is a
    prefix of `name`. Candidates are sorted by `(len, name)`, not just `len`,
    so the result is deterministic even when two candidates tie in length —
    plain `set` iteration order depends on the process's hash seed and is not
    stable across runs.
    """
    base_name = base_company_name(name)
    if base_name in COMPANY_NAME_ALIASES:
        return COMPANY_NAME_ALIASES[base_name]
    lower_base = base_name.lower()
    for candidate in sorted(known_names, key=lambda c: (len(c), c)):
        if lower_base.startswith(candidate.lower() + " "):
            return candidate
    if base_name in known_names:
        return base_name
    return base_name


def canonical_alias_name(name: str) -> str:
    """Canonicalize a name using ONLY the explicit alias map plus ticker-suffix stripping.

    Unlike `canonical_company_name()`, this does not apply prefix-based
    shortening against a candidate pool. That heuristic was tuned for
    /chain's narrow, single-industry-context candidate pools; applied across
    an entire multi-context graph it can conflate genuinely distinct
    entities that happen to share a name prefix (e.g. a parent company and a
    separately-tracked subsidiary or facility row). Use this for merges that
    must hold across the whole graph, not just within one context.
    """
    base_name = base_company_name(name)
    return COMPANY_NAME_ALIASES.get(base_name, base_name)
