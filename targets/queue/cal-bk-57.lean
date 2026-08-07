/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.BlackScholes.ExchangeOption
public import MathFin.BlackScholes.MargrabeGrounding
public import MathFin.Foundations.BivariateGaussian

-- pointers: MathFin/BlackScholes/ExchangeOption.lean, MathFin/BlackScholes/MargrabeGrounding.lean, MathFin/Foundations/BivariateGaussian.lean
-- main-module: MathFin/BlackScholes/BetterOfTwoOption.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-blackscholes-better-of-two
-- source-issue: 57

/-!
Closed-form discounted price of the better-of-two (K=0 rainbow) option via max(S1,S2) = S2 + (S1-S2)+ and the Margrabe exchange-option price.
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

/-- **Better-of-two (K=0 rainbow) option price**: given (1) integrability of
`S2T` and of the exchange payoff `(S1T − S2T)⁺`, (2) that `S2` is a
discounted `Q`-martingale (`e^{-rT}·E_Q[S2T] = S2_0`), and (3) the Margrabe
exchange-option pricing hypothesis (`e^{-rT}·E_Q[(S1T − S2T)⁺] =
margrabePrice S1_0 S2_0 σ T`), the discounted expected payoff of receiving the
better of the two assets at maturity is `S2_0 + margrabePrice S1_0 S2_0 σ T`.
Pure algebra: `max a b = b + max (a - b) 0` pointwise, then linearity of the
(discounted) Bochner integral splits the price into the two hypotheses. -/
theorem betterOfTwo_price {Ω : Type*} {mΩ : MeasurableSpace Ω} {Q : Measure Ω}
    [IsProbabilityMeasure Q] {S1T S2T : Ω → ℝ} (hS1T : Measurable S1T)
    (hS2T : Measurable S2T) {S1_0 S2_0 r σ T : ℝ} (hσ : σ ≠ 0) (hT : 0 < T)
    (hintS2 : Integrable S2T Q)
    (hintEx : Integrable (fun ω => max (S1T ω - S2T ω) 0) Q)
    (hmart : Real.exp (-r * T) * ∫ ω, S2T ω ∂Q = S2_0)
    (hmargrabe : Real.exp (-r * T) * ∫ ω, max (S1T ω - S2T ω) 0 ∂Q
      = margrabePrice S1_0 S2_0 σ T) :
    Real.exp (-r * T) * ∫ ω, max (S1T ω) (S2T ω) ∂Q
      = S2_0 + margrabePrice S1_0 S2_0 σ T := by sorry

end MathFin
