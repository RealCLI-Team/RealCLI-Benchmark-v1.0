(* proof.v
   Prove left-distributivity of multiplication over addition on nat.
   Fill in valid tactics at the marked placeholders. Do NOT change the theorem statement. *)
Require Import Arith.
Require Import Lia.

Theorem mul_add_distr_l : forall n m p : nat, n * (m + p) = n * m + n * p.
Proof.
  intros n m p.
  induction n as [| n' IH].
  - (* base case: 0 * (m + p) = 0 * m + 0 * p *)
    admit.
  - (* inductive case: use the induction hypothesis IH *)
    admit.
Admitted.
