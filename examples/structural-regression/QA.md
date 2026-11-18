# Quality review record — cross-domain structural regression

**Purpose.** Four small figures from different fields, each paired with a faithful
and a degraded drawing, to check that reference evidence still gates the result:
a lost internal structure must show up as a failed binding instead of passing.

**Route.** Each case generates its own `reference.png`, builds a constraint pack
with `constraints build` (coarse / medium / fine), draws two Scene projects with
`SceneBuilder`, and runs `constraints check` with explicit bindings. No model
call, no OCR, no raster output.

**Results** (from `regression.json`)

| Case | Faithful drawing | Degraded drawing | What the degraded variant does |
|---|---|---|---|
| materials-layered | passed | failed | merges the six pores into one white band |
| chemistry-scheme | passed | failed | deletes the third ring and both bonds |
| physics-optics | passed | failed | draws the beam straight through the lens |
| soft-illustration | needs_review | — | no degraded variant; see below |

**Defects found while running the suite, fixed in `constraints.py`**

1. A binding whose object no longer exists raised and aborted the whole check.
   It now returns `failed` with the note `Binding object missing from the
   project`, so a deleted structure is reported like any other failure.
2. A hard binding failure produced `overall.status = needs_review`. Hard
   failures now make the pack `failed`; only soft/open items stay
   `needs_review`.
3. A region with no source edges scored `recall = 0.0`, which read as "the
   drawing missed everything". Empty evidence now reports `recall = null` and
   the binding stays `needs_review` with `No edge evidence in this region`.
   This matters for soft references: the suit must not invent hard constraints
   where the reference has none.

**Soft reference case.** The gradient blob produces no `strong_structural`
class at any scale (classes: `antialias`, `fine_stroke`,
`gradient_transition`), so the blurred boundary is left unconstrained rather
than being traced into paths. `needs_review` is the intended result here, not a
pass.

**Known limits**

- Synthetic 420 x 300 references with flat colours. They test wiring and
  detection, not visual quality or scientific correctness.
- The negative-space heuristic reports `needs_review` on stacked layers even
  when the holes are present; the materials case binds repetition and boundary
  instead, and that gap is recorded rather than papered over.
- `constraints check` compares edges and coarse region signatures. It does not
  verify colour gradients, text or meaning; `scientific_review` stays
  `not_executed`.

**Reproduce**

```bash
python examples/structural-regression/run.py work/regression --deliver examples/structural-regression
```
