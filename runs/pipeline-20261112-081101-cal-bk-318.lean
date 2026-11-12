/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.Models.LocalVol.ForwardSmile
public import MathFin.Models.ModelFree.CalendarArbitrage
public import MathFin.Models.ModelFree.CallPriceFunction
public import MathFin.Models.ModelFree.SVI.BlackSmile
public import MathFin.Models.ModelFree.SVI.TailCondition

set_option autoImplicit false

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

-- pointers: MathFin/Models/ModelFree/SVI/TailCondition.lean, MathFin/Models/ModelFree/SVI/BlackSmile.lean, MathFin/Models/ModelFree/CallPriceFunction.lean, MathFin/Models/ModelFree/CalendarArbitrage.lean, MathFin/Models/LocalVol/ForwardSmile.lean
-- main-module: MathFin/Models/ModelFree/BlackFormTail.lean
-- benchmark: benchmarks/black_scholes.json
-- benchmark-id: mf-smile-black-form-tail-iff
-- source-issue: 318
-- deferred: SVI tail lemmas re-derived as corollaries of the general lemma; left-wing bound: IsNormalizedCallPrice (bsSmile p) implies b(1 - rho) ≤ 2, and the mass at zero; calendar-spread martingale step for any convex payoff via conditional Jensen; t > 0 calendar statements tied to the local-variance characterisation; unification of blackForm, totalVarianceCall and logBlackCall into one (k, w) Black form; IsNormalizedCallPrice from a nonnegative random variable with mean 1 (easy direction); port hygiene: scoped maxHeartbeats, maxRecDepth and open scoped Classical
-- blueprint: thm:smile-no-arbitrage






/-- For any everywhere-positive total variance `w`, the Black form `blackForm k (w k)` tends to
`0` as `k → +∞` iff `dpl k (w k)` tends to `-∞`. -/

theorem blackForm_lower_bound_of_dpl_ge (w : ℝ → ℝ) (hw : ∀ k, 0 < w k) (M : ℝ) :
    ∃ c : ℝ, 0 < c ∧ ∃ R : ℝ, ∀ k : ℝ, R ≤ k → M ≤ MathFin.ForwardSmile.dpl k (w k) →
      c ≤ MathFin.ForwardSmile.blackForm k (w k) := by
  have hδ : 0 < MathFin.Phi M := (MathFin.Phi_mem_Ioo _).1
  refine ⟨MathFin.Phi M / 2, half_pos hδ, 2 / MathFin.Phi M ^ 2, fun k hk hM ↦ ?_⟩
  have hk0 : 0 ≤ k := hk.trans' (by positivity)
  have hs : 0 < √(w k) := Real.sqrt_pos.2 (hw k)
  have hsq : √(w k) ^ 2 = w k := Real.sq_sqrt (hw k).le
  have hE : 0 ≤ Real.exp k * MathFin.Phi (MathFin.ForwardSmile.dmi k (w k)) :=
    mul_nonneg (Real.exp_pos k).le (MathFin.Phi_nonneg _)
  have hmills : -MathFin.ForwardSmile.dmi k (w k) *
      (Real.exp k * MathFin.Phi (MathFin.ForwardSmile.dmi k (w k))) ≤ 1 := by
    have := mul_le_mul_of_nonneg_left
      (neg_mul_Phi_le_gaussianPDFReal (MathFin.ForwardSmile.dmi k (w k))) (Real.exp_pos k).le
    rw [MathFin.ForwardSmile.exp_mul_pdf_dmi (hw k)] at this
    linarith [gaussianPDFReal_zero_one_le_one (MathFin.ForwardSmile.dpl k (w k))]
  have hx' : -MathFin.ForwardSmile.dmi k (w k) = k / √(w k) + √(w k) / 2 := by
    simp only [MathFin.ForwardSmile.dmi]
    field_simp
    nlinarith [hsq]
  have hx0 : 0 ≤ -MathFin.ForwardSmile.dmi k (w k) := by rw [hx']; positivity
  have hx2 : 2 * k ≤ (-MathFin.ForwardSmile.dmi k (w k)) ^ 2 := by
    rw [hx']; field_simp; nlinarith [sq_nonneg (2 * k - √(w k) ^ 2)]
  have hd : 2 / MathFin.Phi M ≤ -MathFin.ForwardSmile.dmi k (w k) := by
    refine le_of_sq_le_sq ?_ hx0
    have : 2 / MathFin.Phi M ^ 2 * 2 = (2 / MathFin.Phi M) ^ 2 := by
      rw [div_pow]; ring
    nlinarith
  have h3 := (mul_le_mul_of_nonneg_right hd hE).trans hmills
  rw [div_mul_eq_mul_div, div_le_iff₀ hδ] at h3
  have := MathFin.strictMono_Phi.monotone hM
  unfold MathFin.ForwardSmile.blackForm
  linarith

theorem blackForm_nonneg_le_Phi_dpl (k w : ℝ) (hw : 0 < w) :
    0 ≤ MathFin.ForwardSmile.blackForm k w ∧
      MathFin.ForwardSmile.blackForm k w ≤ MathFin.Phi (MathFin.ForwardSmile.dpl k w) := by
  let p : MathFin.SVI.Params := ⟨w, 0, 0, 0, 0⟩
  have hv (x : ℝ) : MathFin.SVI.variance p x = w := by simp [p, MathFin.SVI.variance]
  have hK : 0 < Real.exp k := Real.exp_pos k
  have hw' : 0 < MathFin.SVI.variance p (Real.log (Real.exp k)) := by rw [hv]; exact hw
  have hbc : MathFin.SVI.blackCall p (Real.exp k) = MathFin.ForwardSmile.blackForm k w := by
    simp [MathFin.SVI.blackCall, hv]
  rw [← hbc]
  refine ⟨?_, ?_⟩
  · rw [MathFin.SVI.blackCall_eq_bsSmile p _ hK hw']
    exact MathFin.SVI.bsSmile_nonneg p hK hw'
  · simpa [hv] using MathFin.SVI.blackCall_le_Phi_dpl p (Real.exp k)

theorem Phi_tendsto_zero_atBot : Filter.Tendsto MathFin.Phi Filter.atBot (nhds 0) :=
  tendsto_Phi_atBot

theorem blackForm_tendsto_zero_iff_dpl_tendsto_atBot (w : ℝ → ℝ) (hw : ∀ k, 0 < w k) : Filter.Tendsto (fun k ↦ MathFin.ForwardSmile.blackForm k (w k)) Filter.atTop (nhds 0) ↔ Filter.Tendsto (fun k ↦ MathFin.ForwardSmile.dpl k (w k)) Filter.atTop Filter.atBot := by
  constructor
  · intro h
    by_contra hn
    rw [Filter.tendsto_atBot] at hn
    obtain ⟨M, hM⟩ := not_forall.mp hn
    have hf := Filter.not_eventually.mp hM
    obtain ⟨c, hc, R, hR⟩ := blackForm_lower_bound_of_dpl_ge w hw M
    obtain ⟨k, ⟨hk1, hk2⟩, hk3⟩ := ((hf.and_eventually (h.eventually (Iio_mem_nhds hc))).and_eventually (Filter.eventually_ge_atTop R)).exists
    have h4 := hR k hk3 (le_of_lt (not_le.mp hk1))
    exact (not_lt.mpr h4) hk2
  · intro h
    exact tendsto_of_tendsto_of_tendsto_of_le_of_le tendsto_const_nhds (Phi_tendsto_zero_atBot.comp h) (fun k => (blackForm_nonneg_le_Phi_dpl k (w k) (hw k)).1) (fun k => (blackForm_nonneg_le_Phi_dpl k (w k) (hw k)).2)

end MathFin
