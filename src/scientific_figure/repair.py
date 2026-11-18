"""Directed, bounded repair via a revision-checked subtree transaction."""
from __future__ import annotations
import copy

from .analysis import analyze, bind_analysis
from .candidate_search import SearchBudget, search
from .editing import apply_patch
from .evaluation import evaluate, fingerprint, profile_values
from .scene import validate


ACTIONS = {"restore_internal_regions": ["color_layers", "region_bands"],
           "restore_internal_boundaries": ["region_bands", "stroke_layers"],
           "restore_topology": ["region_bands", "color_layers", "flat_contour"],
           "reduce_fragmentation": ["primitive_fit", "region_bands"]}


def repair_scene(scene, path, object_id, request):
    validate(scene)
    if (not isinstance(request, dict) or set(request)-{"action", "parts", "max_rounds", "budget"}
            or request.get("action") not in {*ACTIONS,"auto"}):
        raise ValueError("repair requires a supported action")
    rounds = request.get("max_rounds", 2)
    if type(rounds) is not int or not 1 <= rounds <= 3:
        raise ValueError("repair rounds must be in [1,3]")
    provenance = scene.get("provenance", {})
    context = provenance.get("adaptive_context")
    steps = provenance.get("steps", [])
    step = next((s for s in steps if s.get("candidates", {}).get("object_id") == object_id), None)
    if context is None or step is None:
        raise ValueError("needs_input: repair needs a source-bound adaptive object; reconstruct with Plan 0.2 first")
    original_evidence = step["analysis"]
    bind_analysis(path, original_evidence)
    budget = SearchBudget(request.get("budget"))
    profile = profile_values(context["profile"])
    before = evaluate(path, context["analysis"], scene, profile=profile,budget=budget)
    history = []
    seen = set()
    for round_no in range(rounds):
        if not budget.available():
            break
        parts = request.get("parts", original_evidence["parts"])
        evidence = analyze(path, original_evidence["source"]["region"],
                           colors=min(16, original_evidence["configuration"]["colors"]*(round_no+1)),
                           background=original_evidence["background"], parts=parts)
        from .lifecycle import descendants
        keep=descendants(scene,object_id)
        local_objects=copy.deepcopy([o for o in scene["objects"] if o["id"] in keep])
        for node in local_objects:
            if node["id"]==object_id:
                node.pop("parent",None);node.pop("transform",None)
        local={"schema_version":"0.1","revision":scene["revision"],
               "canvas":{"width":evidence["source"]["region"][2],"height":evidence["source"]["region"][3],"background":"none"},
               "objects":local_objects,"resources":copy.deepcopy(scene.get("resources",[]))}
        try:
            local_before=evaluate(path,evidence,local,profile=profile,budget=budget)
        except ValueError:
            break
        action=request["action"]
        if action=="auto":
            reasons=set(local_before["reasons"])
            if "internal_boundary_missing" in reasons:
                action="restore_internal_boundaries"
            elif reasons & {"internal_region_missing_or_merged","required_part_not_addressable","part_edit_affects_wrong_region"}:
                action="restore_internal_regions"
            elif "visible_topology_changed" in reasons:action="restore_topology"
            elif "complexity_budget" in reasons:action="reduce_fragmentation"
            else:
                history.append({"round":round_no+1,"status":"stopped","reason":"no_supported_verified_failure","target_before":local_before})
                break
        replacement, candidates = search(path, evidence, object_id=object_id,
                                          methods=ACTIONS[action], profile=profile, budget=budget)
        key = fingerprint(replacement)
        if key in seen:
            history.append({"round": round_no+1, "status": "stopped", "reason": "repeated_state"})
            break
        seen.add(key)
        entry = {"round": round_no+1, "action": action,"requested_action":request["action"], "candidates": candidates}
        history.append(entry)
        if candidates["selection"]["status"] != "selected":
            entry.update(status="rejected", reason="no_feasible_local_candidate")
            continue
        selected=next(r for r in candidates["records"] if r["candidate_id"]==candidates["selection"]["candidate_id"])
        local_after=selected["audit"]
        if action=="reduce_fragmentation":
            improved=(local_after["C"]["draw_nodes"]<local_before["C"]["draw_nodes"]
                      or local_after["C"]["segments"]<local_before["C"]["segments"])
        elif action=="restore_internal_boundaries":
            improved=(local_after["F"]["internal_boundary"]["coverage"]
                      > local_before["F"]["internal_boundary"]["coverage"]+1e-6)
        elif action=="restore_topology":
            improved=not local_before["T"]["silhouette_equal"] and local_after["T"]["silhouette_equal"]
        else:
            def missing(audit):
                return sum(r["constraint_applied"] and r["iou"]<profile["min_region_iou"] for r in audit["T"]["regions"])+sum(
                    t["required"] and not t["task_passed"] for t in audit["E"]["tasks"])
            improved=(missing(local_after)<missing(local_before)
                      or local_after["F"]["source_color_mae"]+1e-6<local_before["F"]["source_color_mae"])
        entry["target_before"]=local_before
        entry["target_after"]=local_after
        if not improved:
            entry.update(status="rejected",reason="no_improvement_for_requested_failure")
            continue
        patch = {"schema_version": "0.2", "base_revision": scene["revision"], "operations": [
            {"op": "replace_subtree", "id": object_id, "objects": replacement["objects"]}]}
        try:
            trial = apply_patch(scene, patch)
        except ValueError as error:
            entry.update(status="rejected", reason=str(error))
            continue
        try:
            after = evaluate(path, context["analysis"], trial, profile=profile,budget=budget)
        except ValueError as error:
            entry.update(status="rejected",reason=str(error))
            break
        entry["context_audit"] = after
        # A local win must not degrade the full visible image beyond the frozen slack.
        if after["status"] != "passed" or after["F"]["source_color_mae"] > before["F"]["source_color_mae"]+profile["appearance_slack"]:
            entry.update(status="rejected", reason="context_failed_or_regressed")
            continue
        entry["status"] = "accepted"
        trial["provenance"]["adaptive_context"]["audit"] = after
        for s in trial["provenance"]["steps"]:
            if s.get("candidates", {}).get("object_id") == object_id:
                s["analysis"] = evidence
                s["candidates"] = candidates
                s["status"] = "selected"
        report = {"schema_version": "0.2", "status": "repaired", "object_id": object_id,
                  "base_revision": scene["revision"], "revision": trial["revision"],
                  "before": before, "after": after, "history": history, "budget": budget.report(),
                  "scientific_review": "host_required"}
        trial["provenance"].setdefault("repairs", []).append(report)
        return trial, report
    return copy.deepcopy(scene), {"schema_version": "0.2", "status": "needs_review", "object_id": object_id,
                                 "base_revision": scene["revision"], "revision": scene["revision"],
                                 "before": before, "history": history, "budget": budget.report(),
                                 "reason": "no_verified_repair; original scene retained"}
