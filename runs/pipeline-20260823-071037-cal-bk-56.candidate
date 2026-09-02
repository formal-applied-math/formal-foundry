/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.BlackScholes.GBMLogMoments
public import MathFin.BlackScholes.Call
public import MathFin.BlackScholes.Dividends

-- pointers: MathFin/BlackScholes/GBMLogMoments.lean, MathFin/BlackScholes/Call.lean, MathFin/BlackScholes/Dividends.lean
-- main-module: MathFin/BlackScholes/ForwardStartCall.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-blackscholes-forward-start-call
-- source-issue: 56

/-!
Closed-form price of a forward-start call via GBM ratio factorization (Rubinstein).
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

/-- **Rubinstein forward-start call**: the strike is fixed at `t1` as the
fraction `α` of the intermediate spot `S_{t1}`, and the call pays
`(S_T − α · S_{t1})⁺` at `T`. Its time-0 price under the risk-neutral GBM
(drift `r − q`, vol `σ`, continuous dividend yield `q`) equals the
discounted forward `S_0 · e^{−q·t1}` times the Garman-normal-form value of a
unit-spot call struck at `α` with residual maturity `T − t1`. -/
theorem forward_start_call_price
    {Ω : Type*} {mΩ : MeasurableSpace Ω} {Q : Measure Ω} [IsProbabilityMeasure Q]
    {S_0 r q σ α t1 T : ℝ} {Z1 Z2 : Ω → ℝ}
    (hS_0 : 0 < S_0) (hσ : 0 < σ) (hα : 0 < α) (ht1 : 0 < t1) (ht1T : t1 < T)
    (hZ1 : HasLaw Z1 (gaussianReal 0 1) Q)
    (hZ2 : BSCallHyp Q 1 α (r - q) σ (T - t1) Z2)
    (hIndep : IndepFun Z1 Z2 Q) :
    ∫ ω, Real.exp (-r * T) *
        max (bsTerminal (bsTerminal S_0 (r - q) σ t1 (Z1 ω)) (r - q) σ (T - t1) (Z2 ω)
              - α * bsTerminal S_0 (r - q) σ t1 (Z1 ω)) 0 ∂Q
      = S_0 * Real.exp (-q * t1) *
          bsVGarman (Real.exp (-q * (T - t1))) α (Real.exp (-r * (T - t1))) σ (T - t1) := by
  sorry

end MathFin
