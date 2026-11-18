---
name: scientific-figure
description: Understand a reference or scientific description and redraw it as clean, independently editable SVG objects. Use the calling host's vision and reasoning with deterministic geometry/render/edit tools; no additional model API or GUI is required.
---

# Scientific Figure Reconstruction & Drawing

An independent, provider-neutral skill. The calling host decides what to draw; the engine executes declared geometry, paints, layers, edits and exports. Deliver SVG plus a PNG rendered from that SVG. Use Anaconda `ScientificFigure_skill` and the installed `figure-cli`.

## 0.9 guide: two tasks, one host and Scene

Current Engine 0.9.0 retains FigurePlan 0.1 and Scene 0.3. The 0.8 execution, editing and rendering base is preserved. The calling host is already the model doing the interpretation and visual review; do not add a second model or Agent. See the [complete 0.9 plan](docs/next-stage-plan-0.9.md), [0.8 capabilities](docs/drawing-workflow-0.8.md) and [semantic planning / new CLI workflow](docs/semantic-planning-workflow.md). The workflow page defines the implemented entry points and exact file contracts; [delivery notes](docs/delivery-0.9.0.md) record actual verification and limits.

Choose the task guide before constructing a complex figure:

- **Reference reconstruction:** read [reference-reconstruction](references/reference-reconstruction.md). Establish information structure and reading order, essential parts, relations and specific unknowns before geometry. Separate entities, process arrows, annotation leaders, magnified views, legends and decoration. Do not fill unreadable regions from topic knowledge.
- **Natural-language design:** read [text-design](references/text-design.md). Separate user-explicit content, design choices and scientific assumptions. Design the hierarchy around the requested mechanism; do not invent preparation, administration or experimental conditions.
- **Both tasks:** use [construction-selection](references/construction-selection.md) when the method is uncertain. Choose by required shape, parts, repetition, holes, occlusion and style, then compare actual available resources. A simple flat object can go straight from a short intent/unknown note to geometry and preview; no mandatory search or multiple planning files.

## Default: understand, select, construct, inspect, correct

1. State purpose, intended display size, modules and reading order. List objects, indispensable internal parts, editable units, labels, color meanings and relations. Keep unresolved content explicit. For a complex figure, make this draft inspectable before writing extensive geometry. Use existing FigurePlan modules/objects/relations where useful; unselected constructions can remain `unresolved`. These are stages of one planning input, not another Scene tree.
2. Ask only the local questions that affect a decision: for example, whether a pale area is an opening or a surface, or where a leader ends. Inspect the original and relevant crops; use source-bound evidence only as needed. Preserve ROI and coordinate mappings. Measurements and similarity scores are observations, not object identities or scientific confidence; there is no implicit OCR.
3. Choose recipe/asset/direct/program by structure and style. Check actual Recipe parameters and editable part keys or asset rights and import limits. The catalog has 15 Recipe families, two symbol-family metadata entries and one Twemoji lemon asset; do not invent mouse/flask/avatar Recipes. If a Recipe omits required parts, reject that construction and fall back to a free program, optionally combining suitable subparts. Use local coordinates, curves, transforms, existing drawing functions and mature libraries. Direct Scene, legacy semantic Plan 0.3 and `SceneBuilder` remain valid; do not require a large handwritten JSON before drawing.
4. Build main structure and required internals first, then connectors and labels. Preserve stable object/part IDs. Use filled surfaces, strokes, true holes, bounded masks/occlusion, repetition and gradients as appropriate; do not add volume lighting to flat art. The host explicitly runs programs through ordinary tools and supplies generated Scene fragments; check/compile and project loading do not run saved source. Keep new candidates separate from an edited project and use B/U/C merging before acceptance.
5. Read the actual `preview.png` at the intended display size and inspect key enlarged regions. Check modules and connections → silhouette/proportions/holes → necessary internal structure → colors/layers → text. Compare the reference at the same scale; when replacing an earlier drawing, compare reference/old/new together. A valid plan, named path or passing test cannot establish that the intended structure was drawn correctly.
6. Tie each finding to an ID and a concrete action: change construction method, replace a candidate, adjust geometry/parameters, obtain local evidence or retain uncertainty. Patch against the current revision or regenerate a candidate and merge; render and inspect again. Default at most three targeted correction rounds. Preserve accepted objects and user edits. Bind visual assessment to the **current** Scene/preview; changed geometry, resources or rendering expires old assessments. Report omissions and unexecuted reviews. Scientific review remains separate and unexecuted when not requested.

Success means a well-drawn, structurally clear figure with independently editable major parts. Tests establish engineering behavior, not drawing quality. Independent-host ablations, held-out benchmark campaigns, human panels, domain certification, runtime integration, cross-version template migration and experimental offset/variable-width development are outside the current work scope. Do not turn them into prerequisites or unsolicited next tasks. Report actual reuse separately as external artwork, drawing-code reuse and geometry/rendering-library reuse; having an asset in the Registry does not mean this drawing used it.

The host may write Python or JavaScript drawing programs. Use ordinary variables, functions, local coordinates, transforms, repetition and mature geometry libraries to calculate shapes; use stable engine helpers when available. A figure-specific drawing program is allowed; do not turn a few reference figures into topic-specific engine algorithms or treat example plans as a scientific template library. Send generated Scene/Plan candidates through engine validation, rendering and project transactions. When rerunning against an edited project, compare the last generated baseline, the current Scene and the new candidate before accepting changes; preserve user edits and report conflicts. Use the available [construction helpers](docs/construction-tools.md) and [generation protocol](docs/generation-and-effects.md): `generation diff PROJECT --candidate SOURCE --output DIFF`, inspect the proposed preview and conflicts, then `generation accept PROJECT --candidate DIFF --base-revision N`. Saved source is inert; accept recomputes the merge and checks current revision/content/receipt. Never overwrite an edited project with pure script output. Do not embed raster images, trace every texture into tiny paths, or replace missing structure with an attractive fill.

Choose between reconstruction from the supplied reference and permitted resource reuse before drawing. Disclose separately: external artwork (source, license, version/hash and modifications); reused drawing code, including your own functions (file/version, reused parts and changes); and geometry/rendering libraries (actual library/version and role). A locally written function is not an external asset, and a library call is not borrowed artwork. Record method inspiration separately from copied code; copied external code needs its fixed source revision, files, license and notices. For an explicitly frozen raster comparison, never substitute an official source SVG. Inspect main structure, geometry/holes/occlusion, then style/text in actual previews within the same correction budget. Background-colored cover patches do not establish real holes or editable interlacing. Program sources are reproducibility inputs; the accepted Scene is the only current editable authority. Opening a project or bundle must not execute a drawing program.

## Execute with files, without a model service

```bash
conda run -n ScientificFigure_skill figure-cli doctor --json
```

Use `inspect reference.png` for source size/hash. Source-bound plans declare SHA-256, size and ROI. Legacy Plan 0.3 semantic execution does not implicitly run color segmentation, pixel candidate search or skeleton analysis. Old Plan 0.1/0.2 inputs retain their behavior.

```bash
conda run -n ScientificFigure_skill figure-cli reconstruct reference.png --plan plan.json --output work/figure
```

For drawing from a scientific description, supply a semantic Plan without `source`, or a normal Scene:

```bash
conda run -n ScientificFigure_skill figure-cli draw plan.json --output work/figure
```

See [semantic contracts](docs/semantic-drawing.md) and [drawing rules](references/drawing-policy.md). Schemas are packaged under `src/scientific_figure/schemas/`. `construct` requires actual geometry; a natural-language object name alone is not executable. Scene remains the only authoritative edit source. Text stays text, compound paths preserve holes, and group opacity composites once. Use `SCIENTIFIC_FIGURE_FONT` for a compatible installed TTF/OTF; missing glyphs are reported.

## Real host review

```bash
conda run -n ScientificFigure_skill figure-cli review work/figure --json
```

Read `host_review.binding`. After inspecting that preview, write a host-review record using that exact binding and the declared schema, then submit:

```bash
conda run -n ScientificFigure_skill figure-cli review work/figure --record host-review.json --json
```

Keep mechanical checks, host visual assessment and scientific assessment separate. The engine verifies the record's binding; it cannot prove that the host looked at the image or knows the science. A successful command or mechanical pass is not publication quality. Hosts unable to read images must leave visual review unexecuted and deliver the preview for review.

Findings name `object_id`, `issue`, `evidence`, `action` and `remaining_uncertainty`. Use `$scene` for a global finding. A changed Scene, source binding, preview, render scale or render configuration cannot inherit approval of an earlier artifact.

## Edit and export

```bash
conda run -n ScientificFigure_skill figure-cli edit work/figure --patch patch.json
```

Old ID set patches and Patch 0.2 structural patches remain supported. Patch 0.3 adds/updates/removes linear-gradient resources in the same transaction as geometry/reference changes. Patch 0.4 provides exact object/resource records, clearing optional fields, reparenting, explicit order and Scene0.2 relation updates; validate the whole final closure. Updating a shared resource affects all users; to change one object, add a new resource ID and change that object's fill reference. Dangling references, conflicts and invalid final scenes are rejected atomically.

```bash
conda run -n ScientificFigure_skill figure-cli export work/figure --objects leaf --format svg --output work/leaf.svg
```

Export retains ancestor transforms, group opacity and referenced paint/clip/mask resources. Selected static export bakes current local occlusions and freezes connectors; keep the full project/Bundle for live relation editing. SVG can be edited externally. Controlled `import-svg INPUT --output NEW` accepts only the documented [subset](docs/svg-import.md), after checking source rights/version/SHA and recording normalization; it is not arbitrary SVG round-trip editing.

## Shared evidence, context and portable bundles

First distinguish one asset, several assets, and a composed figure. For several assets, list members and their source ROIs before drawing; reconstruct each as its own complete editable project. For a composed figure, the host decides placements, labels, containers and scientific connections, then supplies the composition plan.

Use the optional CPU evidence command when the original is hard to inspect:

```bash
conda run -n ScientificFigure_skill figure-cli evidence reference.png --channels contrast --output work/evidence
```

`--roi x,y,width,height` restricts work. `detail,palette` are explicit extra channels. Transparent ROIs require `--background '#ffffff'` or the actual known background. Read the original and `evidence_board.png`; edges/palette are observations, never semantic objects or calibrated confidence. The source is read-only. Budgets and explicit source/ROI/board mappings are documented in [evidence contracts](docs/evidence-and-context.md).

`prepare request.json --output work/prepared` accepts a declared single_asset/asset_bundle task and input list without a model. It reports `not_integrated`: do not claim execution before the host's first inference unless that runtime has actually been wired and tested.

Attach optional strict context with `draw` or `reconstruct --context context.json`. It binds roles, representations, uncertainties, evidence and profile/text/layout documents to Scene IDs. Token bindings resolve supported styles explicitly at initial build. Scene remains authoritative; context snapshots survive cloning and source-file removal. `style-check PROJECT` reports mechanical mismatches; it cannot judge scientific meaning or visual style quality. `context attach PROJECT --context FILE --base-revision N [--output NEW]` updates intent without repainting and expires old review bindings. Scene0.3 adds radial gradients and numeric dash styles. Explicit `text measure/wrap` returns real font metrics or a wrapping proposal without changing Scene; rich text, font-weight/fallback and automatic layout remain unsupported. Use `consume PREPARED --views source.png,evidence_board.png` to read verified prepared file content; it proves local tool consumption only, while real runtime preflight stays not_integrated.

Create complete-member immutable snapshots:

```bash
conda run -n ScientificFigure_skill figure-cli bundle create bundle-plan.json --output work/assets
```

```bash
conda run -n ScientificFigure_skill figure-cli bundle validate work/assets
```

Members retain Scene, text, gradients, local IDs and receipts; Scene0.2 also retains vector clip/alpha mask and local relation records, and optional generation input/baseline data stays inert. Fonts are recorded as family/SHA requirements; never redistribute system fonts. Editing a bundled member invalidates the bundle instead of silently updating its root hashes. Create a new bundle from revised projects.

Compose through Scene import and explicit transforms, with separate instance IDs and fresh review:

```bash
conda run -n ScientificFigure_skill figure-cli compose composition-plan.json --bundle work/assets --output work/composed
```

Internal and cross-instance connectors stay live. Painted asset backgrounds require an explicit preserve/omit policy. Edit the instance wrapper transform to move it; consult the recorded ID map for individual parts. The selected font must match the recorded SHA. Static selected-object export freezes connector geometry; retain the full composition project for subsequent live editing. See [bundle/composition contracts](docs/bundles-and-composition.md) and the source-free runnable example:

```bash
conda run -n ScientificFigure_skill python -I scripts/demo_composition.py --output work/composition-demo
```

`fit points.json --output fit.json` is a thin wrapper for host-selected sample points; it does not read images, infer objects or guarantee topology. Every host-authored correction still requires actual preview inspection. Six development cases are not a held-out evaluation, and identical-geometry evidence transport controls do not establish perceptual benefits.

## Optional pixel reconstruction, explicitly requested

Use pixel reconstruction only when local geometry evidence is useful, or the user explicitly requests tracing. Specify ROI, purpose, background/alpha assumptions and protected structures. Do not use full-image segmentation as the default object decomposition.

Plan 0.2 or Plan 0.3 pixel mode retains the bounded automatic methods and their stricter source constraints; read [adaptive reconstruction](docs/adaptive-reconstruction.md) and [internal-structure limits](docs/internal-structure.md). `region_bands` can preserve thin filled regions; `stroke_layers` is an optional dark-ridge hypothesis. Neither identifies scientific roles. Source-edge coverage is contrast coverage, not scientific completeness. `fit_stroke` recovers only a simple straight line; explicit curved paths are already supported.

The Python evidence API permits contrast-only inspection without ridge/skeleton work. Legacy `analyze()` remains compatible. Do not route a semantic correction through pixel `repair auto` unless the target is actually a source-bound pixel reconstruction object. Use ID patches for constructed objects.

Inkvec/VTracer are researched optional geometry baselines, not installed mandatory cores or semantic interpreters. No SAM, YOLO, model training, provider SDK, extra model service, GUI or CitrusScholar integration is needed. Current backends use CPU; explicit GPU requests report unsupported.

## Deliver and state limits

Deliver `figure.svg`, `preview.png`, `scene.json`, derived `objects.json`, `qa.json`, `review.json` and byte-bound `receipt.json`; add source plan/comparison when applicable. Report meaningful objects separately from paths/control points, actual edit/export checks, host interventions and unresolved structure. Four previously seen examples are development evidence, not a generalization benchmark. Keep private references, task-specific source and derived projects in local private packages, separate from public source/examples; never redistribute system fonts, environments or weights. SVG must contain no embedded raster, scripts or external links; PNG comes from that same SVG. This project is independent of QAagent: do not initialize Git, submit/push, start/deploy services or change network/security settings as part of drawing work. `python -I` is isolation from some import paths, not a security sandbox.
