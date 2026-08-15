/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.FixedIncome.DurationSensitivity
public import MathFin.FixedIncome.ZCB
public import MathFin.FixedIncome.Immunization

-- pointers: MathFin/FixedIncome/DurationSensitivity.lean, MathFin/FixedIncome/ZCB.lean, MathFin/FixedIncome/Immunization.lean
-- main-module: MathFin/FixedIncome/KeyRateDuration.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-fixedincome-key-rate-duration
-- source-issue: 69

/-!
Key-rate duration of a multi-tenor bond via HasDerivAt on a single tenor's spot rate, and the identity that key-rate durations sum to the parallel-shift effective duration.
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

/-- Multi-tenor bond price: the sum over tenors `i ∈ s` of the zero-coupon-bond
price of cashflow `c i` maturing at `T i`, discounted at that tenor's own spot
rate `ρ i`. Under a flat curve `ρ i = y` for all `i` this reduces to the usual
discrete-cashflow bond price. -/
noncomputable def P {ι : Type*} (s : Finset ι) (T c : ι → ℝ) (ρ : ι → ℝ) : ℝ :=
  ∑ i ∈ s, c i * zcb (ρ i) 0 (T i)

/-- **Key-rate duration** of tenor `k`: the percentage price sensitivity of `P`
to a shift in `rₖ` alone, with every other tenor's rate held fixed. -/
noncomputable def KRD {ι : Type*} (s : Finset ι) (T c r : ι → ℝ) (k : ι) : ℝ :=
  (c k * T k * zcb (r k) 0 (T k)) / P s T c r

/-- **Effective (parallel-shift) duration**: the percentage price sensitivity of
`P` to a uniform shift `h` applied to every tenor's rate simultaneously. -/
noncomputable def ED {ι : Type*} (s : Finset ι) (T c r : ι → ℝ) : ℝ :=
  -(1 / P s T c r) * (-(∑ j ∈ s, c j * T j * zcb (r j) 0 (T j)))

example : P ({0} : Finset ℕ) (fun _ => (3 : ℝ)) (fun _ => (5 : ℝ)) (fun _ => (0 : ℝ)) = 5 := by
  norm_num [P, zcb]

example : KRD ({0} : Finset ℕ) (fun _ => (3 : ℝ)) (fun _ => (5 : ℝ)) (fun _ => (0 : ℝ)) 0 = 3 := by
  norm_num [KRD, P, zcb]

example : ED ({0} : Finset ℕ) (fun _ => (3 : ℝ)) (fun _ => (5 : ℝ)) (fun _ => (0 : ℝ)) = 3 := by
  norm_num [ED, P, zcb]

/-- **Key-rate duration cluster**: (i) the price slice `x ↦ P (update r k x)`,
viewing `P` as a function of tenor `k`'s spot rate alone, has derivative
`-(c k * T k * zcb (r k) 0 (T k))` at `r k`; (ii) the parallel-shift price
path `h ↦ P (r + h)` has derivative `-(Σ c k * T k * zcb (r k) 0 (T k))` at
`h = 0`; and (iii) the key-rate durations aggregate exactly to the effective
duration, `Σ KRD k = ED`, unconditionally (no hypothesis `P r ≠ 0` is needed,
since the algebraic rearrangement in (iii) holds under `x / 0 = 0`). -/
theorem hasDerivAt_P_singleTenor_and_parallelShift_and_krd_sum_eq_ed
    {ι : Type*} [DecidableEq ι] (s : Finset ι) (T c r : ι → ℝ) (k : ι) (hk : k ∈ s) :
    HasDerivAt (fun x => P s T c (Function.update r k x))
        (-(c k * T k * zcb (r k) 0 (T k))) (r k) ∧
    HasDerivAt (fun h => P s T c (fun i => r i + h))
        (-(∑ j ∈ s, c j * T j * zcb (r j) 0 (T j))) 0 ∧
    ∑ j ∈ s, KRD s T c r j = ED s T c r := by sorry

end MathFin
