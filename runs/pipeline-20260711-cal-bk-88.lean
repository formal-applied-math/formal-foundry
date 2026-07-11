/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib

-- pointers: MathFin/BlackScholes/Dividends.lean, MathFin/BlackScholes/Forward.lean
-- main-module: MathFin/Futures/Contango.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-futures-contango
-- source-issue: 88

/-!
# Contango, backwardation, and basis convergence

The no-arbitrage forward with a carry / convenience yield `δ` is
`F(T) = S · exp((r − δ)·T)` (the dividend-yield forward of `BlackScholes/Dividends`,
where the convenience yield plays the role of a continuous dividend). This records
the qualitative cost-of-carry sign structure: the market is in **contango**
(`F > S`) exactly when `r > δ`, in **backwardation** (`F < S`) exactly when
`r < δ`, and the **basis** `S − F` vanishes at `T = 0`. Pure `Real.exp`
monotonicity in the effective drift `r − δ`.
-/

@[expose] public section

namespace MathFin

/-- Cost-of-carry sign structure for the forward `F(T) = S · exp((r − δ)·T)`:
contango `F > S ↔ r > δ`, backwardation `F < S ↔ r < δ`, and basis convergence
`F(0) = S`. -/
theorem contango_backwardation_basis
    {S r δ T : ℝ} (hS : 0 < S) (hT : 0 < T) :
    (S < S * Real.exp ((r - δ) * T) ↔ δ < r) ∧
    (S * Real.exp ((r - δ) * T) < S ↔ r < δ) ∧
    S * Real.exp ((r - δ) * 0) = S := by
  have h3 : S * Real.exp ((r - δ) * 0) = S := by
    simp
  have hpos_inv : 0 < S⁻¹ := inv_pos.mpr hS
  have h1 : S < S * Real.exp ((r - δ) * T) ↔ δ < r := by
    constructor
    · intro h
      have h' := mul_lt_mul_of_pos_right h hpos_inv
      field_simp [hS.ne.symm] at h'
      -- h' : 1 < Real.exp ((r - δ) * T)
      have h_mul : 0 < (r - δ) * T := (Real.one_lt_exp_iff).mp h'
      have h_sub : 0 < r - δ := by
        by_contra! hle
        nlinarith
      exact sub_pos.mp h_sub
    · intro h
      have h_sub : 0 < r - δ := sub_pos.mpr h
      have h_mul : 0 < (r - δ) * T := mul_pos h_sub hT
      have h_exp : 1 < Real.exp ((r - δ) * T) := (Real.one_lt_exp_iff.mpr h_mul)
      have h_mul2 : S * 1 < S * Real.exp ((r - δ) * T) := mul_lt_mul_of_pos_left h_exp hS
      simpa [mul_one] using h_mul2
  have h2 : S * Real.exp ((r - δ) * T) < S ↔ r < δ := by
    constructor
    · intro h
      have h' := mul_lt_mul_of_pos_right h hpos_inv
      field_simp [hS.ne.symm] at h'
      -- h' : Real.exp ((r - δ) * T) < 1
      have h_mul : (r - δ) * T < 0 := (Real.exp_lt_one_iff).mp h'
      have h_sub : r - δ < 0 := by
        by_contra! hge
        nlinarith
      linarith
    · intro h
      have h_sub : r - δ < 0 := by linarith
      have h_mul : (r - δ) * T < 0 := by
        nlinarith
      have h_exp : Real.exp ((r - δ) * T) < 1 := (Real.exp_lt_one_iff.mpr h_mul)
      have h_mul2 : S * Real.exp ((r - δ) * T) < S * 1 := mul_lt_mul_of_pos_left h_exp hS
      simpa [mul_one] using h_mul2
  exact And.intro h1 (And.intro h2 h3)

end MathFin