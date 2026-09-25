/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.Actuarial.Mortality

-- pointers: MathFin/Actuarial/Mortality.lean
-- main-module: MathFin/Actuarial/SurvivalModel.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-actuarial-survival-force-bridge
-- source-issue: 116

/-!
Bridges Ito's probabilistic survival ccdf `survive` on a `Survival_Model` to the analytic `survivalFromForce`, and instantiates the bridge at the Gompertz force of mortality.
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

variable {Ω : Type*} [MeasurableSpace Ω] {P : Measure Ω} {X : Ω → ℝ}

/-- **Force-of-mortality bridge for Ito's probabilistic `Survival_Model`.** Fix a
`Survival_Model` for entry age `x`: a probability measure `P`, an age-at-death random
variable `X`, and `x` with `P (alive X x) ≠ 0` (the life reaches age `x`), so that
`fun t ↦ survive P X t x` (Ito's `tpₓ`) is a genuine ccdf of the future lifetime `T(x)` via
`survive_eq_survivalFunction_ratio` (AFP: `ccdfTx_ccdfX`). Let `μ` be its force of mortality
on `[0, ∞)` — a continuous function such that `tpₓ`'s negative logarithmic derivative equals
`μ t` at every `t ≥ 0`, i.e. `HasDerivAt (fun s ↦ -Real.log (survive P X s x)) (μ t) t` (the
probabilistic-side analogue of `force_eq_neg_log_deriv_survival`). Then `tpₓ` reconstructs as
`survivalFromForce μ` on `[0, ∞)`: `tpₓ = exp(-∫₀ᵗ μ_{x+s} ds)` — the classical identity
linking Ito's probabilistic layer to the analytic `survivalFromForce`. -/
theorem survive_eq_survivalFromForce_of_force
    (hX : Measurable X) [IsProbabilityMeasure P] (x : ℝ)
    (hx : P (SurvivalModel.alive X x) ≠ 0) (μ : ℝ → ℝ) (hμ : Continuous μ)
    (hderiv : ∀ t, 0 ≤ t →
      HasDerivAt (fun s ↦ -Real.log (SurvivalModel.survive P X s x)) (μ t) t) :
    ∀ t, 0 ≤ t → SurvivalModel.survive P X t x = survivalFromForce μ t := by sorry

/-- **Gompertz instance of the bridge**, analogous to Ito's `Examples`: a `Survival_Model`
whose force of mortality is the Gompertz law `μ u = B · exp(c u)` has `tpₓ` reconstructing as
`survivalFromForce` at that force, in closed form `exp(-(B/c) · (exp(c t) − 1))` (combining
the bridge theorem with `gompertz_cumulative_force`). -/
theorem survive_eq_gompertz
    (hX : Measurable X) [IsProbabilityMeasure P] (x : ℝ)
    (hx : P (SurvivalModel.alive X x) ≠ 0) (B c : ℝ) (hc : c ≠ 0)
    (hderiv : ∀ t, 0 ≤ t → HasDerivAt
      (fun s ↦ -Real.log (SurvivalModel.survive P X s x)) (B * Real.exp (c * t)) t) :
    ∀ t, 0 ≤ t → SurvivalModel.survive P X t x
        = survivalFromForce (fun u ↦ B * Real.exp (c * u)) t
      ∧ survivalFromForce (fun u ↦ B * Real.exp (c * u)) t
        = Real.exp (-(B / c) * (Real.exp (c * t) - 1)) := by
  intro t ht
  refine ⟨survive_eq_survivalFromForce_of_force hX (P := P) x hx
    (fun u ↦ B * Real.exp (c * u)) (by fun_prop) hderiv t ht, ?_⟩
  unfold survivalFromForce survivalFromIntensity
  rw [show cumulativeIntensity (fun u ↦ B * Real.exp (c * u)) t
        = ∫ u in (0:ℝ)..t, B * Real.exp (c * u) from rfl,
      gompertz_cumulative_force B c t hc]
  ring_nf

example (B c t : ℝ) (hc : c ≠ 0) :
    survivalFromForce (fun u ↦ B * Real.exp (c * u)) t =
      Real.exp (-(B / c) * (Real.exp (c * t) - 1)) := by
  unfold survivalFromForce survivalFromIntensity
  rw [show cumulativeIntensity (fun u ↦ B * Real.exp (c * u)) t
        = ∫ u in (0:ℝ)..t, B * Real.exp (c * u) from rfl,
      gompertz_cumulative_force B c t hc]
  ring_nf

end MathFin
