/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.FixedIncome.ZCB
public import MathFin.FixedIncome.YieldCurve
public import MathFin.Futures.Black76

-- pointers: MathFin/FixedIncome/ZCB.lean, MathFin/FixedIncome/YieldCurve.lean, MathFin/Futures/Black76.lean
-- main-module: MathFin/FixedIncome/InterestRateSwap.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-fixedincome-swap
-- source-issue: 66
-- new-defs: annuity, payer_swap_value, par_swap_rate

/-!
Vanilla interest-rate swap value and par swap rate.
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

noncomputable def annuity {ι : Type*} (δ : ℝ) (P : ι → ℝ) (s : Finset ι) : ℝ := δ * Finset.sum s P

def payer_swap_value (P0 Pn K A : ℝ) : ℝ := P0 - Pn - K * A

noncomputable def par_swap_rate (P0 Pn A : ℝ) : ℝ := (P0 - Pn) / A

theorem payer_swap_value_eq_zero_iff_fixed_rate_eq_par_swap_rate {ι : Type*} (r δ K : ℝ) (T : ι → ℝ) (s : Finset ι) (T_0 T_n : ι) (hT0 : T_0 ∈ s) (hTn : T_n ∈ s) (hδ : δ > 0) (hP : ∀ i ∈ s, MathFin.zcb r 0 (T i) > 0) : payer_swap_value (MathFin.zcb r 0 (T T_0)) (MathFin.zcb r 0 (T T_n)) K (annuity δ (fun i => MathFin.zcb r 0 (T i)) s) = 0 ↔ K = par_swap_rate (MathFin.zcb r 0 (T T_0)) (MathFin.zcb r 0 (T T_n)) (annuity δ (fun i => MathFin.zcb r 0 (T i)) s) := by
  set P0 := MathFin.zcb r 0 (T T_0) with hP0
  set Pn := MathFin.zcb r 0 (T T_n) with hPn
  set A := annuity δ (fun i => MathFin.zcb r 0 (T i)) s with hA
  have hsum_pos : 0 < Finset.sum s (fun i => MathFin.zcb r 0 (T i)) := by
    refine Finset.sum_pos (fun i hi => hP i hi) ?_
    exact ⟨T_0, hT0⟩
  have hA_pos : 0 < A := by
    dsimp [A, annuity]
    positivity
  have hA_ne_zero : A ≠ 0 := by linarith
  dsimp [payer_swap_value, par_swap_rate]
  constructor
  · intro h
    field_simp [hA_ne_zero]
    linarith
  · intro h
    rw [h]
    field_simp [hA_ne_zero]
    ring

end MathFin
