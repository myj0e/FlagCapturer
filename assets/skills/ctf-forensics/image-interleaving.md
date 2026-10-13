# Periodic image interleaving / 光栅与周期交织

## When this is a useful lead

Overlapping text, regularly missing columns/rows, periodic stripes or persistent OCR garbage can suggest interleaving. They can also come from scaling, halftoning, scan artifacts or ordinary noise. A binary image and a zsteg/file magic label alone do not identify the encoding. Inspect the actual image and compare alternative explanations.

Search `workflow_search` for `光栅 周期交织 raster grating interleaving overlapping text` and read `method/image-periodic-interleaving` when relevant. This is an optional probe, not a required image checklist.

## A small discriminating probe

For columns, inspect `image[:, phase::period]`; for rows use `image[phase::period, :]`. Search a finite range of periods and all phases on both axes. Compact sampling removes the other phases. A mask keeps native coordinates and covers the other phases, which can preserve glyph spacing. Do not assume the number of hidden messages or the correct axis in advance.

The captured helper `scripts/periodic_image.py` uses Pillow only. Check available capabilities before execution. Stage it using:

```json
{"path":"builtin/ctf-forensics/scripts/periodic_image.py"}
```

Pass this to `skill_materialize`; it returns a generated sandbox path. Use that path with sandbox `command_run`, declare the input image asset ID, and export the important contact sheets and manifest using the existing output capture mechanism. Example argv (replace both placeholders with actual sandbox paths):

```text
python3 HELPER IMAGE --output /work/interleaving --periods 2,3,4,5,6,7,8 --axes rows,cols
```

The manifest lists every tested period/phase/axis, source hash, image size and sheet position; phases outside a tiny image dimension are explicitly skipped. View the contact sheets with `image_view`. If a candidate is promising, produce its full resolution image with, for example:

```text
python3 HELPER IMAGE --output /work/selected --periods 3 --axes cols --phase 1 --save-candidates
```

Use `--mode mask` if compact sampling makes spacing ambiguous. Candidate previews are scaled; they are not sufficient evidence that a full-resolution candidate lacks fine detail. Inputs are limited to 8 million pixels and 32768 pixels per dimension; periods 2–16; at most 32 full-resolution candidates, with an aggregate 32 million pixel export limit. The helper never installs packages, runs OCR, ranks candidates or submits a flag. Inspect its source through `skill_read` if needed.

## Interpret results without overclaiming

- A coherent word, diagram or direction sequence emerging in separate phases supports interleaving; inspect native resolution and record its parameters and source asset.
- OCR is optional corroboration. Filtered readability, exit code zero, or merely saving many images does not establish a decoded message.
- No useful candidate excludes only the tested axes, periods, phases and representation. Preserve that scope instead of rejecting all interleaving.
- Changing one parameter can be useful; repeat a broad search only when the input, representation or coverage changes.
- If the output provides geometric/directional hints, follow those hints. `space-filling-curves.md` is a separate reference, not an automatic next step.

Background example: https://goodlunatic.github.io/posts/d0736a7/ (降维打击). This guide and helper are independently authored; they contain no challenge answer or mandatory solution chain.
