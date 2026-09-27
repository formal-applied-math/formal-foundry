/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.RiskMeasures.UtilityDerivation
public import MathFin.Performance.Kelly

-- pointers: MathFin/RiskMeasures/UtilityDerivation.lean, MathFin/Performance/Kelly.lean
-- main-module: MathFin/RiskMeasures/RiskAversion.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-riskmeasures-arrow-pratt-risk-aversion
-- source-issue: 79

/-!
Arrow-Pratt absolute/relative risk-aversion coefficients A and R; CARA/CRRA/log constancy; links to acceptance-set translation and Kelly log-growth.
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

/-- The **Arrow-Pratt absolute risk aversion** of a utility function `u` at wealth `x`:
`A(x) = -u''(x) / u'(x)`. -/
noncomputable def absoluteRiskAversion (u : ℝ → ℝ) (x : ℝ) : ℝ :=
  - deriv (deriv u) x / deriv u x

/-- The **Arrow-Pratt relative risk aversion** of a utility function `u` at wealth `x`:
`R(x) = x * A(x)`. -/
noncomputable def relativeRiskAversion (u : ℝ → ℝ) (x : ℝ) : ℝ :=
  x * absoluteRiskAversion u x

example (x : ℝ) : absoluteRiskAversion (fun x : ℝ => x) x = 0 := by
  simp [absoluteRiskAversion]

example (x : ℝ) : relativeRiskAversion (fun x : ℝ => x) x = 0 := by
  simp [relativeRiskAversion, absoluteRiskAversion]

/-- Bundles the intended benchmark entry: Arrow-Pratt constancy for CARA (absolute)
and for CRRA/log (relative), plus the two corollaries linking risk aversion to
acceptance-set translation invariance (via `acceptableUnderUtility_monotone_translation`)
and to the Kelly log-growth objective (via `integral_log_kellyReturnMeasure`). -/
theorem _agentic_placeholder :
    (∀ (a : ℝ), 0 < a → ∀ (x : ℝ),
        absoluteRiskAversion (fun x => - Real.exp (-(a * x))) x = a) ∧
    (∀ (γ : ℝ), 0 < γ → γ ≠ 1 → ∀ (x : ℝ), 0 < x →
        relativeRiskAversion (fun x => Real.rpow x (1 - γ) / (1 - γ)) x = γ) ∧
    (∀ (x : ℝ), 0 < x → relativeRiskAversion Real.log x = 1) ∧
    (∀ (a : ℝ), 0 < a →
        Monotone (fun x => - Real.exp (-(a * x))) ∧
        ∀ {ι : Type*} (s : Finset ι) (p : ι → ℝ), (∀ i ∈ s, 0 ≤ p i) →
          ∀ (W : ℝ) (X : ι → ℝ) (c : ℝ), 0 ≤ c →
            acceptableUnderUtility s p (fun x => - Real.exp (-(a * x))) W X →
            acceptableUnderUtility s p (fun x => - Real.exp (-(a * x))) W (fun i => X i + c)) ∧
    (∀ (p : ℝ), 0 ≤ p → p ≤ 1 → ∀ (b f : ℝ),
        kellyGrowth p b f = ∫ x, Real.log x ∂(kellyReturnMeasure p b f)) := by
  sorry

end MathFin
