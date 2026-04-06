"""
BigEd Fleet — Dimension Interaction Graph (12 dimensions).

IMPORTANT: These weights are INITIAL PRIORS based on fleet operations intuition.
They are NOT empirically calibrated. Before claiming statistical validity,
calibrate against historical audit outcomes. Each edge carries provenance="prior".

See Projects/_plans/ray_trace_plan.md for design decisions (D7).
"""

from scorerift.ray_trace import Interaction, build_graph

BIGED_EDGES = [
    Interaction("security", "reliability", 0.6, -1.0,
                "security breaches break reliability", "prior"),
    Interaction("testing", "performance", 0.4, 1.0,
                "tests validate perf claims", "prior"),
    Interaction("testing", "reliability", 0.5, 1.0,
                "tests catch reliability regressions", "prior"),
    Interaction("architecture", "code_quality", 0.5, 1.0,
                "good arch enables clean code", "prior"),
    Interaction("code_quality", "security", 0.3, 1.0,
                "clean code has fewer vulns", "prior"),
    Interaction("observability", "reliability", 0.4, 1.0,
                "monitoring catches issues early", "prior"),
    Interaction("documentation", "usability_ux", 0.3, 1.0,
                "docs improve UX", "prior"),
    Interaction("module_plugin", "architecture", 0.3, 1.0,
                "modularity signals good arch", "prior"),
    Interaction("data_hitl", "dynamic_abilities", 0.4, 1.0,
                "HITL data feeds ML capabilities", "prior"),
    Interaction("performance", "usability_ux", 0.4, 1.0,
                "fast system = good UX", "prior"),
    Interaction("reliability", "observability", 0.3, 1.0,
                "reliable system is observable", "prior"),
    Interaction("security", "code_quality", 0.3, -1.0,
                "security issues signal code quality gaps", "prior"),
]

BIGED_GRAPH = build_graph(BIGED_EDGES)
