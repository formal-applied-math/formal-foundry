/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.FixedIncome.HazardCurve
public import MathFin.FixedIncome.Credit
public import MathFin.FixedIncome.CDSTimeVarying

-- pointers: MathFin/FixedIncome/HazardCurve.lean, MathFin/FixedIncome/Credit.lean, MathFin/FixedIncome/CDSTimeVarying.lean
-- main-module: MathFin/FixedIncome/CVA.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-credit-cva-unilateral
-- source-issue: 98
-- new-defs: {'name': 'cva', 'signature': '(EE : ℝ → ℝ) → (R r : ℝ) → (h : ℝ → ℝ) → (T : ℝ) → ℝ', 'meaning': 'Unilateral CVA: (1-R) times the integral over [0,T] of expected exposure times discount times default density h(u)·S(u).', 'built_from': ['MathFin.hazardSurvival', 'Real.exp', 'intervalIntegral notation ∫ u in a..b, f u']}

/-!
Unilateral CVA as a recovery-adjusted exposure integral against the hazard default density, with closed form for constant exposure and hazard.
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

/-- **Unilateral CVA**: `(1 - R)` times the integral over `[0, T]` of expected
exposure `EE u`, discounted at rate `r`, weighted by the risk-neutral
default-time density `h(u) · S(u)` (the same credit-triangle marginal used by
`cdsFairSpread`), where `S = hazardSurvival h` is the deterministic-hazard
survival curve. -/
noncomputable def cva (EE : ℝ → ℝ) (R r : ℝ) (h : ℝ → ℝ) (T : ℝ) : ℝ :=
  (1 - R) * ∫ u in (0:ℝ)..T, EE u * Real.exp (-(r * u)) * h u * hazardSurvival h u

example : cva (fun _ => (0:ℝ)) (0:ℝ) (0:ℝ) (fun _ => (0:ℝ)) (0:ℝ) = 0 := by
  unfold cva
  norm_num

example : cva (fun _ => (1:ℝ)) (0:ℝ) (0:ℝ) (fun _ => (0:ℝ)) (0:ℝ) = 0 := by
  unfold cva
  norm_num

/-- **Closed-form unilateral CVA under constant exposure and constant hazard**:
with hazard survival `hazardSurvival (fun _ => h₀) u = exp(-h₀ u)`, the CVA
integrand collapses to `E₀ · h₀ · exp(-(r + h₀) u)`, whose antiderivative on
`[0, T]` gives `(1 - R) · E₀ · (h₀ / (r + h₀)) · (1 - exp(-(r + h₀) T))`. The
hypothesis `r + h₀ ≠ 0` avoids the `0/0` degeneracy of `h₀ / (r + h₀)` under
Lean's division convention; at `r + h₀ = 0` the true integral value is
`E₀ · h₀ · T`. -/
theorem cva_const_const {E₀ R r h₀ T : ℝ} (hrh : r + h₀ ≠ 0) :
    cva (fun _ => E₀) R r (fun _ => h₀) T =
      (1 - R) * E₀ * (h₀ / (r + h₀)) * (1 - Real.exp (-(r + h₀) * T)) := by sorry

end MathFin
