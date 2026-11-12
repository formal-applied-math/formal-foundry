/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.Models.LocalVol.ForwardSmile
public import MathFin.Models.ModelFree.CalendarArbitrage
public import MathFin.Models.ModelFree.CallPriceFunction
public import MathFin.Models.ModelFree.SVI.BlackSmile
public import MathFin.Models.ModelFree.SVI.TailCondition

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

-- pointers: MathFin/Models/ModelFree/SVI/TailCondition.lean, MathFin/Models/ModelFree/SVI/BlackSmile.lean, MathFin/Models/ModelFree/CallPriceFunction.lean, MathFin/Models/ModelFree/CalendarArbitrage.lean, MathFin/Models/LocalVol/ForwardSmile.lean
-- main-module: MathFin/Models/ModelFree/BlackFormTail.lean
-- benchmark: benchmarks/black_scholes.json
-- benchmark-id: mf-smile-black-form-tail-iff
-- source-issue: 318
-- deferred: SVI tail lemmas re-derived as corollaries of the general lemma; left-wing bound: IsNormalizedCallPrice (bsSmile p) implies b(1 - rho) ≤ 2, and the mass at zero; calendar-spread martingale step for any convex payoff via conditional Jensen; t > 0 calendar statements tied to the local-variance characterisation; unification of blackForm, totalVarianceCall and logBlackCall into one (k, w) Black form; IsNormalizedCallPrice from a nonnegative random variable with mean 1 (easy direction); port hygiene: scoped maxHeartbeats, maxRecDepth and open scoped Classical
-- blueprint: thm:smile-no-arbitrage






/-- For any everywhere-positive total variance `w`, the Black form `blackForm k (w k)` tends to
`0` as `k → +∞` iff `dpl k (w k)` tends to `-∞`. -/

theorem Phi_tendsto_zero_atBot : Filter.Tendsto MathFin.Phi Filter.atBot (nhds 0) := by sorry

end MathFin
