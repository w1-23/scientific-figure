# Quality review record — xQTL prioritisation redraw

**Task route:** reference reconstruction. `reference.png` (926 x 1054) was read
panel by panel; the host transcribed the four panels, their labels, the species
legend and the numeric bubble values, then wrote `draw.py`. The engine executed
the geometry into Scene 0.3 and rendered SVG/PNG from the same source. No
reference-derived image was traced or embedded; the output is vector only.

**Artifacts**

| File | What it is |
|---|---|
| `redraw.svg` | editable vector, 165 real `<text>` objects, no raster or script |
| `redraw.png` | preview rendered from the same SVG (scale 2) |
| `redraw.scene.json` | Scene project |
| `draw.py` | reproducible program, including the `--deliver` export |
| `OBJECTS.md` | object-ID inventory generated from the project |
| `reference.png` | the supplied reference, kept only for side-by-side comparison |

**Transcription fidelity**

Preserved from the reference:

- Panel titles A–D and the four-panel reading order.
- The GWAS genotype table (4 accessions x 4 SNPs + trait, with `…` row), the
  model `Y = Xβ + Zb + ε`, the LD triangle and the Manhattan-style plot.
- Every timeline bubble value and species (4,892; 517; 199; 192; 163; 472; 287;
  355; 67; 442; 1,135; 7,887; 1,703; 17,599; 752; 3,724; 3,606; 314), the year
  axis, the species legend and the "Population sample size" key.
- Every method label in panel C: ChIP-seq, CAGE-seq, ATAC-seq, CUT&Tag,
  ONT/PacBio, WGBS, RNA-seq, ISO-seq, MeRIP-seq, Ribo-seq, MS/MS, LC/MS, GC/MS,
  WGS/WES, and the Promoter / Enhancer / Gene / Protein / DNA structure.
- All eight xQTL mini-panels with their captions (Gene expression, Chromatin
  accessibility, Alternative splicing, Protein abundance, CpG, Metabolites
  abundance, CRE) plus the Me/Ac marks.
- TWAS (Training → Imputation → Association), Colocalization (Trait 1,
  Causal GV, Trait 2), Summary-data-based Mendelian Randomization (Exposure,
  Trait), and the tool column: xQTL — RicePilaf, PoPS, MSC, SMR-Portal;
  Machine learning — FLAMES, EMS, Open Targets Platform, Iswine, QTG-LGBM,
  GARFIELD; Statistical scores — DEPICT, POCKET, MAGMA.

Deliberate simplifications (structure kept, micro-texture not copied):

- The reference's raster molecular art becomes vector shapes: nucleosomes,
  polymerase, ribosome, protein and nucleus are schematic, not textured.
- The Manhattan plot is a deterministic reconstruction of the reference's
  density style; it is not the original genotype data and carries no axis units.
- The LD triangle is a qualitative red/white cell grid, not the original matrix.
- Fonts are the host system's sans-serif; the reference's exact typeface and
  micro-typography are not reproduced.

**Mechanical results** (engine 0.9.0)

- 1,215 objects, 537 paths, 165 text objects, `vector_only = true`,
  `geometry_validation = passed`, nothing outside the canvas.
- `drawing_review = needs_review` with one declared issue: `global_color_budget`
  (the species palette exceeds the default 12-fill budget). Roles are the
  reference's own species colours; nothing was recoloured automatically.
- `scientific_review = not_executed`.

**Visual checks performed**

Full figure plus local regions (table/plot, timeline, molecular diagram,
mini-QTL block, integration block, tool column). Fixed during review: the table
footer collided with the model arrow; the polymerase covered the gene label; the
Ac mark crossed the assay box; the nascent-RNA cap sat on `MeRIP-seq`; the
timeline legend collided with rotated year labels; several bubbles covered year
labels; the Machine-learning box overflowed its text; the transfer arrows ran
into the tool column and the MR arrows were not attached to the causal variants.

**Known limitations**

- Bubble positions are data-driven (sqrt scaling) and approximate the
  reference's placement, not its pixel positions.
- Panel C keeps the reference's relationships but not its exact art direction.
- No value, tool name or method was added beyond what the reference shows.

**Reference rights**

The reference figure remains the property of its authors and publisher. It is
included here only as the comparison input for this reconstruction and is not
relicensed by this project. The redraw is a technical reconstruction, not a
republication of the original artwork.

**Reproduce**

```bash
python examples/genomics-prioritization/draw.py work/genomics --scale 3 --deliver examples/genomics-prioritization
```
