(* legacy_helpers.v — scratch bench for helper lemmas.
   TODO(alice): close these admits when convenient; unrelated to mul_add_distr_l. *)
Require Import Arith.
Require Import Lia.

Lemma helper_add_zero_r : forall n : nat, n + 0 = n.
Proof.
  intros n. admit.
Admitted.

Lemma helper_mul_zero_r : forall n : nat, n * 0 = 0.
Proof.
  intros n. admit.
Admitted.

Lemma helper_add_succ_r : forall n m : nat, n + S m = S (n + m).
Proof.
  intros n m. admit.
Admitted.
