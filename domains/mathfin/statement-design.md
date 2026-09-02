── STATEMENT DESIGN (the drafter's job is a faithful, in-depth STATEMENT) ──
The hardest failures are statement failures, not proof failures. Before `:= by sorry`:
- Name objects ONLY from the declarations shown for THIS target. Never carry over an
  example constant from these instructions (do not reach for `{{exemplar}}` unless it
  appears in the shown declarations and the statement is about it).
- If the primitive the statement needs is absent from the shown declarations, take the
  DEFINITIONS route — do not invent a constant name.
- Shape hard side-conditions to be inherited, not asserted; state at the natural level
  of generality (`s.Nonempty` over a member-witness, `A ≠ 0` over provable positivity);
  casts go outward around lattice/arith ops.

