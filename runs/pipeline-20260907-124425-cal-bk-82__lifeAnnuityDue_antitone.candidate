/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.Actuarial.Mortality

set_option autoImplicit false

@[expose] public section

namespace MathFin

-- apply: one_div_le_one_div_of_le, pow_le_pow_left, mul_le_mul_of_nonneg_right, Finset.sum_le_sum, survivalFromForce_pos
theorem lifeAnnuityDue_antitone (μ : ℝ → ℝ) (x : ℝ) (n : ℕ) :
    ∀ i₁ i₂ : ℝ, -1 < i₁ → -1 < i₂ → i₁ ≤ i₂ →
      lifeAnnuityDue μ x i₂ n ≤ lifeAnnuityDue μ x i₁ n := by sorry

end MathFin
