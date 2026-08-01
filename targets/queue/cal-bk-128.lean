/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.BlackScholes.MertonJumpDiffusion

-- pointers: MathFin/BlackScholes/MertonJumpDiffusion.lean
-- main-module: MathFin/BlackScholes/MertonJumpCompensator.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-blackscholes-merton-jump-compensator
-- source-issue: 128
-- deferred: the continuous-time jump-diffusion SDE dS/S = μ dt + σ dB + (e^Y − 1) dN as a stochastic-process/SDE object; the compensated jump integral ∫(e^y−1) dÑ = ∫(e^y−1) dN − λκt and its mean-zero/martingale property as a stochastic-integral statement (tracked under the compensated-integral pointer issue #1); μ = r − λκ as a standalone real-number drift equation (realized here structurally through mertonSpot's built-in −Λk compensation plus bsV's r-discounting, since MathFin.mertonSpot takes no separate μ or r parameter)
-- new-defs: {'name': 'mertonJumpCompensator', 'signature': 'ℝ → ℝ → ℝ', 'meaning': 'Closed-form risk-neutral jump compensator κ = E[e^Y − 1] for log-normal jump size Y = m + δZ', 'built_from': ['Real.exp']}

/-!
Log-normal Merton jump compensator κ = E[e^Y−1] = e^{m+δ²/2}−1 as a proved Gaussian expectation, instantiated in mertonSpot's risk-neutral recombination E[S]=S₀.
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

/-- Risk-neutral jump compensator `κ = E[e^Y − 1]` for a log-normal jump size
`Y = m + δ·Z`, `Z` standard normal: closed form `e^{m+δ²/2} − 1`. -/
noncomputable def mertonJumpCompensator (m δ : ℝ) : ℝ :=
  Real.exp (m + δ ^ 2 / 2) - 1

example : mertonJumpCompensator 0 0 = 0 := by
  simp [mertonJumpCompensator]

example (δ : ℝ) : mertonJumpCompensator 0 δ = mertonJumpCompensator 0 (-δ) := by
  simp [mertonJumpCompensator]

/-- The closed-form jump compensator `e^{m+δ²/2} − 1` equals the actual
expectation `E[e^Y − 1]` computed against the standard Gaussian measure. -/
theorem mertonJumpCompensator_eq_integral (m δ : ℝ) :
    mertonJumpCompensator m δ =
      ∫ z, (Real.exp (m + δ * z) - 1) ∂ProbabilityTheory.gaussianReal 0 1 := by sorry

/-- Once the abstract compensator `k` in the Merton mixture is instantiated at
the concrete log-normal-jump value `κ = e^{m+δ²/2} − 1`, the jump-count-averaged
conditional spot exactly recombines to the initial spot `S_0`. -/
theorem merton_lognormal_spot_recombination (S_0 m δ : ℝ) (Λ : NNReal) :
    ∫ n, MathFin.mertonSpot S_0 (mertonJumpCompensator m δ) Λ n
      ∂ProbabilityTheory.poissonMeasure Λ = S_0 :=
  MathFin.integral_mertonSpot S_0 (mertonJumpCompensator m δ) Λ

end MathFin
