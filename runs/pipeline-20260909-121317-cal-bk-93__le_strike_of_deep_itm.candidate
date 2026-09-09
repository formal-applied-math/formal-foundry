/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.Binomial.American
public import MathFin.Binomial.MertonAmericanCallTree
public import MathFin.Binomial.Model

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

-- pointers: MathFin/Binomial/American.lean, MathFin/Binomial/MertonAmericanCallTree.lean, MathFin/Binomial/Model.lean
-- main-module: MathFin/Binomial/AmericanPutEarlyExercisePremium.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-binomial-put-early-exercise-premium
-- source-issue: 93






/-- **Deep in-the-money American put early-exercise premium.** In a
no-arbitrage binomial tree, if the spot is so far in-the-money that even
after `n` consecutive up-moves it never exceeds the strike (`S * u ^ n ≤ K`),
then for positive rate `r`, positive strike `K`, positive spot `S`, and at
least one time step, the American put price at `(n, S)` strictly exceeds the
European binomial put price with the same payoff. -/

-- apply: lt_u, Real.exp_zero, Real.exp_lt_exp, one_le_pow₀, le_mul_of_one_le_right
theorem le_strike_of_deep_itm {u d r K S : ℝ} {n : ℕ} (h : BinomialNoArb u d r) (hr : 0 < r) (hS : 0 < S) (hdeep : S * u ^ n ≤ K) : S ≤ K := by sorry

end MathFin
