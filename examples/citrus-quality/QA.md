# Reproduce the citrus quality-assessment schematic

[PROMPT.md](PROMPT.md) gives the Chinese prompt used for this redraw and its English translation. The program builds the figure in Scene 0.3, exports the editable SVG, and renders the PNG from that SVG. The measurement records share the example fruit identifier `C-01`.

```bash
python examples/citrus-quality/draw.py work/citrus --scale 2 --deliver examples/citrus-quality
```

The project contains `figure.svg`, `preview.png`, and `scene.json`. The delivery directory also contains an [editable object inventory](OBJECTS.md).
