#!/usr/bin/env bash
set -euo pipefail

# Reference solution for the Coq proof-completion task.
# Writes a completed proof to /app/proof.v and compiles it.

cd /app

cat > /app/proof.v <<'COQ'
(* proof.v
   Left-distributivity of multiplication over addition on nat. *)
Require Import Arith.
Require Import Lia.

Theorem mul_add_distr_l : forall n m p : nat, n * (m + p) = n * m + n * p.
Proof.
  intros n m p.
  induction n as [| n' IH].
  - simpl. reflexivity.
  - simpl. rewrite IH. lia.
Qed.
COQ

coqc proof.v
coqchk -silent -norec proof

echo "Proof completed and verified: /app/proof.v -> /app/proof.vo"
