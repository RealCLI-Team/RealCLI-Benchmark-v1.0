# Add integer rings and prime ideal decomposition to SymPy's algebraic number fields

We use SymPy for symbolic computation in algebraic number theory; the repo is at `/app` (SymPy source, pure Python). Currently the algebraic number fields built by `QQ.algebraic_field(...)` only support the four arithmetic operations and can't answer even the most basic questions: What is the field discriminant? What is an integral basis of the ring of integers (maximal order)? How does a rational prime decompose in the field? Yet these are prerequisites for all our subsequent work. Please implement this capability on top of the existing polys / numberfields infrastructure.

## External contract (must be strictly followed)

### 1. New methods on algebraic field objects

The field object returned by `QQ.algebraic_field(...)` must gain four new methods:

- `discriminant()`: returns the field discriminant (a rational integer).
- `integral_basis()`: returns an integral basis of the field, i.e. a basis of the ring of integers O_K as a Z-module; the result is a list of n field elements (n being the field degree), each convertible back to a symbolic expression via the existing `K.to_sympy(...)`.
- `maximal_order()`: returns the maximal order O_K itself (a module-structure object), supporting integer scalar multiplication to denote principal ideals (e.g. `7 * ZK` denotes the ideal 7·O_K) and equality comparison.
- `primes_above(p)`: returns all prime ideals above the rational prime p in K. Each prime ideal object must expose the ramification index `e` and inertia degree `f` attributes; prime ideals support integer powers, products, and equality comparison, so that the fundamental identity of decomposition can be verified programmatically: `∏ P_i ** e_i == p * ZK`. Additionally, prime ideal objects must provide a convenience method `P.valuation(I)` that returns the valuation (non-negative integer) of the integral ideal I at the prime ideal P, with semantics exactly matching the public function `prime_valuation(I, P)` below (i.e. `P.valuation(I) == prime_valuation(I, P)`). For example, in Q(ζ₅), at the unique prime ideal P above 5: `P.valuation(5·O_K) == 4`, `P.valuation(3·O_K) == 0`.

Some classical results for you to check your implementation against (we've verified all of them by hand):

- The cyclotomic field Q(ζ₅) (minimal polynomial `cyclotomic_poly(5)`) has discriminant 125; Q(ζ₇) has discriminant -16807.
- The quadratic field Q(√5) has discriminant 5 with integral basis {1, (1+√5)/2}; Q(√-3) has discriminant -3; Q(√7) has discriminant 28.
- Dedekind's cubic field (minimal polynomial x³+x²-2x+8) has discriminant -503, and 2 splits completely into 3 distinct prime ideals (each with e=f=1). This is the famous example that "2 is an essential discriminant divisor": the power basis {1, θ, θ²} is not an integral basis, and (θ²+θ)/2 is an algebraic integer.
- The cubic field x³+3x²+5 has discriminant -135.
- In Q(ζ₇), 7 is totally ramified: only 1 prime ideal, with e=6, f=1, and its 6th power equals 7·O_K; an odd prime p does not ramify (e=1), and its inertia degree equals the multiplicative order of p modulo 7 (e.g. p=11 gives f=3 with 2 prime ideals, p=13 gives f=2 with 3, p=29 splits completely into 6).
- In Q(√5), 2 is inert (a single prime ideal, e=1, f=2); in Q(√7), 2 ramifies (e=2, f=1, and its square equals 2·O_K).
- Any decomposition must satisfy the fundamental identity Σ eᵢ·fᵢ = n.

### 2. Public functions and namespaces

Besides the methods on field objects, provide directly callable public functions, importable from `sympy.polys.numberfields`, `sympy.polys`, and the top-level `sympy` namespace:

- `round_two(T)`: for a monic, irreducible, univariate, integer-coefficient polynomial T, compute the maximal order and discriminant of the number field K=Q[x]/(T), returning the pair `(ZK, dK)`. Inputs not meeting the above conditions (non-monic, reducible, non-integer coefficients) must raise `ValueError`.
- `prime_decomp(p, T=...)`: perform prime ideal decomposition directly on the defining polynomial T, returning a list of prime ideals; at least one of the defining polynomial or the maximal order must be given — giving neither must raise `ValueError`.
- `prime_valuation(I, P)`: compute the valuation (non-negative integer) of the integral ideal I at the prime ideal P. For example, in Q(ζ₅), at the unique prime ideal P above 5: v_P(25·O_K) = 8, v_P(5·O_K) = 4, v_P(3·O_K) = 0.

## Requirements

- Internal algorithms and code organization are up to you (the classical approach in the literature is Zassenhaus's Round 2 algorithm; see Cohen's *A Course in Computational Algebraic Number Theory*, which also contains the standard algorithm for prime ideal decomposition), but the external behavior above must be met exactly.
- Only modify source under `/app`; do not break existing algebraic field and polynomial functionality (arithmetic on field elements, negative powers, `to_sympy` / `from_sympy` conversions, etc. must all keep working as before).
- When done, actually run a few of the examples listed above (`python3 -c "..."` is fine) and confirm the behavior is correct before wrapping up.
