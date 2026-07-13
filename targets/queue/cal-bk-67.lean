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
  V = 0 ↔ K = F := by sorry

end MathFin
