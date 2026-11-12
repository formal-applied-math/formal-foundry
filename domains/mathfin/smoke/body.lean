/-- **Central probability of the standard normal.** The mass of `[-x, x]` under the
standard normal, written as a difference of the distribution function, is `2 Φ(x) − 1`. -/
theorem Phi_sub_Phi_neg_eq_two_mul_sub_one (x : ℝ) : Phi x - Phi (-x) = 2 * Phi x - 1 := by
  rw [Phi_neg]
  ring
