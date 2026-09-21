/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.Actuarial.Insurance
public import MathFin.Actuarial.Mortality

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

-- pointers: MathFin/Actuarial/Insurance.lean, MathFin/Actuarial/Mortality.lean
-- main-module: MathFin/Actuarial/WholeLifeInsuranceEPV.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-actuarial-whole-life-insurance-epv
-- source-issue: 83
-- deferred: infinite-series (tsum) formulation of A_x for survival functions with no finite terminal age; n-year pure endowment and endowment insurance EPV as separately named objects
-- new-defs: {'name': 'annuityDueEPV', 'signature': 'ℝ → (ℕ → ℝ) → ℕ → ℝ', 'meaning': 'The n-year life annuity-due EPV ä_x = ∑_{k=0}^{n-1} v^k · ₖpₓ for discount factor v and survival sequence S (S k = ₖpₓ).', 'built_from': ['Finset.range', 'Finset.sum', 'HPow.hPow', 'HMul.hMul']}, {'name': 'insuranceEPV', 'signature': 'ℝ → (ℕ → ℝ) → (ℕ → ℝ) → ℕ → ℝ', 'meaning': 'The n-year term life insurance EPV A_x = ∑_{k=0}^{n-1} v^{k+1} · ₖpₓ · q_{x+k} for discount factor v, mortality sequence q and survival sequence S.', 'built_from': ['Finset.range', 'Finset.sum', 'HPow.hPow', 'HMul.hMul']}, {'name': 'isLifeTable', 'signature': '(ℕ → ℝ) → (ℕ → ℝ) → Prop', 'meaning': 'S and q form a consistent discrete life table: S 0 = 1 and S (k+1) = S k * (1 - q k) for every k.', 'built_from': ['Eq', 'And', 'HMul.hMul', 'HSub.hSub']}






/-- `S` and `q` form a consistent discrete life table: certain survival at
issue (`S 0 = 1`) and the standard recursion `ₖ₊₁pₓ = ₖpₓ · (1 - q_{x+k})`. -/
noncomputable def isLifeTable (S q : ℕ → ℝ) : Prop :=
  S 0 = 1 ∧ ∀ k : ℕ, S (k + 1) = S k * (1 - q k)

/-- The `n`-year life annuity-due EPV `ä_x = ∑_{k=0}^{n-1} v^k · ₖpₓ`. -/
noncomputable def annuityDueEPV (v : ℝ) (S : ℕ → ℝ) (n : ℕ) : ℝ :=
  ∑ k ∈ Finset.range n, v ^ k * S k

/-- The `n`-year term life insurance EPV `A_x = ∑_{k=0}^{n-1} v^{k+1} · ₖpₓ · q_{x+k}`. -/
noncomputable def insuranceEPV (v : ℝ) (q S : ℕ → ℝ) (n : ℕ) : ℝ :=
  ∑ k ∈ Finset.range n, v ^ (k + 1) * S k * q k

example : isLifeTable (fun _ : ℕ => (1 : ℝ)) (fun _ : ℕ => 0) :=
  ⟨by norm_num, fun k => by norm_num⟩

example : annuityDueEPV 1 (fun _ : ℕ => (1 : ℝ)) 3 = 3 := by
  norm_num [annuityDueEPV, Finset.sum_range_succ]

example : insuranceEPV 1 (fun _ : ℕ => (0.1 : ℝ)) (fun _ : ℕ => 1) 2 = 0.2 := by
  norm_num [insuranceEPV, Finset.sum_range_succ]

/-- **Fundamental identity of life contingencies**: for a discrete life table
`S, q` with terminal age `ω` (survival to `ω` is impossible, `S ω = 0`), the
whole-life term insurance EPV satisfies `A_x = 1 - d · ä_x`, where
`d = 1 - v` is the discount rate. This is the `n = ω` case of the general
telescoping identity `insuranceEPV v q S n = 1 - v^n * S n - d * annuityDueEPV v S n`,
simplified by `v^ω * S ω = v^ω * 0 = 0`. -/

-- apply: Finset.sum_range_succ, isLifeTable
theorem insuranceEPV_telescoping_step (v : ℝ) (S q : ℕ → ℝ) (h : isLifeTable S q) (k : ℕ) (ih : insuranceEPV v q S k = 1 - v ^ k * S k - (1 - v) * annuityDueEPV v S k) : insuranceEPV v q S (k + 1) = 1 - v ^ (k + 1) * S (k + 1) - (1 - v) * annuityDueEPV v S (k + 1) := by sorry

end MathFin
