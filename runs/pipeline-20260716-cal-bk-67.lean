/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.FixedIncome.ForwardRate
public import MathFin.FixedIncome.ZCB

-- pointers: MathFin/FixedIncome/ForwardRate.lean, MathFin/FixedIncome/ZCB.lean
-- main-module: MathFin/FixedIncome/SimpleForwardRate.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-forward-rate-simple
-- source-issue: 67

/-!
Simple forward rate, FRA value, and fair-rate identity
-/

@[expose] public section

namespace MathFin

theorem fra_value (r t T₁ T₂ : ℝ) (hδ : T₂ - T₁ ≠ 0) (K : ℝ) : let δ := T₂ - T₁; let P₁ := zcb r t T₁; let P₂ := zcb r t T₂; let F := (P₁ / P₂ - 1) / δ; let V := δ * P₂ * (F - K); V = δ * P₂ * (F - K) ∧ (V = 0 ↔ K = F) := by
  intro δ P₁ P₂ F V
  have hP₂_pos : P₂ > 0 := zcb_pos r t T₂
  have h_mul_ne_zero : δ * P₂ ≠ 0 := mul_ne_zero hδ (ne_of_gt hP₂_pos)
  have h_zero_iff : (δ * P₂ * (F - K) = 0) ↔ (K = F) := by
    constructor
    · intro h
      rcases eq_zero_or_eq_zero_of_mul_eq_zero h with (h' | h')
      · exfalso; exact h_mul_ne_zero h'
      · linarith
    · intro h
      have hsub : F - K = 0 := by linarith
      simp [hsub]
  exact ⟨rfl, h_zero_iff⟩

end MathFin