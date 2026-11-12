/-- **A symmetric difference of the standard normal distribution function.**
`Phi x - Phi (-x) = 2 * Phi x - 1`, from the reflection `Phi (-x) = 1 - Phi x`. -/
theorem Phi_sub_Phi_neg_eq_two_mul_sub_one (x : ℝ) : Phi x - Phi (-x) = 2 * Phi x - 1 := by
  rw [Phi_neg]
  ring
