/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.Actuarial.Insurance

-- pointers: MathFin/Actuarial/Insurance.lean
-- main-module: MathFin/Actuarial/ActuarialInsurance.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-insurance-premium-principles
-- source-issue: 85
-- new-defs: expectedValuePremium, variancePremium, stdDevPremium

/-!
Classical loaded premium principles and their nonnegative-loading bounds.
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

def expectedValuePremium : ∀ (θ μ : ℝ), ℝ := λ θ μ => (1 + θ) * μ
def variancePremium : ∀ (α μ σ2 : ℝ), ℝ := λ α μ σ2 => μ + α * σ2
def stdDevPremium : ∀ (β μ σ : ℝ), ℝ := λ β μ σ => μ + β * σ

theorem premium_ge_mean (μ σ2 σ θ α β : ℝ) (hμ : 0 ≤ μ) (hσ2 : 0 ≤ σ2) (hσ : 0 ≤ σ) (hθ : 0 ≤ θ) (hα : 0 ≤ α) (hβ : 0 ≤ β) (hσ_eq : σ = Real.sqrt σ2) : expectedValuePremium θ μ ≥ μ ∧ variancePremium α μ σ2 ≥ μ ∧ stdDevPremium β μ σ ≥ μ := by
  have h1 : expectedValuePremium θ μ ≥ μ := by
    dsimp [expectedValuePremium]
    nlinarith
  have h2 : variancePremium α μ σ2 ≥ μ := by
    dsimp [variancePremium]
    nlinarith
  have h3 : stdDevPremium β μ σ ≥ μ := by
    dsimp [stdDevPremium]
    nlinarith
  exact And.intro h1 (And.intro h2 h3)

end MathFin
