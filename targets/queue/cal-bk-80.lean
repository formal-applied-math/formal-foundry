/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.RiskMeasures.UtilityDerivation
public import MathFin.RiskMeasures.Gaussian

-- pointers: MathFin/RiskMeasures/UtilityDerivation.lean, MathFin/RiskMeasures/Gaussian.lean
-- main-module: MathFin/RiskMeasures/CertaintyEquivalent.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-riskmeasures-certainty-equivalent-cara-gaussian
-- source-issue: 80
-- deferred: bridge certaintyEquivalent (caraUtility a) X = -entropicRisk a X (depends on entropic-risk issue landing first)

/-!
Certainty equivalent under CARA utility for Gaussian wealth equals μ − (a/2)σ², with nonnegative risk premium and an acceptance-set bridge to UtilityDerivation.
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

/-- The certainty equivalent of a strictly increasing utility `w` for a random
variable `Y` under `P`: the value `CE` solving `w CE = ∫ ω, w (Y ω) ∂P`,
realized as `Function.invFun w` applied to the expected utility. -/
noncomputable def certaintyEquivalent {Ω : Type*} [MeasurableSpace Ω]
    (w : ℝ → ℝ) (Y : Ω → ℝ) (P : Measure Ω) : ℝ :=
  Function.invFun w (∫ ω, w (Y ω) ∂P)

/-- The CARA (constant absolute risk aversion) utility `u(x) = -exp(-a·x)`
with risk-aversion coefficient `a`. -/
noncomputable def caraUtility (a x : ℝ) : ℝ := -Real.exp (-a * x)

example : caraUtility 1 0 = -1 := by simp [caraUtility]

example {Ω : Type*} [MeasurableSpace Ω] (P : Measure Ω) (Y : Ω → ℝ) :
    certaintyEquivalent id Y P = Function.invFun id (∫ ω, Y ω ∂P) := by
  simp [certaintyEquivalent]

/-- **CARA certainty equivalent under Gaussian wealth.** For risk-aversion
`a > 0`, CARA utility `caraUtility a`, and wealth `X` whose law under `P` is
`N(μ, σ²)`, the certainty equivalent equals `MathFin.gaussianVaR μ σ 0` (the
Gaussian VaR at the median quantile `z = 0`, which is `μ`) minus the variance
penalty `(a/2)·σ²`; equivalently the risk premium `gaussianVaR μ σ 0 − CE`
equals `(a/2)·σ²` and is nonnegative, expressing that a CARA risk-averse
agent's certainty equivalent never exceeds the mean. The Gaussian moment
generating function used internally is derived from Mathlib's gaussian-law
lemmas, not assumed. -/
theorem certaintyEquivalent_caraUtility_gaussianReal
    {Ω : Type*} [MeasurableSpace Ω] (P : Measure Ω) [IsProbabilityMeasure P]
    (a : ℝ) (ha : 0 < a) (μ σ : ℝ) (hσ : 0 ≤ σ) (X : Ω → ℝ)
    (hX : Measure.map X P = ProbabilityTheory.gaussianReal μ (σ ^ 2).toNNReal) :
    certaintyEquivalent (caraUtility a) X P = gaussianVaR μ σ 0 - (a / 2) * σ ^ 2 ∧
    gaussianVaR μ σ 0 - certaintyEquivalent (caraUtility a) X P = (a / 2) * σ ^ 2 ∧
    0 ≤ gaussianVaR μ σ 0 - certaintyEquivalent (caraUtility a) X P := by
  sorry

/-- **Acceptance bridge.** For a finite index type `ι` with discrete
probabilities `p` on `s : Finset ι` and the discrete probability measure
`P = ∑ i ∈ s, ENNReal.ofReal (p i) • Measure.dirac i` (so
`∫ i, u (Y i) ∂P = ∑ i ∈ s, p i * u (Y i)`), `MathFin.acceptableUnderUtility`
at baseline `0` is exactly nonnegativity of the certainty equivalent, for any
strictly increasing and surjective utility `u`. Surjectivity is what makes
`certaintyEquivalent` an honest inverse at the specific value
`∑ i ∈ s, p i * u (Y i)`, so the equivalence is sound (a merely strictly
increasing `u`, e.g. one with a jump, need not attain that weighted
average). -/
theorem acceptableUnderUtility_iff_certaintyEquivalent_nonneg
    {ι : Type*} [MeasurableSpace ι] [MeasurableSingletonClass ι]
    (s : Finset ι) (p : ι → ℝ) (hp : ∀ i ∈ s, 0 ≤ p i) (hp_sum : ∑ i ∈ s, p i = 1)
    (u : ℝ → ℝ) (hu : StrictMono u) (hu_surj : Function.Surjective u) (Y : ι → ℝ) :
    acceptableUnderUtility s p u 0 Y ↔
      0 ≤ certaintyEquivalent u Y (∑ i ∈ s, ENNReal.ofReal (p i) • Measure.dirac i) := by
  have hkey : ∀ c : ℝ, 0 ≤ Function.invFun u c ↔ u 0 ≤ c := by
    intro c
    have h1 : u (Function.invFun u c) = c := Function.invFun_eq (hu_surj c)
    conv_rhs => rw [← h1]
    rw [hu.le_iff_le]
  have hint : ∫ i, u (Y i) ∂(∑ i ∈ s, ENNReal.ofReal (p i) • Measure.dirac i)
      = ∑ i ∈ s, p i * u (Y i) := by
    have hf : ∀ i ∈ s, Integrable (fun ω => u (Y ω)) (ENNReal.ofReal (p i) • Measure.dirac i) := by
      intro i _
      refine Integrable.smul_measure ?_ ENNReal.ofReal_ne_top
      exact integrable_dirac (by simp)
    rw [MeasureTheory.integral_finsetSum_measure hf]
    refine Finset.sum_congr rfl (fun i hi => ?_)
    rw [integral_smul_measure, integral_dirac, ENNReal.toReal_ofReal (hp i hi)]
    rfl
  unfold certaintyEquivalent acceptableUnderUtility
  rw [hint, hkey]
  simp

end MathFin
