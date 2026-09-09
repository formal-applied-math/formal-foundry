/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.Portfolio.CAPMEquilibrium
public import MathFin.Portfolio.CAPM

-- pointers: MathFin/Portfolio/CAPMEquilibrium.lean, MathFin/Portfolio/CAPM.lean
-- main-module: MathFin/Portfolio/ArbitragePricingTheory.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-portfolio-apt-exact-factor-pricing
-- source-issue: 71

/-!
No-arbitrage in an exact multi-factor model forces asset intercepts to be affine in the factor loadings.
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

/-- **Arbitrage Pricing Theory (exact factor model)**: let `a : ι → ℝ` be asset
intercepts and `β : ι → κ → ℝ` factor loadings in the exact factor model
`Rᵢ = a i + ∑_{k ∈ t} β i k * F k`. If every zero-cost, zero-factor-exposure
portfolio `w` earns zero expected payoff on the intercepts (no-arbitrage),
then the intercepts are affine in the loadings: there exist a zero-beta
return `lam0` and factor risk premia `lam : κ → ℝ` with
`a i = lam0 + ∑_{k ∈ t} β i k * lam k` for every `i ∈ s`. -/
theorem apt_exact_factor_pricing {ι κ : Type*} (s : Finset ι) (t : Finset κ)
    (a : ι → ℝ) (β : ι → κ → ℝ)
    (h_no_arb : ∀ w : ι → ℝ,
      portfolioReturn s w (fun _ ↦ (1 : ℝ)) = 0 →
      (∀ k ∈ t, portfolioReturn s w (fun i ↦ β i k) = 0) →
      portfolioReturn s w a = 0) :
    ∃ (lam0 : ℝ) (lam : κ → ℝ), ∀ i ∈ s, a i = lam0 + ∑ k ∈ t, β i k * lam k := by
  sorry

end MathFin
