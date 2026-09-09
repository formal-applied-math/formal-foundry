/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.Actuarial.Insurance
public import MathFin.Actuarial.Mortality

set_option autoImplicit false

@[expose] public section

namespace MathFin

-- apply: annuityDue_closed_form, Finset.sum_congr, div_self, survivalFromForce_pos
theorem lifeAnnuityDue_eq_certain_annuity (μ : ℝ → ℝ) (x : ℝ) (n : ℕ) :
    ∀ i : ℝ, i ≠ 0 →
      (∀ k < n, survivalFromForce μ (x + (k : ℝ)) = survivalFromForce μ x) →
      lifeAnnuityDue μ x i n = (1 - (1 / (1 + i)) ^ n) / (1 - 1 / (1 + i)) := by sorry

end MathFin
