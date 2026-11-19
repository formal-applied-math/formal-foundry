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
-- deferred: full HJB value function V(t,w); derivation of the quadratic objective from CRRA utility under GBM dynamics
-- blueprint: thm:merton-portfolio-fraction

/-!
# Merton myopic optimal risky fraction

The Merton objective π(μ−r) − (γσ²/2)π² has unique critical point and global maximiser π* =
(μ−r)/(γσ²) with value (μ−r)²/(2γσ²), expressed through the single-asset risk contribution.

## Main results

* `merton_myopic_fraction`: Merton myopic optimal risky fraction

## References

* [Karatzas and Shreve, *Methods of Mathematical Finance*][KaratzasShreve1998]

## Tags

merton, optimal fraction, crra, mean-variance, first-order condition, risky fraction, JEL C13, JEL
C58, MSC 62G30, MSC 62P05
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

/-- The Merton objective `π (μ - r) - (γ σ² / 2) π²`. -/
noncomputable def mertonObjective (γ μ r σ π : ℝ) : ℝ :=
  π * (μ - r) - (γ * σ ^ 2 / 2) * π ^ 2

example : mertonObjective 2 3 1 1 1 = 1 := by norm_num [mertonObjective]
example : mertonObjective 1 0 0 1 2 = -2 := by norm_num [mertonObjective]

/-- Merton myopic fraction: the one-asset risk contribution is `π² σ²`, the objective is
the excess return minus `γ/2` times it, and for `γ > 0`, `σ ≠ 0` the fraction
`π* = (μ - r) / (γ σ²)` is the unique critical point and global maximiser, with value
`(μ - r)² / (2 γ σ²)`. -/
theorem merton_myopic_fraction (γ μ r σ : ℝ) (hγ : 0 < γ) (hσ : σ ≠ 0) :
    (∀ π : ℝ, riskContribution (Finset.univ : Finset Unit) (fun _ _ => σ ^ 2) (fun _ => π) ()
        = π ^ 2 * σ ^ 2) ∧
    (∀ π : ℝ, mertonObjective γ μ r σ π = π * (μ - r) - (γ / 2) *
        riskContribution (Finset.univ : Finset Unit) (fun _ _ => σ ^ 2) (fun _ => π) ()) ∧
    (∀ π : ℝ, IsRiskParity (Finset.univ : Finset Unit) (fun _ _ => σ ^ 2) (fun _ => π)) ∧
    (∀ π : ℝ, HasDerivAt (mertonObjective γ μ r σ) (μ - r - γ * σ ^ 2 * π) π) ∧
    (∀ π : ℝ, HasDerivAt (mertonObjective γ μ r σ) 0 π ↔ π = (μ - r) / (γ * σ ^ 2)) ∧
    (∀ π : ℝ, HasDerivAt (fun x : ℝ => μ - r - γ * σ ^ 2 * x) (-(γ * σ ^ 2)) π) ∧
    -(γ * σ ^ 2) < 0 ∧
    (∀ π : ℝ, mertonObjective γ μ r σ π ≤
        mertonObjective γ μ r σ ((μ - r) / (γ * σ ^ 2))) ∧
    mertonObjective γ μ r σ ((μ - r) / (γ * σ ^ 2)) = (μ - r) ^ 2 / (2 * γ * σ ^ 2) := by
  sorry

end MathFin
