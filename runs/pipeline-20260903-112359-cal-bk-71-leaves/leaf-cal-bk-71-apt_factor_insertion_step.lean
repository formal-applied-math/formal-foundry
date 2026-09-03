/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.Portfolio.CAPMEquilibrium

set_option autoImplicit false

@[expose] public section

namespace MathFin

-- apply: Finset.sum_cons, Finset.cons_induction, portfolioReturn
theorem apt_factor_insertion_step {ι κ : Type*} (s : Finset ι) (t : Finset κ) (k₀ : κ)
    (hk₀ : k₀ ∉ t) (a : ι → ℝ) (β : ι → κ → ℝ)
    (h_no_arb : ∀ w : ι → ℝ,
      portfolioReturn s w (fun _ ↦ (1 : ℝ)) = 0 →
      (∀ k ∈ Finset.cons k₀ t hk₀, portfolioReturn s w (fun i ↦ β i k) = 0) →
      portfolioReturn s w a = 0)
    (ih : ∀ a' : ι → ℝ,
      (∀ w : ι → ℝ, portfolioReturn s w (fun _ ↦ (1 : ℝ)) = 0 →
        (∀ k ∈ t, portfolioReturn s w (fun i ↦ β i k) = 0) →
        portfolioReturn s w a' = 0) →
      ∃ (lam0 : ℝ) (lam : κ → ℝ), ∀ i ∈ s, a' i = lam0 + ∑ k ∈ t, β i k * lam k) :
    ∃ (lam0 : ℝ) (lam : κ → ℝ), ∀ i ∈ s, a i = lam0 + ∑ k ∈ Finset.cons k₀ t hk₀, β i k * lam k := by sorry

end MathFin
