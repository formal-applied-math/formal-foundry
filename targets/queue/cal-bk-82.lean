/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.Actuarial.Insurance
public import MathFin.Actuarial.Mortality

-- pointers: MathFin/Actuarial/Insurance.lean, MathFin/Actuarial/Mortality.lean
-- main-module: MathFin/Actuarial/LifeAnnuityDue.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-actuarial-life-annuity-due
-- source-issue: 82

/-!
Survival-weighted discrete life annuity-due ä_x:n, with nonnegativity, monotone decrease in i, and reduction to the certain annuity when survival is identically 1.
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

/-- **Survival-weighted discrete life annuity-due** `ä_x:n` under force of
mortality `μ`, starting age `x`, annual effective interest rate `i`, and term
`n`: `∑_{k=0}^{n-1} v^k · ₖpₓ` where `v = 1/(1+i)` is the annual discount
factor and `ₖpₓ = S(x+k)/S(x)` is the `k`-year survival probability of a life
aged `x`, with `S := survivalFromForce μ`. -/
noncomputable def lifeAnnuityDue (μ : ℝ → ℝ) (x i : ℝ) (n : ℕ) : ℝ :=
  ∑ k ∈ Finset.range n, (1 / (1 + i)) ^ k *
    (survivalFromForce μ (x + (k : ℝ)) / survivalFromForce μ x)

example (μ : ℝ → ℝ) (x i : ℝ) : lifeAnnuityDue μ x i 0 = 0 := by
  simp [lifeAnnuityDue]

example (μ : ℝ → ℝ) (x i : ℝ) : lifeAnnuityDue μ x i 1 = 1 := by
  have hS : survivalFromForce μ x ≠ 0 := ne_of_gt (survivalFromForce_pos μ x)
  simp [lifeAnnuityDue, div_self hS]

/-- **Basic properties of the life annuity-due**: it is nonnegative whenever
`v = 1/(1+i) > 0`; it is antitone in the interest rate `i` on `(-1, ∞)`; and
when survival is identically `1` over the term it collapses to the certain
annuity closed form `(1 − v^n)/(1 − v)`. -/
theorem lifeAnnuityDue_basic_properties (μ : ℝ → ℝ) (x : ℝ) (n : ℕ) :
    (∀ i : ℝ, -1 < i → 0 ≤ lifeAnnuityDue μ x i n) ∧
    (∀ i₁ i₂ : ℝ, -1 < i₁ → -1 < i₂ → i₁ ≤ i₂ →
      lifeAnnuityDue μ x i₂ n ≤ lifeAnnuityDue μ x i₁ n) ∧
    (∀ i : ℝ, i ≠ 0 →
      (∀ k < n, survivalFromForce μ (x + (k : ℝ)) = survivalFromForce μ x) →
      lifeAnnuityDue μ x i n = (1 - (1 / (1 + i)) ^ n) / (1 - 1 / (1 + i))) := by sorry

end MathFin
