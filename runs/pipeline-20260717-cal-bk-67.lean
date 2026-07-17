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
-- main-module: MathFin/FixedIncome/FixedIncome.FRA.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-fixedincome-fra_value
-- source-issue: 67

/-!
FRA value formula and fair forward rate identity
-/

@[expose] public section

namespace MathFin

theorem forward_rate_iff (r K T₁ T₂ : ℝ) (hT : T₂ ≠ T₁) :
    let P₁ := MathFin.zcb r 0 T₁;
    let P₂ := MathFin.zcb r 0 T₂;
    let δ := T₂ - T₁;
    let F := (P₁ / P₂ - 1) / δ;
    let V := δ * P₂ * (F - K);
    V = 0 ↔ K = F := by
  intro P₁ P₂ δ F V
  have hδ_ne_zero : δ ≠ 0 := sub_ne_zero.mpr hT
  have hP₂_pos : 0 < P₂ := zcb_pos r 0 T₂
  have h_factor_ne_zero : δ * P₂ ≠ 0 := mul_ne_zero hδ_ne_zero hP₂_pos.ne'
  constructor
  · intro hV
    have hprod : (δ * P₂) * (F - K) = 0 := by
      calc
        (δ * P₂) * (F - K) = V := rfl
        _ = 0 := hV
    rcases eq_zero_or_eq_zero_of_mul_eq_zero hprod with (h | h)
    · exact (h_factor_ne_zero h).elim
    · linarith
  · intro hKF
    have hsub : F - K = 0 := by linarith
    calc
      V = δ * P₂ * (F - K) := rfl
      _ = (δ * P₂) * 0 := by rw [hsub]
      _ = 0 := by ring

end MathFin
