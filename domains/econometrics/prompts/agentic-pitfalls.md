COMMON PITFALLS under our pin (avoid these upfront; lean_diagnostics catches the rest):
- Apply every {{namespace}}/Mathlib definition to ALL its arguments (`{{exemplar_applied}}`, never `{{exemplar_partial}}`) — a partial application is a `… → …` where a value is expected.
- `autoImplicit false` is on: do NOT write an explicit universe (`Type u`) or a `universe` decl; use `Type*` / `Sort*`.
- For `ℝ≥0` add `open scoped NNReal` and write `ℝ≥0` (bare `ℝ ≥ 0` misparses as a Prop).
- A stuck typeclass metavariable (`?m…`): name the ambiguous implicit (`(μ := μ)`) or `@`-apply, so instance search is not left guessing.
- For an unknown identifier, use lean_loogle / lean_leansearch to find the real name + namespace before guessing; do not invent a constant.
