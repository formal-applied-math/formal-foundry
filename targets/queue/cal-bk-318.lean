/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.Models.ModelFree.SVI.TailCondition
public import MathFin.Models.ModelFree.SVI.BlackSmile
public import MathFin.Models.ModelFree.CallPriceFunction
public import MathFin.Models.ModelFree.CalendarArbitrage
public import MathFin.Models.LocalVol.ForwardSmile

-- pointers: MathFin/Models/ModelFree/SVI/TailCondition.lean, MathFin/Models/ModelFree/SVI/BlackSmile.lean, MathFin/Models/ModelFree/CallPriceFunction.lean, MathFin/Models/ModelFree/CalendarArbitrage.lean, MathFin/Models/LocalVol/ForwardSmile.lean
-- main-module: MathFin/Models/ModelFree/BlackFormTail.lean
-- benchmark: benchmarks/black_scholes.json
-- benchmark-id: mf-smile-black-form-tail-iff
-- source-issue: 318
-- deferred: SVI tail lemmas re-derived as corollaries of the general lemma; left-wing bound: IsNormalizedCallPrice (bsSmile p) implies b(1 - rho) ≤ 2, and the mass at zero; calendar-spread martingale step for any convex payoff via conditional Jensen; t > 0 calendar statements tied to the local-variance characterisation; unification of blackForm, totalVarianceCall and logBlackCall into one (k, w) Black form; IsNormalizedCallPrice from a nonnegative random variable with mean 1 (easy direction); port hygiene: scoped maxHeartbeats, maxRecDepth and open scoped Classical
-- blueprint: thm:smile-no-arbitrage

/-!
# Tail condition for the Black form at general positive total variance

For any everywhere-positive total variance `w`, the Black form `blackForm k (w k)` tends to 0 at
large `k` exactly when `d₊(k, w k)` tends to `-∞`.

## Main results

* `blackForm_tendsto_zero_iff_dpl_tendsto_atBot`: Tail condition for the Black form at general
  positive total variance

## References

* [Roper, *Arbitrage free implied volatility surfaces*][Roper2010]

## Tags

tail condition, black form, total variance, d plus, call price, no-arbitrage, smile, MSC 91G20
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

/-- For any everywhere-positive total variance `w`, the Black form `blackForm k (w k)` tends to
`0` as `k → +∞` iff `dpl k (w k)` tends to `-∞`. -/
theorem blackForm_tendsto_zero_iff_dpl_tendsto_atBot (w : ℝ → ℝ) (hw : ∀ k, 0 < w k) :
    Filter.Tendsto (fun k ↦ MathFin.ForwardSmile.blackForm k (w k)) Filter.atTop (nhds 0) ↔
      Filter.Tendsto (fun k ↦ MathFin.ForwardSmile.dpl k (w k)) Filter.atTop Filter.atBot := by
  sorry

end MathFin
