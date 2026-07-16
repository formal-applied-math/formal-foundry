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

theorem fra_value (r t T₁ T₂ : ℝ) (hδ : T₂ - T₁ ≠ 0) (K : ℝ) : let δ := T₂ - T₁; let P₁ := zcb r t T₁; let P₂ := zcb r t T₂; let F := (P₁ / P₂ - 1) / δ; let V := δ * P₂ * (F - K); V = δ * P₂ * (F - K) ∧ (V = 0 ↔ K = F) := by sorry

end MathFin
