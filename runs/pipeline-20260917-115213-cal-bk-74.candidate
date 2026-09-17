/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.RiskMeasures.CoherentAxioms
public import MathFin.RiskMeasures.Spectral
public import MathFin.RiskMeasures.Gaussian

-- pointers: MathFin/RiskMeasures/CoherentAxioms.lean, MathFin/RiskMeasures/Spectral.lean, MathFin/RiskMeasures/Gaussian.lean
-- main-module: MathFin/RiskMeasures/Entropic.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-riskmeasures-entropic
-- source-issue: 74
-- new-defs: {'name': 'entropicRisk', 'signature': '{Ω : Type*} → [MeasurableSpace Ω] → (θ : ℝ) → (X : Ω → ℝ) → (P : MeasureTheory.Measure Ω) → ℝ', 'meaning': 'θ⁻¹ * Real.log (ProbabilityTheory.mgf X P (-θ)), the entropic risk measure of X at risk-aversion θ', 'built_from': ['ProbabilityTheory.mgf', 'Real.log', 'Inv.inv']}, {'name': 'entropicRiskGaussian', 'signature': '(μ σ θ : ℝ) → ℝ', 'meaning': '-μ + (θ/2) * σ^2, the closed-form entropic risk value for X ~ N(μ, σ²)', 'built_from': ['Neg.neg', 'HAdd.hAdd', 'HMul.hMul', 'HPow.hPow']}

/-!
Entropic risk measure ρ_θ(X) = θ⁻¹ log E[e^{-θX}]: cash-invariance, convexity, monotonicity, and the Gaussian closed form -μ + (θ/2)σ².
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

/-- The entropic risk measure `ρ_θ(X) = θ⁻¹ log E[e^{-θX}]`, via the moment
generating function of `X` at `-θ`. -/
noncomputable def entropicRisk {Ω : Type*} [MeasurableSpace Ω]
    (θ : ℝ) (X : Ω → ℝ) (P : Measure Ω) : ℝ :=
  θ⁻¹ * Real.log (mgf X P (-θ))

/-- Closed-form entropic risk value `-μ + (θ/2)·σ²` for `X ~ N(μ, σ²)`. -/
noncomputable def entropicRiskGaussian (μ σ θ : ℝ) : ℝ :=
  -μ + (θ / 2) * σ ^ 2

example : entropicRiskGaussian 0 1 2 = 1 := by
  unfold entropicRiskGaussian; norm_num

example : entropicRiskGaussian 3 0 5 = -3 := by
  unfold entropicRiskGaussian; norm_num

example {Ω : Type*} [MeasurableSpace Ω] (θ : ℝ) (P : Measure Ω) [IsProbabilityMeasure P] :
    entropicRisk θ (fun _ => (0 : ℝ)) P = 0 := by
  simp [entropicRisk]

/-- **Entropic risk measure**: cash-invariance, convexity, monotonicity, and the
Gaussian closed form `-μ + (θ/2)·σ²`, for `θ > 0` and `X, Y : Ω → ℝ` each satisfying
only the single-point integrability of `exp(-θ·X)` / `exp(-θ·Y)` needed to make
`entropicRisk` itself well-defined at `-θ` — no assumption of MGF finiteness at
every real argument. -/
theorem entropicRisk_properties
    {Ω : Type*} [MeasurableSpace Ω] (P : Measure Ω) [IsProbabilityMeasure P]
    {θ : ℝ} (hθ : 0 < θ) {X Y : Ω → ℝ}
    (hX : Integrable (fun ω => Real.exp (-θ * X ω)) P)
    (hY : Integrable (fun ω => Real.exp (-θ * Y ω)) P) :
    (∀ c : ℝ, entropicRisk θ (fun ω => X ω + c) P = entropicRisk θ X P - c) ∧
    (∀ l : ℝ, l ∈ Set.Icc (0 : ℝ) 1 →
      entropicRisk θ (fun ω => l * X ω + (1 - l) * Y ω) P ≤
        l * entropicRisk θ X P + (1 - l) * entropicRisk θ Y P) ∧
    (X ≤ᵐ[P] Y → entropicRisk θ Y P ≤ entropicRisk θ X P) ∧
    (∀ (μ σ : ℝ), 0 ≤ σ → ∀ v : ℝ≥0, (v : ℝ) = σ ^ 2 →
      Measure.map X P = gaussianReal μ v →
      entropicRisk θ X P = entropicRiskGaussian μ σ θ) := by
  sorry

end MathFin
