/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.Models.ModelFree.SVI.TailCondition

set_option autoImplicit false

@[expose] public section

namespace MathFin


-- pointers: MathFin/Models/ModelFree/SVI/TailCondition.lean, MathFin/Models/ModelFree/SVI/BlackSmile.lean, MathFin/Models/ModelFree/CallPriceFunction.lean, MathFin/Models/ModelFree/CalendarArbitrage.lean, MathFin/Models/LocalVol/ForwardSmile.lean
-- main-module: MathFin/Models/ModelFree/BlackFormTail.lean
-- benchmark: benchmarks/black_scholes.json
-- benchmark-id: mf-smile-black-form-tail-iff
-- source-issue: 318
-- deferred: SVI tail lemmas re-derived as corollaries of the general lemma; left-wing bound: IsNormalizedCallPrice (bsSmile p) implies b(1 - rho) ≤ 2, and the mass at zero; calendar-spread martingale step for any convex payoff via conditional Jensen; t > 0 calendar statements tied to the local-variance characterisation; unification of blackForm, totalVarianceCall and logBlackCall into one (k, w) Black form; IsNormalizedCallPrice from a nonnegative random variable with mean 1 (easy direction); port hygiene: scoped maxHeartbeats, maxRecDepth and open scoped Classical
-- blueprint: thm:smile-no-arbitrage






/-- For any everywhere-positive total variance `w`, the Black form `blackForm k (w k)` tends to
`0` as `k → +∞` iff `dpl k (w k)` tends to `-∞`. -/

-- apply: MathFin.SVI.blackCall_le_Phi_dpl, MathFin.SVI.bsSmile_nonneg
theorem blackForm_nonneg_le_Phi_dpl (k w : ℝ) (hw : 0 < w) :
    0 ≤ MathFin.ForwardSmile.blackForm k w ∧
      MathFin.ForwardSmile.blackForm k w ≤ MathFin.Phi (MathFin.ForwardSmile.dpl k w) := by
  let p : MathFin.SVI.Params := ⟨w, 0, 0, 0, 0⟩
  have hv (x : ℝ) : MathFin.SVI.variance p x = w := by simp [p, MathFin.SVI.variance]
  have hK : 0 < Real.exp k := Real.exp_pos k
  have hw' : 0 < MathFin.SVI.variance p (Real.log (Real.exp k)) := by rw [hv]; exact hw
  have hbc : MathFin.SVI.blackCall p (Real.exp k) = MathFin.ForwardSmile.blackForm k w := by
    simp [MathFin.SVI.blackCall, hv]
  rw [← hbc]
  refine ⟨?_, ?_⟩
  · rw [MathFin.SVI.blackCall_eq_bsSmile p _ hK hw']
    exact MathFin.SVI.bsSmile_nonneg p hK hw'
  · simpa [hv] using MathFin.SVI.blackCall_le_Phi_dpl p (Real.exp k)

end MathFin
