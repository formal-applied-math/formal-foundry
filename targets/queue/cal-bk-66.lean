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

theorem payer_swap_value_eq_zero_iff_fixed_rate_eq_par_swap_rate {ι : Type*} (r δ K : ℝ) (T : ι → ℝ) (s : Finset ι) (T_0 T_n : ι) (hT0 : T_0 ∈ s) (hTn : T_n ∈ s) (hδ : δ > 0) (hP : ∀ i ∈ s, MathFin.zcb r 0 (T i) > 0) : payer_swap_value (MathFin.zcb r 0 (T T_0)) (MathFin.zcb r 0 (T T_n)) K (annuity δ (fun i => MathFin.zcb r 0 (T i)) s) = 0 ↔ K = par_swap_rate (MathFin.zcb r 0 (T T_0)) (MathFin.zcb r 0 (T T_n)) (annuity δ (fun i => MathFin.zcb r 0 (T i)) s) := by sorry

end MathFin
