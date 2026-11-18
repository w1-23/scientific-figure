# Quality review record — citrus quality-assessment schematic

**Task route:** natural language only. No reference image was supplied, so no
Evidence pack, ROI mapping, constraint bindings or fidelity checks apply. The
host wrote the module/object plan and the geometry program (`draw.py`); the
engine compiled it into Scene 0.3 and rendered SVG/PNG from the same source.

**Artifacts**

| File | What it is |
|---|---|
| `citrus-quality.svg` | final editable vector, real `<text>`, no raster, no script, no external link |
| `citrus-quality.png` | preview rendered from the same SVG at scale 2 |
| `citrus-quality.scene.json` | Scene project (authoritative geometry, style, text, groups) |
| `draw.py` | the reproducible program that produced all of the above |
| `OBJECTS.md` | object-ID inventory generated from the project |

**Mechanical results** (engine 0.9.0, canvas 1400 x 1040)

- 365 objects: 124 paths, 70 real text objects, groups, ellipses and one rect
  background panel; `vector_only = true`.
- `geometry_validation = passed`; every object lies inside the canvas.
- `drawing_review = needs_review` for two declared reasons, kept rather than
  suppressed:
  - 6 `thin_stroke` flags — the leaf vein hairlines (0.6 px at final size).
    Reviewed at final size: they are deliberate texture, not structural lines.
  - 1 `global_color_budget` flag — the figure uses more fills than the default
    budget of 12. Roles are intentional and listed below; nothing was recolored
    automatically.
- `scientific_review = not_executed`. No agronomist or analytical-chemistry
  review was performed.

**Visual checks performed**

Full figure plus three local regions were rendered and read at final size:
module B (cutaway, beam, probe, labels), module C (bench, instruments, data
sheet, chips), module D (histogram, mean line, qualified-proportion ring).
Checks covered label/arrow collisions, text clipping at panel edges, connector
attachment, and reading order A -> B -> C -> D.

Defects found and fixed in this review round:

1. `Peel · albedo · segments` ran under the module D panel; split into two
   shorter lines.
2. Module C had no data sheet and no slice, both named in the brief; added a
   ruled data sheet and a slice on a sample dish, and captioned the bench items.
3. The C -> D connector started in empty space; rerouted from the metric chips
   to the calibration/validation strip and relabelled it `reference assays`.
4. The balance display invented a reading (`0.00 g`); replaced with `— g`.
5. Batch axes carried no mean marker; added a `mean` rule and kept
   `fruit-to-fruit variation` and `qualified proportion`.
6. The whole fruit had no visual link to its cutaway; added a short arrow.
7. Bold text stroked below 0.6 px produced 29 false legibility flags; the bold
   stroke is now floored at 0.6 px, which also reads better at final size.

**No invented claims**

- No R², RMSE, accuracy, wavelength, band count, instrument model or measured
  value appears anywhere; the figure says so in the header.
- Batch counts and shares are labelled illustrative and require measured data.
- Individual-fruit bars are ordinal indicators, not values; the card says
  "Predicted per fruit; no numeric claims".
- The data sheet is ruled, not filled with fabricated assay results.

**Palette roles**

`#4f9a5b`/`#2c6e47` foliage; `#b8c94b` green, `#dfb13d` turning, `#ec8a2f` ripe
fruit (one maturity ramp, used for the same meaning everywhere); `#f4c66b`
peel, `#fbe7bf` albedo, `#f6a75a` flesh; `#30556b` instrument body; `#2b9fc0`
optical signal; `#2c6f9c` data/pipeline; `#2a9d8f` reference and validation.
Maturity colour is never reused for a non-maturity meaning.

**Known limitations**

- The instrument, probe and beam are generic drawings; they do not depict a
  named commercial device or a specific optical geometry.
- Fruit anatomy is simplified: nine segments, three seeds, no vascular detail.
- Typography is host-authored English; no translation or OCR was involved.
- Layout is fixed at 1400 x 1040; the SVG scales, but text does not reflow.

**Reproduce**

```bash
python examples/citrus-quality/draw.py work/citrus --scale 2 --deliver examples/citrus-quality
```
