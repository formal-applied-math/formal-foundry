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
-- main-module: MathFin/Models/ModelFree/LeftWingBound.lean
-- benchmark: benchmarks/black_scholes.json
-- benchmark-id: mf-svi-left-wing-bound
-- source-issue: 318
-- deferred: general tail lemma C -> 0 iff d_plus -> -infinity for any positive total variance w : R -> R; easy direction of grounding IsNormalizedCallPrice: nonnegative S with E S = 1 gives the structure; mass at zero of the underlying law for the SVI smile; calendar-spread martingale step for any convex payoff via conditional Jensen; t > 0 calendar statements connected to the local-variance characterisation; single (k, w) Black form replacing blackForm, totalVarianceCall and logBlackCall; port hygiene: scope maxHeartbeats, maxRecDepth and open scoped Classical; folding Foundations/NormalTail into its natural homes
-- blueprint: thm:smile-no-arbitrage

/-!
# The left-wing slope bound for a normalised SVI call price function

A raw SVI smile with positive variance whose smiled call is a normalised call price function has
left wing slope b(1 - rho) at most 2.

## Main results

* `svi_left_wing_slope_le_two`: The left-wing slope bound for a normalised SVI call price function

## References

* [Roper, *Arbitrage free implied volatility surfaces*][Roper2010]

## Tags

svi, left wing, moment formula, no-arbitrage, call price function, roper, MSC 91G20
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

theorem svi_left_wing_slope_le_two (p : SVI.Params)
    (hpos : ∀ k : ℝ, 0 < SVI.variance p k)
    (hC : IsNormalizedCallPrice (SVI.bsSmile p)) :
    p.b * (1 - p.rho) ≤ 2 := by sorry

end MathFin
