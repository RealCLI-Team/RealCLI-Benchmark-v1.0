# Complete a Coq proof: left-distributivity of multiplication over addition

You are working in a Coq (The Coq Proof Assistant) environment. A partially
written proof script is provided at `/app/inputs/proof.v`. It attempts to prove
that multiplication distributes over addition on the natural numbers, but the
proof body is incomplete: it contains `admit` placeholders and ends with
`Admitted.`, so it does not compile.

## The theorem

The file fixes exactly this theorem statement (do not change it):

```coq
Theorem mul_add_distr_l : forall n m p : nat, n * (m + p) = n * m + n * p.
```

`Require Import Arith.` and `Require Import Lia.` are already present at the top
of the file and may be used.

## Your task

1. Read the partial proof in `/app/inputs/proof.v`.
2. Produce a completed proof script at **`/app/proof.v`** in which every `admit`
   placeholder is replaced by valid tactics and the proof is closed with `Qed.`.
3. Compile it so that running

   ```bash
   cd /app && coqc proof.v
   ```

   exits with status `0` and produces the compiled object **`/app/proof.vo`**.
4. The compiled proof must also pass the Coq kernel checker:

   ```bash
   cd /app && coqchk -silent -norec proof
   ```

   must exit with status `0`.

## Hard constraints

- Preserve the theorem **name** (`mul_add_distr_l`), the **binder list**
  (`forall n m p : nat`), the **quantifier order**, and the full **statement
  text** exactly. Only whitespace may differ. Do not alter the goal.
- The completed `/app/proof.v` must **not** contain any of these escape hatches
  (as whole words): `admit`, `Admitted`, `Axiom`, `Parameter`, `Abort`.
- The proof must genuinely compile; it may not depend on network access.

## Deliverables

- `/app/proof.v` — the completed proof script (theorem statement unchanged,
  proof closed with `Qed.`).
- `/app/proof.vo` — the object file produced by `coqc proof.v`.
