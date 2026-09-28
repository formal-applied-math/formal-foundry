/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.BlackScholes.MertonJumpDiffusion

-- pointers: MathFin/BlackScholes/MertonJumpDiffusion.lean
-- main-module: MathFin/BlackScholes/MertonJumpDiffusionDelta.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-merton-jd-delta
-- source-issue: 129
-- deferred: gamma mixture: the analogous second S-derivative (Poisson-weighted BS gammas) of mertonCallPrice; vega mixture: the analogous σ-derivative of mertonCallPrice, requiring the chain rule through mertonVol σ δ T n

/-!
Merton call delta as the Poisson-weighted mixture of chain-ruled Black–Scholes deltas, with the mixture bounded in [0,1].
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

/-- **Merton call delta**: `∂_{S₀} mertonCallPrice` is the Poisson-weighted mixture of
chain-ruled conditional Black–Scholes deltas, `∑' n, w(n) · (Sₙ(S₀)/S₀) · Φ(d₁(Sₙ(S₀)))`
(the factor `Sₙ(S₀)/S₀` being `deriv (fun S ↦ mertonSpot S k Λ n) S₀`), and this mixture
delta is itself a probability, i.e. lies in `[0, 1]`. -/
theorem mertonCallPrice_hasDerivAt {S_0 K r σ T k : ℝ} (δ : ℝ) (Λ : ℝ≥0)
    (hS_0 : 0 < S_0) (hK : 0 < K) (hσ : 0 < σ) (hT : 0 < T) (hk : -1 < k) :
    HasDerivAt (fun S ↦ mertonCallPrice S K r σ T k δ Λ)
        (∑' n : ℕ, Real.exp (-(Λ : ℝ)) * (Λ : ℝ) ^ n / (n.factorial : ℝ)
          * (mertonSpot S_0 k Λ n / S_0)
          * deriv (fun S ↦ bsV K r (mertonVol σ δ T n) S T) (mertonSpot S_0 k Λ n)) S_0
      ∧ (∑' n : ℕ, Real.exp (-(Λ : ℝ)) * (Λ : ℝ) ^ n / (n.factorial : ℝ)
          * (mertonSpot S_0 k Λ n / S_0)
          * deriv (fun S ↦ bsV K r (mertonVol σ δ T n) S T) (mertonSpot S_0 k Λ n))
        ∈ Set.Icc (0 : ℝ) 1 := by
  -- Each conditional BS derivative equals `Φ(d₁)` at the jump-adjusted spot.
  have hderiv_eq : ∀ (n : ℕ) (S : ℝ), 0 < S →
      deriv (fun s ↦ bsV K r (mertonVol σ δ T n) s T) (mertonSpot S k Λ n)
        = Phi (bsd1 (mertonSpot S k Λ n) K r (mertonVol σ δ T n) T) := by
    intro n S hS
    exact (hasDerivAt_bsV_S hK (mertonVol_pos hσ hT n) (mertonSpot_pos hS hk Λ n) hT).deriv
  -- `mertonSpot` is linear in the spot, so its "S-derivative over S" is S-independent.
  have hCn_eq : ∀ (n : ℕ) (S : ℝ), 0 < S → mertonSpot S k Λ n / S = mertonSpot 1 k Λ n := by
    intro n S hS
    unfold mertonSpot
    field_simp
  have hCn_pos : ∀ n : ℕ, 0 < mertonSpot 1 k Λ n := fun n ↦ mertonSpot_pos one_pos hk Λ n
  -- Chain-ruled derivative of one conditional BS term, for `S > 0`.
  have hderiv_term : ∀ (n : ℕ) (S : ℝ), 0 < S →
      HasDerivAt (fun S ↦ mertonCallTerm S K r σ T k δ Λ n)
        ((mertonSpot S k Λ n / S)
          * deriv (fun s ↦ bsV K r (mertonVol σ δ T n) s T) (mertonSpot S k Λ n)) S := by
    intro n S hS
    have hfun_eq : (fun S ↦ mertonCallTerm S K r σ T k δ Λ n)
        = fun S ↦ bsV K r (mertonVol σ δ T n) (mertonSpot S k Λ n) T := by
      funext S; exact mertonCallTerm_eq_bsV S K r σ T k δ Λ n
    rw [hfun_eq]
    have hspot_pos : 0 < mertonSpot S k Λ n := mertonSpot_pos hS hk Λ n
    have hbsV : HasDerivAt (fun s ↦ bsV K r (mertonVol σ δ T n) s T)
        (Phi (bsd1 (mertonSpot S k Λ n) K r (mertonVol σ δ T n) T)) (mertonSpot S k Λ n) :=
      hasDerivAt_bsV_S hK (mertonVol_pos hσ hT n) hspot_pos hT
    have hspotderiv : HasDerivAt (fun S' ↦ mertonSpot S' k Λ n) (mertonSpot S k Λ n / S) S := by
      have heq : (fun S' : ℝ ↦ mertonSpot S' k Λ n) = fun S' ↦ S' * (mertonSpot S k Λ n / S) := by
        funext S'; unfold mertonSpot; field_simp
      rw [heq]
      simpa using (hasDerivAt_id S).mul_const (mertonSpot S k Λ n / S)
    rw [← hderiv_eq n S hS] at hbsV
    rw [mul_comm]
    exact hbsV.comp S hspotderiv
  -- Poisson-mixture weight, and its (S-independent) summable domination.
  have hw_nonneg : ∀ n : ℕ, 0 ≤ Real.exp (-(Λ : ℝ)) * (Λ : ℝ) ^ n / (n.factorial : ℝ) :=
    fun n ↦ by positivity
  have hu_summable : Summable (fun n : ℕ ↦
      Real.exp (-(Λ : ℝ)) * (Λ : ℝ) ^ n / (n.factorial : ℝ) * mertonSpot 1 k Λ n) :=
    summable_weights_mul_mertonSpot 1 k Λ
  have hderiv_bound : ∀ (n : ℕ) (S : ℝ), S ∈ Set.Ioi (0 : ℝ) →
      ‖Real.exp (-(Λ : ℝ)) * (Λ : ℝ) ^ n / (n.factorial : ℝ)
        * ((mertonSpot S k Λ n / S)
          * deriv (fun s ↦ bsV K r (mertonVol σ δ T n) s T) (mertonSpot S k Λ n))‖
        ≤ Real.exp (-(Λ : ℝ)) * (Λ : ℝ) ^ n / (n.factorial : ℝ) * mertonSpot 1 k Λ n := by
    intro n S hS
    rw [Set.mem_Ioi] at hS
    rw [hCn_eq n S hS, hderiv_eq n S hS, Real.norm_eq_abs,
      abs_of_nonneg (mul_nonneg (hw_nonneg n)
        (mul_nonneg (hCn_pos n).le (Phi_nonneg _)))]
    exact mul_le_mul_of_nonneg_left
      (mul_le_of_le_one_right (hCn_pos n).le (Phi_le_one _)) (hw_nonneg n)
  -- Uniform convergence of the derivative partial sums, on the positive reals.
  have huniform := tendstoUniformlyOn_tsum_nat hu_summable hderiv_bound
  -- Pointwise convergence of the primitive partial sums to `mertonCallPrice`.
  have hconv : ∀ S ∈ Set.Ioi (0 : ℝ),
      Filter.Tendsto (fun N ↦ ∑ n ∈ Finset.range N,
          Real.exp (-(Λ : ℝ)) * (Λ : ℝ) ^ n / (n.factorial : ℝ)
            * mertonCallTerm S K r σ T k δ Λ n)
        Filter.atTop (nhds (mertonCallPrice S K r σ T k δ Λ)) := by
    intro S hS
    rw [Set.mem_Ioi] at hS
    have hsummable : Summable (fun n : ℕ ↦
        Real.exp (-(Λ : ℝ)) * (Λ : ℝ) ^ n / (n.factorial : ℝ)
          * mertonCallTerm S K r σ T k δ Λ n) := by
      have hint := (integrable_poissonMeasure_iff (r := Λ)
        (f := mertonCallTerm S K r σ T k δ Λ)).mp
        (integrable_mertonCallTerm δ Λ hS hK hσ hT hk)
      refine hint.congr fun n ↦ ?_
      rw [Real.norm_eq_abs, abs_of_nonneg (mertonCallTerm_nonneg δ Λ hS hK hσ hT hk n)]
    have htendsto := hsummable.hasSum.tendsto_sum_nat
    rwa [← mertonCallPrice_eq_tsum S K r σ T k δ Λ] at htendsto
  -- Each finite partial sum is differentiable, by the finite-sum rule.
  have hderivAt_partial : ∀ (N : ℕ), ∀ S ∈ Set.Ioi (0 : ℝ),
      HasDerivAt (fun S ↦ ∑ n ∈ Finset.range N,
          Real.exp (-(Λ : ℝ)) * (Λ : ℝ) ^ n / (n.factorial : ℝ)
            * mertonCallTerm S K r σ T k δ Λ n)
        (∑ n ∈ Finset.range N, Real.exp (-(Λ : ℝ)) * (Λ : ℝ) ^ n / (n.factorial : ℝ)
          * ((mertonSpot S k Λ n / S)
            * deriv (fun s ↦ bsV K r (mertonVol σ δ T n) s T) (mertonSpot S k Λ n))) S := by
    intro N S hS
    rw [Set.mem_Ioi] at hS
    exact HasDerivAt.fun_sum fun n _ ↦
      (hderiv_term n S hS).const_mul (Real.exp (-(Λ : ℝ)) * (Λ : ℝ) ^ n / (n.factorial : ℝ))
  -- Assemble via the locally-uniform-limit derivative theorem, on the open set `S > 0`.
  have hmain := hasDerivAt_of_tendstoUniformlyOn isOpen_Ioi huniform
    (Filter.Eventually.of_forall hderivAt_partial) hconv (Set.mem_Ioi.mpr hS_0)
  have heq_deriv : (∑' n : ℕ, Real.exp (-(Λ : ℝ)) * (Λ : ℝ) ^ n / (n.factorial : ℝ)
        * ((mertonSpot S_0 k Λ n / S_0)
          * deriv (fun S ↦ bsV K r (mertonVol σ δ T n) S T) (mertonSpot S_0 k Λ n)))
      = ∑' n : ℕ, Real.exp (-(Λ : ℝ)) * (Λ : ℝ) ^ n / (n.factorial : ℝ)
          * (mertonSpot S_0 k Λ n / S_0)
          * deriv (fun S ↦ bsV K r (mertonVol σ δ T n) S T) (mertonSpot S_0 k Λ n) :=
    tsum_congr fun n ↦ by ring
  rw [heq_deriv] at hmain
  refine ⟨hmain, ?_, ?_⟩
  · -- Nonnegativity: every term is a product of nonnegatives.
    refine tsum_nonneg fun n ↦ ?_
    rw [hderiv_eq n S_0 hS_0]
    have hCn_nonneg : 0 ≤ mertonSpot S_0 k Λ n / S_0 :=
      div_nonneg (mertonSpot_pos hS_0 hk Λ n).le hS_0.le
    exact mul_nonneg (mul_nonneg (hw_nonneg n) hCn_nonneg) (Phi_nonneg _)
  · -- Boundedness by `1`: each term is dominated by the spot-recombination series,
    -- which sums to `1` (Merton's compensation identity at `S₀ = 1`).
    have h_sum_eq_one : (∑' n : ℕ, Real.exp (-(Λ : ℝ)) * (Λ : ℝ) ^ n / (n.factorial : ℝ)
        * mertonSpot 1 k Λ n) = 1 := by
      have h := integral_mertonSpot 1 k Λ
      rw [integral_poissonMeasure] at h
      simpa using h
    rw [← h_sum_eq_one]
    refine Summable.tsum_le_tsum (fun n ↦ ?_) ?_ hu_summable
    · rw [hderiv_eq n S_0 hS_0, hCn_eq n S_0 hS_0]
      exact mul_le_of_le_one_right
        (mul_nonneg (hw_nonneg n) (hCn_pos n).le) (Phi_le_one _)
    · refine Summable.of_nonneg_of_le (fun n ↦ ?_) (fun n ↦ ?_) hu_summable
      · have hCn_nonneg : 0 ≤ mertonSpot S_0 k Λ n / S_0 :=
          div_nonneg (mertonSpot_pos hS_0 hk Λ n).le hS_0.le
        rw [hderiv_eq n S_0 hS_0]
        exact mul_nonneg (mul_nonneg (hw_nonneg n) hCn_nonneg) (Phi_nonneg _)
      · rw [hderiv_eq n S_0 hS_0, hCn_eq n S_0 hS_0]
        exact mul_le_of_le_one_right
          (mul_nonneg (hw_nonneg n) (hCn_pos n).le) (Phi_le_one _)

end MathFin
