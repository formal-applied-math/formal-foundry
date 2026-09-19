/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.DeFi.ConstantProductAMM

-- pointers: MathFin/DeFi/ConstantProductAMM.lean
-- main-module: MathFin/DeFi/ImpermanentLoss.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-defi-impermanent-loss
-- source-issue: 90

/-!
Impermanent loss IL(r) = 2√r/(1+r) − 1 for constant-product AMM LPs, with IL(r) ≤ 0 and equality iff r = 1.
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

namespace DeFi

/-- The reserve of token X after an arbitrageur has driven the pool's
internal price to `r * p₀`, where `p₀` is the internal price at the
original reserves `(x₀, y₀)`. Derived from the constant-product
invariant `xᵣ * yᵣ = x₀ * y₀` together with `yᵣ / xᵣ = r * (y₀ / x₀)`. -/
noncomputable def newReserveX (x₀ r : ℝ) : ℝ := x₀ / Real.sqrt r

/-- The reserve of token Y after an arbitrageur has driven the pool's
internal price to `r * p₀` (see `newReserveX`). -/
noncomputable def newReserveY (y₀ r : ℝ) : ℝ := y₀ * Real.sqrt r

/-- Value (denominated in token Y, at the new external price `r * p₀`) of the
LP position after the pool has been arbitraged to the new price. -/
noncomputable def lpValue (x₀ y₀ r : ℝ) : ℝ :=
  newReserveX x₀ r * (r * internalPrice x₀ y₀) + newReserveY y₀ r

/-- Value (denominated in token Y, at the new external price `r * p₀`) of
simply holding the original basket `(x₀, y₀)` instead of providing it as
liquidity. -/
noncomputable def holdValue (x₀ y₀ r : ℝ) : ℝ :=
  x₀ * (r * internalPrice x₀ y₀) + y₀

/-- **Impermanent loss**: the relative shortfall of the LP position's value
versus simply holding the original basket, after the external price of X
(in units of Y) has moved by a factor `r` away from the deposit-time
internal price. -/
noncomputable def impermanentLoss (x₀ y₀ r : ℝ) : ℝ :=
  lpValue x₀ y₀ r / holdValue x₀ y₀ r - 1

example (x₀ : ℝ) : newReserveX x₀ 1 = x₀ := by
  simp [newReserveX, Real.sqrt_one]

example (x₀ y₀ : ℝ) (hx₀ : 0 < x₀) (hy₀ : 0 < y₀) : impermanentLoss x₀ y₀ 1 = 0 := by
  have hx : newReserveX x₀ 1 = x₀ := by simp [newReserveX, Real.sqrt_one]
  have hy : newReserveY y₀ 1 = y₀ := by simp [newReserveY, Real.sqrt_one]
  have hpos : 0 < x₀ * internalPrice x₀ y₀ + y₀ := by
    have : 0 < internalPrice x₀ y₀ := div_pos hy₀ hx₀
    positivity
  simp [impermanentLoss, lpValue, holdValue, hx, hy, div_self hpos.ne']

end DeFi

/-- **Impermanent loss for a constant-product AMM LP**: starting from
reserves `x₀, y₀ > 0` with internal price `p₀ = y₀ / x₀` and invariant
`k = x₀ * y₀`, if the external price of X (in units of Y) moves to `r * p₀`
with `r > 0`, an arbitrageur drives the pool to reserves
`xᵣ = x₀ / √r`, `yᵣ = y₀ * √r`. These reserves (i) preserve the
constant-product invariant, (ii) match the pool's internal price to the new
external price, and (iii) are strictly positive. The resulting impermanent
loss `IL(r) = lpValue(r) / holdValue(r) - 1` has the closed form
`2√r / (1 + r) - 1`, is always `≤ 0` (AM–GM), and vanishes exactly at
`r = 1` (no price movement, no loss). -/
theorem defi_impermanent_loss_formula
    (x₀ y₀ : ℝ) (hx₀ : 0 < x₀) (hy₀ : 0 < y₀) (r : ℝ) (hr : 0 < r) :
    DeFi.newReserveX x₀ r * DeFi.newReserveY y₀ r = x₀ * y₀ ∧
      DeFi.internalPrice (DeFi.newReserveX x₀ r) (DeFi.newReserveY y₀ r)
        = r * DeFi.internalPrice x₀ y₀ ∧
      0 < DeFi.newReserveX x₀ r ∧ 0 < DeFi.newReserveY y₀ r ∧
      DeFi.impermanentLoss x₀ y₀ r = 2 * Real.sqrt r / (1 + r) - 1 ∧
      DeFi.impermanentLoss x₀ y₀ r ≤ 0 ∧
      (DeFi.impermanentLoss x₀ y₀ r = 0 ↔ r = 1) := by
  sorry

end MathFin
