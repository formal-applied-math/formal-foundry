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
-- main-module: MathFin/FixedIncome/MathFin.FixedIncome.ForwardRate.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-fra-value_and_fair_rate
-- source-issue: 67

/-!
FRA value is zero exactly when the strike equals the simple forward rate.
-/

@[expose] public section

namespace MathFin

theorem fra_value_zero_iff_fair (T₁ T₂ : ℝ) (P₁ P₂ : ℝ) (hδ : T₂ - T₁ ≠ 0) (K : ℝ)
  (h_pos : P₁ > 0 ∧ P₂ > 0) :
  let δ := T₂ - T₁;
  let F := (P₁ / P₂ - 1) / δ;
  let V := δ * P₂ * (F - K);
  V = 0 ↔ K = F := by
  intro δ F V
  have hP₂_ne : P₂ ≠ 0 := by linarith [h_pos.2]
  have hδ_ne : δ ≠ 0 := hδ
  constructor
  · intro hV
    have hzero : F - K = 0 := by
      have hprod : δ * P₂ * (F - K) = 0 := hV
      rcases mul_eq_zero.mp hprod with (hδP₂ | hFK)
      · rcases mul_eq_zero.mp hδP₂ with (hδzero | hP₂zero)
        · exact absurd hδzero hδ_ne
        · exact absurd hP₂zero hP₂_ne
      · exact hFK
    linarith
  · intro hFK
    calc
      V = δ * P₂ * (F - K) := rfl
      _ = δ * P₂ * 0 := by rw [hFK, sub_self]
      _ = 0 := by ring

end MathFin