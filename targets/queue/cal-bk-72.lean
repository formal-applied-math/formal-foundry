/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.Choice.Portfolio.SharpeFOCDerivation
public import MathFin.Choice.Portfolio.RiskParityFOC
public import MathFin.Choice.Performance.Kelly

-- pointers: MathFin/Choice/Portfolio/SharpeFOCDerivation.lean, MathFin/Choice/Portfolio/RiskParityFOC.lean, MathFin/Choice/Performance/Kelly.lean
-- main-module: MathFin/Choice/Performance/MertonFraction.lean
-- benchmark: benchmarks/portfolio_and_performance.json
-- benchmark-id: mf-portfolio-merton-myopic-fraction
-- source-issue: 72
-- deferred: full HJB value function V(t,w) for CRRA utility; comparison with the discrete Kelly fraction as a limit; derivation of the quadratic objective from Itô's formula on the wealth process
-- blueprint: thm:merton-portfolio-fraction

/-!
# Merton's myopic optimal risky fraction as the first-order maximiser

For γ>0, σ≠0 the objective π(μ−r)−(γσ²/2)π² has unique critical point and global maximiser
π*=(μ−r)/(γσ²), with value (μ−r)²/(2γσ²).

## Main results

* `logBarrierObj_unit`: Merton's myopic optimal risky fraction as the first-order maximiser

## References

* [Merton, *An analytic derivation of the efficient portfolio frontier*][Merton1972]

## Tags

merton, optimal fraction, crra, mean-variance, first-order condition, growth-optimal, JEL C13, JEL
C58, MSC 62G30, MSC 62P05
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

/-- Certainty-equivalent growth `π(μ−r) − (γσ²/2)π²` of the risky fraction `π`. -/
noncomputable def mertonObjective (γ μ r σ π : ℝ) : ℝ :=
  π * (μ - r) - (γ * σ ^ 2 / 2) * π ^ 2

/-- The quadratic penalty is the one-asset `logBarrierObj` with zero budget. -/
theorem logBarrierObj_unit (γ σ π : ℝ) :
    logBarrierObj (Finset.univ : Finset Unit) (fun _ _ => γ * σ ^ 2) 0 (fun _ => π)
      = (γ * σ ^ 2 / 2) * π ^ 2 := by
  simp [logBarrierObj, portfolioVarN]
  ring

/-- The Merton objective is the mean excess return minus the one-asset risk objective. -/
theorem mertonObjective_eq_sub_logBarrierObj (γ μ r σ π : ℝ) :
    mertonObjective γ μ r σ π = π * (μ - r) -
      logBarrierObj (Finset.univ : Finset Unit) (fun _ _ => γ * σ ^ 2) 0 (fun _ => π) := by
  rw [logBarrierObj_unit]; rfl

example : mertonObjective 2 3 1 1 1 = 1 := by norm_num [mertonObjective]

example : mertonObjective 1 0 0 1 0 = 0 := by norm_num [mertonObjective]

/-- Merton's myopic fraction `π* = (μ−r)/(γσ²)` is the unique critical point and global
maximiser of the strictly concave objective, with value `(μ−r)²/(2γσ²)`. -/
theorem merton_fraction_first_order_maximiser (γ μ r σ : ℝ) (hγ : 0 < γ) (hσ : σ ≠ 0) :
    (∀ π, HasDerivAt (mertonObjective γ μ r σ) ((μ - r) - γ * σ ^ 2 * π) π) ∧
    (∀ π, (μ - r) - γ * σ ^ 2 * π = 0 ↔ π = (μ - r) / (γ * σ ^ 2)) ∧
    ((∀ π, HasDerivAt (fun x : ℝ => (μ - r) - γ * σ ^ 2 * x) (-(γ * σ ^ 2)) π) ∧
      -(γ * σ ^ 2) < 0) ∧
    (∀ π, mertonObjective γ μ r σ π ≤ mertonObjective γ μ r σ ((μ - r) / (γ * σ ^ 2))) ∧
    mertonObjective γ μ r σ ((μ - r) / (γ * σ ^ 2)) = (μ - r) ^ 2 / (2 * γ * σ ^ 2) := by
  sorry

end MathFin
