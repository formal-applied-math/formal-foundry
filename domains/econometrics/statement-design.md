── STATEMENT DESIGN (the drafter's job is a faithful, in-depth STATEMENT) ──
The hardest failures are statement failures, not proof failures. Before `:= by sorry`:
- Name objects ONLY from the declarations shown for THIS target. Never carry over an
  example constant from these instructions (do not reach for `{{exemplar}}` unless it
  appears in the shown declarations and the statement is about it).
- If the primitive the statement needs is absent from the shown declarations, take the
  DEFINITIONS route — do not invent a constant name.
- An IDENTIFICATION statement equates an estimand (a function of `Model`) with an
  estimator (a function of `Observed`). If your statement has no such equation, it is
  probably not the identification claim the issue asked for.
- Shape hard side-conditions to be inherited, not asserted; state at the natural level
  of generality (`s.Nonempty` over a member-witness, measurability of the treated set
  over blanket regularity on `μ`); casts go outward around lattice/arith ops.
- Do NOT assume `IsProbabilityMeasure`/`IsFiniteMeasure` or positivity of a
  conditioning event's measure by reflex. `cond` degenerates to the zero measure on a
  null event and the algebra survives it — an unnecessary guard makes the theorem
  strictly weaker, which is the failure this pipeline actually commits.
