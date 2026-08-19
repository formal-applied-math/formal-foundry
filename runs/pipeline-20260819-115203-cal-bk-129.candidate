/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.BlackScholes.MertonJumpDiffusion

-- pointers: MathFin/BlackScholes/MertonJumpDiffusion.lean
-- main-module: MathFin/BlackScholes/MertonJumpDiffusionDelta.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-merton-jd-delta
-- source-issue: 129
-- deferred: gamma mixture: the analogous second S-derivative (Poisson-weighted BS gammas) of mertonCallPrice; vega mixture: the analogous σ-derivative of mertonCallPrice, requiring the chain rule through mertonVol σ δ T n

/-!
Merton call delta as the Poisson-weighted mixture of chain-ruled Black–Scholes deltas, with the mixture bounded in [0,1].
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

/-- **Merton call delta**: `∂_{S₀} mertonCallPrice` is the Poisson-weighted mixture of
chain-ruled conditional Black–Scholes deltas, `∑' n, w(n) · (Sₙ(S₀)/S₀) · Φ(d₁(Sₙ(S₀)))`
(the factor `Sₙ(S₀)/S₀` being `deriv (fun S ↦ mertonSpot S k Λ n) S₀`), and this mixture
delta is itself a probability, i.e. lies in `[0, 1]`. -/
theorem mertonCallPrice_hasDerivAt {S_0 K r σ T k : ℝ} (δ : ℝ) (Λ : ℝ≥0)
    (hS_0 : 0 < S_0) (hK : 0 < K) (hσ : 0 < σ) (hT : 0 < T) (hk : -1 < k) :
    HasDerivAt (fun S ↦ mertonCallPrice S K r σ T k δ Λ)
        (∑' n : ℕ, Real.exp (-(Λ : ℝ)) * (Λ : ℝ) ^ n / (n.factorial : ℝ)
          * (mertonSpot S_0 k Λ n / S_0)
          * deriv (fun S ↦ bsV K r (mertonVol σ δ T n) S T) (mertonSpot S_0 k Λ n)) S_0
      ∧ (∑' n : ℕ, Real.exp (-(Λ : ℝ)) * (Λ : ℝ) ^ n / (n.factorial : ℝ)
          * (mertonSpot S_0 k Λ n / S_0)
          * deriv (fun S ↦ bsV K r (mertonVol σ δ T n) S T) (mertonSpot S_0 k Λ n))
        ∈ Set.Icc (0 : ℝ) 1 := by sorry

end MathFin
