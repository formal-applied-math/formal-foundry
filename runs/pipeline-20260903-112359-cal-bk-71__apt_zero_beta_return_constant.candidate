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

-- apply: portfolioReturn, Finset.sum_congr, Finset.sum_smul
theorem apt_zero_beta_return_constant {ι : Type*} (s : Finset ι) (a : ι → ℝ)
    (h : ∀ w : ι → ℝ, portfolioReturn s w (fun _ ↦ (1 : ℝ)) = 0 → portfolioReturn s w a = 0) :
    ∃ lam0 : ℝ, ∀ i ∈ s, a i = lam0 := by sorry

end MathFin
