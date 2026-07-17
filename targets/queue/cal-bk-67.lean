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
  sorry

end MathFin
