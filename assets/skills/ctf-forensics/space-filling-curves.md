# Image permutations and space-filling curves

Use only when observations suggest a one-dimensional traversal or geometric image permutation: recovered direction sequences, recursion hints, or a meaningful size constraint. A square image of side 2^n or 3^n is a compatibility clue, not proof of Hilbert or Peano encoding. Random-looking pixels alone are insufficient.

Search `workflow_search` for `空间填充曲线 Hilbert Peano pixel permutation` and read `method/image-space-filling`.

- Separate the traversal algorithm from its correspondence to the challenge. A continuous, bijective curve can still be the wrong decoding.
- Preserve the source image; choose the curve family, recursion order and orientation from evidence. State whether the input is a row-major stream being scattered along a curve or the reverse gathering operation.
- A small demonstrator can check coordinate range, uniqueness and pixel count. Those checks validate the implementation only.
- Test a bounded set of justified orientations/directions; inspect resulting structure, then use an independent reader if it resembles a QR or other standard format. A QR-like shape is not a decoded payload.
- Record the exact mapping, parameters, input asset and useful output. Failed variants exclude their tested mapping and orientation only.

This reference does not prescribe a curve implementation or a universal parameter sweep. Consult the relevant captured references or available sources when a variant is unclear; do not invent a curve merely to keep trying.

Background example: https://goodlunatic.github.io/posts/d0736a7/ (降维打击).
