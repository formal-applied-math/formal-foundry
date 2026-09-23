/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.DeFi.ConstantProductAMM

-- pointers: MathFin/DeFi/ConstantProductAMM.lean
-- main-module: MathFin/DeFi/ConstantMeanAMM.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-defi-constant-mean-invariant
-- source-issue: 91

/-!
Defines the Balancer constant-mean invariant and spot price, derives the spot price as the invariant surface's marginal rate of substitution, and reduces both to the two-token constant-product AMM at equal weights.
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

/-- **Balancer constant-mean invariant**: for a finite index type `ι` of
tokens with reserves `B` and weights `w` (with `∑ w i = 1`), the pool
invariant is the weighted geometric mean of the reserves,
`∏ i, (B i) ^ (w i)`, using `Real.rpow` for the real exponent. -/
noncomputable def constantMeanInvariant {ι : Type*} [Fintype ι]
    (w B : ι → ℝ) : ℝ :=
  ∏ i, (B i) ^ (w i)

/-- **Balancer spot price** of token `i` in units of token `j`: the
reserve-and-weight ratio `(B j / w j) / (B i / w i)`. -/
noncomputable def constantMeanSpotPrice {ι : Type*} [Fintype ι]
    (w B : ι → ℝ) (i j : ι) : ℝ :=
  (B j / w j) / (B i / w i)

example : constantMeanInvariant (ι := Fin 2) ![(1 : ℝ) / 2, 1 / 2] ![4, 9] = 6 := by
  simp [constantMeanInvariant, Fin.prod_univ_two]
  rw [show (4 : ℝ) = 2 ^ (2 : ℕ) by norm_num, show (9 : ℝ) = 3 ^ (2 : ℕ) by norm_num,
    ← Real.rpow_natCast (2 : ℝ) 2, ← Real.rpow_natCast (3 : ℝ) 2,
    ← Real.rpow_mul (by norm_num), ← Real.rpow_mul (by norm_num)]
  norm_num

example : constantMeanSpotPrice (ι := Fin 2) ![(1 : ℝ) / 2, 1 / 2] ![10, 20] 0 1 = 2 := by
  simp [constantMeanSpotPrice]
  norm_num

/-- The Balancer spot price is the marginal rate of substitution of the
invariant surface: freezing all coordinates except `i` (resp. `j`), the
derivative of the resulting single-variable invariant function at the
current reserve is `w i * constantMeanInvariant w B / B i` (resp. for
`j`), and the ratio of these two derivatives is exactly the spot price.
The reduction at equal weights `w = ![1/2, 1/2]` on two tokens recovers
the constant-product invariant `x * y` (squared) and its spot price
`MathFin.DeFi.internalPrice x y = y / x`. -/
theorem constantMean_spotPrice_eq_marginalRate_and_reducesToConstantProduct
    {ι : Type*} [Fintype ι] [DecidableEq ι] (w B : ι → ℝ)
    (hB : ∀ i, 0 < B i) (hw : ∀ i, 0 < w i) (hw1 : ∑ i, w i = 1)
    {i j : ι} (hij : i ≠ j) (x y : ℝ) (hx : 0 < x) (hy : 0 < y) :
    deriv (fun t => constantMeanInvariant w (Function.update B i t)) (B i)
        = w i * constantMeanInvariant w B / B i ∧
    deriv (fun t => constantMeanInvariant w (Function.update B j t)) (B j)
        = w j * constantMeanInvariant w B / B j ∧
    deriv (fun t => constantMeanInvariant w (Function.update B i t)) (B i) /
        deriv (fun t => constantMeanInvariant w (Function.update B j t)) (B j)
        = constantMeanSpotPrice w B i j ∧
    (constantMeanInvariant (ι := Fin 2) ![(1 : ℝ) / 2, 1 / 2] ![x, y]) ^ 2 = x * y ∧
    constantMeanSpotPrice (ι := Fin 2) ![(1 : ℝ) / 2, 1 / 2] ![x, y] 0 1
        = MathFin.DeFi.internalPrice x y := by sorry

end MathFin
