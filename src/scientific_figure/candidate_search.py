"""Bounded candidate execution and selection. Stores actual alternatives, not labels."""
from __future__ import annotations
import copy
import math
import time

from .analysis import analyze, bind_analysis
from .evaluation import evaluate, profile_values, select_candidate
from .scene import ID
from .strategies import METHODS, build_candidate


class SearchBudget:
    def __init__(self, options=None):
        options = options or {}
        if set(options)-{"max_candidates", "max_renders", "max_seconds"}:
            raise ValueError("unknown search budget")
        self.limits = {"max_candidates": 4, "max_renders": 12, "max_seconds": 30, **options}
        for key, value in self.limits.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 300:
                raise ValueError("invalid search budget")
            if key != "max_seconds" and type(value) is not int:
                raise ValueError("candidate/render budget must be integer")
        self.started = time.monotonic()
        self.candidates = 0
        self.renders = 0

    def available(self):
        return (self.candidates < self.limits["max_candidates"] and self.renders < self.limits["max_renders"]
                and time.monotonic()-self.started < self.limits["max_seconds"])

    def render(self):
        if self.renders >= self.limits["max_renders"] or time.monotonic()-self.started >= self.limits["max_seconds"]:
            raise ValueError("needs_input: shared render/time budget exhausted")
        self.renders += 1

    def report(self):
        return {"limits": self.limits, "candidate_count": self.candidates, "render_count": self.renders,
                "elapsed_seconds": round(time.monotonic()-self.started, 4),
                "time_limit_scope": "checked between atomic builds/renders; not a process timeout"}


def search(path, analysis, *, object_id="material", methods=None, profile=None, budget=None):
    if not isinstance(object_id, str) or not ID.fullmatch(object_id) or len(object_id) > 48:
        raise ValueError("adaptive object ID must be valid and <=48 characters")
    if methods is None:
        methods = list(METHODS[:3])
        if analysis.get("internal_structure", {}).get("internal_edge_pixels", 0) >= 12:
            methods.append("region_bands")
    if (not isinstance(methods, list) or not methods or len(methods) != len(set(methods))
            or any(m not in METHODS for m in methods)):
        raise ValueError("unknown/duplicate/empty candidate methods")
    config = profile_values(profile)
    budget = budget or SearchBudget()
    pixels = bind_analysis(path, analysis)
    records = []
    for method in methods:
        if not budget.available():
            records.append({"candidate_id": method, "method": method, "status": "not_executed", "reason": "shared_budget"})
            continue
        budget.candidates += 1
        # Fine color partition is a distinct source hypothesis, never the chosen mask as truth.
        evidence = analysis
        if method == "color_layers_fine":
            evidence = analyze(path, analysis["source"]["region"], colors=min(16, analysis["configuration"]["colors"]*2),
                               background=analysis["background"], parts=analysis["parts"])
        try:
            candidate = build_candidate(pixels, evidence, object_id, method)
            audit = evaluate(path, analysis, candidate, profile=config, budget=budget)
            records.append({"candidate_id": method, "method": method, "status": "executed",
                            "scene": candidate, "audit": audit})
        except ValueError as error:
            records.append({"candidate_id": method, "method": method, "status": "rejected", "reason": str(error)})
    chosen, decision = select_candidate(records, config)
    if chosen is None:
        executed = [r for r in records if "audit" in r]
        if not executed:
            raise ValueError("needs_input: no candidate executed successfully within budget")
        def incomplete_order(record):
            audit = record["audit"]
            missing = sum(t["required"] and not t["task_passed"] for t in audit["E"]["tasks"])
            return (missing, -audit["F"]["internal_boundary"]["coverage"],
                    audit["F"]["source_color_mae"], 1-audit["F"]["iou"],
                    audit["C"]["segments"], record["candidate_id"])
        chosen = min(executed, key=incomplete_order)
        decision["best_effort_id"] = chosen["candidate_id"]
        decision["best_effort_policy"] = "required edits, internal contrast coverage, color, outline, complexity; all failed constraints retained"
    report = {"schema_version": "0.2", "source": analysis["source"], "object_id": object_id,
              "records": records, "selection": decision, "budget": budget.report(),
              "quality_status": "needs_review", "scientific_review": "host_required"}
    return copy.deepcopy(chosen["scene"]), report
