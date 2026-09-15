/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.RiskMeasures.Spectral
public import MathFin.RiskMeasures.Gaussian

-- pointers: MathFin/RiskMeasures/Spectral.lean, MathFin/RiskMeasures/Gaussian.lean
-- main-module: MathFin/RiskMeasures/Distortion.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-riskmeasures-distortion-wang
-- source-issue: 75
-- deferred: concave g ⇒ subadditivity (hence coherence) of distortionRiskFinite for general, non-comonotonic loss vectors

/-!
Finite-Ω distortion risk measure: spectral representation, translation invariance, positive homogeneity, monotonicity, and a Wang-transform distortion instance valid for all λ and concave for λ ≥ 0.
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

/-- Survival (tail) probability `S i := ∑_{j ≥ i} p j` on the finite ordered
grid `Fin n`, ranked by increasing loss. -/
noncomputable def survivalProb {n : ℕ} (p : Fin n → ℝ) (i : Fin n) : ℝ :=
  ∑ j ∈ Finset.univ.filter (fun j => i ≤ j), p j

/-- `S` shifted down one rank: the survival probability at the NEXT-worse
rank, `0` at the top rank (`Fin.last`). -/
noncomputable def survivalNext {n : ℕ} (p : Fin n → ℝ) (i : Fin n) : ℝ :=
  if h : i.val + 1 < n then survivalProb p ⟨i.val + 1, h⟩ else 0

/-- A distortion function: monotone on `[0,1]` with `g 0 = 0` and `g 1 = 1`. -/
noncomputable def IsDistortion (g : ℝ → ℝ) : Prop :=
  MonotoneOn g (Set.Icc (0 : ℝ) 1) ∧ g 0 = 0 ∧ g 1 = 1

/-- Finite-Ω distortion risk measure: `∑ i, (g (S i) - g (S⁺ i)) * Q i`. -/
noncomputable def distortionRiskFinite {n : ℕ} (g : ℝ → ℝ) (p Q : Fin n → ℝ) : ℝ :=
  ∑ i : Fin n, (g (survivalProb p i) - g (survivalNext p i)) * Q i

/-- The standard normal CDF `Φ`, read off Mathlib's Gaussian-measure CDF. -/
noncomputable def stdNormalCDF : ℝ → ℝ :=
  fun x => ProbabilityTheory.cdf (ProbabilityTheory.gaussianReal 0 1) x

/-- `Φ⁻¹`, via the choice-based generalized inverse (Mathlib carries no
closed-form quantile API at the current pin; see
`MathFin/RiskMeasures/Gaussian.lean`). -/
noncomputable def stdNormalQuantile : ℝ → ℝ := Function.invFun stdNormalCDF

/-- The Wang transform `g_λ u := Φ (Φ⁻¹ u + λ)` on `(0,1)`, extended by
`g_λ 0 = 0` and `g_λ 1 = 1`. -/
noncomputable def wangTransform (lam : ℝ) : ℝ → ℝ :=
  fun u => if u = 0 then 0 else if u = 1 then 1
    else stdNormalCDF (stdNormalQuantile u + lam)

example : survivalProb (n := 2) (fun i => if i = 0 then (0.4 : ℝ) else 0.6) 0 = 1 := by
  simp [survivalProb, Fin.sum_univ_two]; norm_num

example : survivalNext (n := 2) (fun i => if i = 0 then (0.4 : ℝ) else 0.6) 0 = 0.6 := by
  simp only [survivalNext, survivalProb, Finset.sum_filter, Fin.sum_univ_two]
  norm_num

example : IsDistortion (id : ℝ → ℝ) := ⟨fun _ _ _ _ hxy => hxy, rfl, rfl⟩

example :
    distortionRiskFinite (id : ℝ → ℝ) (fun i : Fin 2 => if i = 0 then (0.4 : ℝ) else 0.6)
      (fun i : Fin 2 => if i = 0 then (1 : ℝ) else 2) = 1.6 := by
  simp only [distortionRiskFinite, survivalProb, survivalNext, Finset.sum_filter,
    Fin.sum_univ_two, id]
  norm_num

example (lam : ℝ) : wangTransform lam 0 = 0 := by simp [wangTransform]
example (lam : ℝ) : wangTransform lam 1 = 1 := by simp [wangTransform]

/-- **Finite-Ω distortion risk measure**, in six parts: (a) spectral
representation — `distortionRiskFinite g p Q` equals `spectralRiskFinite` on
the spectrum of `g`-increments of the survival function, for EVERY loss grid
`Q` (no monotonicity hypothesis needed); (b) translation invariance; (c)
positive homogeneity; (d) monotonicity in the loss grid; (e) the Wang
transform `g_λ` is a distortion function for every `λ`, and concave on
`[0,1]` when `λ ≥ 0` (the derivative `d/du g_λ(u) = exp(-λ·Φ⁻¹(u) - λ²/2)` has
derivative sign `-λ`, so concavity is exactly the `λ ≥ 0` direction); (f) a
Gaussian-VaR loss grid on a monotone quantile grid is itself monotone, and
the Wang-transformed distortion risk of that grid is a concrete spectral-risk
instance. -/
theorem distortionRiskFinite_spectral_translation_homogeneity_monotone_wang_gaussianVaR :
    (∀ (n : ℕ) (p : Fin n → ℝ), (∀ i, 0 ≤ p i) → ∑ i, p i = 1 →
        ∀ (g : ℝ → ℝ), IsDistortion g → ∀ (Q : Fin n → ℝ),
          distortionRiskFinite g p Q =
            MathFin.spectralRiskFinite Finset.univ
              (fun i => g (survivalProb p i) - g (survivalNext p i)) Q)
    ∧ (∀ (n : ℕ) (p : Fin n → ℝ), (∀ i, 0 ≤ p i) → ∑ i, p i = 1 →
        ∀ (g : ℝ → ℝ), IsDistortion g → ∀ (Q : Fin n → ℝ) (c : ℝ),
          distortionRiskFinite g p (fun i => Q i + c) = distortionRiskFinite g p Q + c)
    ∧ (∀ (n : ℕ) (p : Fin n → ℝ), (∀ i, 0 ≤ p i) → ∑ i, p i = 1 →
        ∀ (g : ℝ → ℝ), IsDistortion g → ∀ (Q : Fin n → ℝ) (c : ℝ), 0 ≤ c →
          distortionRiskFinite g p (fun i => c * Q i) = c * distortionRiskFinite g p Q)
    ∧ (∀ (n : ℕ) (p : Fin n → ℝ), (∀ i, 0 ≤ p i) → ∑ i, p i = 1 →
        ∀ (g : ℝ → ℝ), IsDistortion g → ∀ (Q Q' : Fin n → ℝ), (∀ i, Q i ≤ Q' i) →
          distortionRiskFinite g p Q ≤ distortionRiskFinite g p Q')
    ∧ (∀ lam : ℝ, IsDistortion (wangTransform lam) ∧
        (0 ≤ lam → ConcaveOn ℝ (Set.Icc (0 : ℝ) 1) (wangTransform lam)))
    ∧ (∀ (n : ℕ) (z : Fin n → ℝ), Monotone z → ∀ (μ σ : ℝ), 0 ≤ σ →
        Monotone (fun i => MathFin.gaussianVaR μ σ (z i)) ∧
        ∀ (p : Fin n → ℝ), (∀ i, 0 ≤ p i) → ∑ i, p i = 1 → ∀ lam : ℝ,
          distortionRiskFinite (wangTransform lam) p (fun i => MathFin.gaussianVaR μ σ (z i)) =
            MathFin.spectralRiskFinite Finset.univ
              (fun i => wangTransform lam (survivalProb p i) - wangTransform lam (survivalNext p i))
              (fun i => MathFin.gaussianVaR μ σ (z i))) := by
  sorry

end MathFin
