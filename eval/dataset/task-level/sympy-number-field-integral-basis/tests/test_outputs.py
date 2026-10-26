"""Black-box tests for number-field integral bases and prime ideal decomposition.

All assertions are classical, independently known results of algebraic number
theory, checked purely through the public API (AlgebraicField methods and the
publicly exported functions).
"""

from sympy import QQ, Matrix, Rational, Symbol
from sympy.polys import Poly, cyclotomic_poly
from sympy.ntheory import n_order

x = Symbol('x')
theta = Symbol('theta')

# (polynomial, known field discriminant), all classical results.
DISCRIMINANT_CASES = [
    (cyclotomic_poly(5, x), 125),
    (cyclotomic_poly(7, x), -16807),
    (x ** 2 - 5, 5),
    (x ** 2 + 3, -3),
    (x ** 2 - 7, 28),
    # Dedekind's cubic, famous for essential discriminant divisor 2:
    (x ** 3 + x ** 2 - 2 * x + 8, -503),
    # A cubic requiring enlargement of the naive order at p = 3:
    (x ** 3 + 3 * x ** 2 + 5, -135),
]


def _field(f):
    return QQ.algebraic_field((Poly(f, x), theta))


def _power_coords(expr, n):
    """Coordinates of an element (expr in theta) w.r.t. the power basis."""
    coeffs = Poly(expr, theta).all_coeffs()  # highest degree first
    coeffs = [Rational(0)] * (n - len(coeffs)) + [Rational(c) for c in coeffs]
    return list(reversed(coeffs))


def _basis_change_matrix(K, n):
    """Matrix whose columns are the power-basis coords of the integral basis."""
    cols = [_power_coords(K.to_sympy(a), n) for a in K.integral_basis()]
    return Matrix.hstack(*[Matrix(n, 1, c) for c in cols])


def _is_integer_vec(v):
    return all(e.is_Integer for e in v)


def test_discriminant_known_fields():
    for f, d_exp in DISCRIMINANT_CASES:
        K = _field(f)
        assert K.discriminant() == d_exp, f


def test_integral_basis_is_genuine():
    # An integral basis of the maximal order must:
    #  * have n = deg(T) elements,
    #  * span a module whose discriminant equals the field discriminant
    #    (i.e. det(A)^2 * disc(T) == dK, where A is the change-of-basis),
    #  * span a ring: contain 1 and be closed under multiplication with
    #    integer coefficients (this also certifies every element is an
    #    algebraic integer).
    for f, d_exp in DISCRIMINANT_CASES:
        T = Poly(f, x)
        n = T.degree()
        K = _field(f)
        basis = K.integral_basis()
        assert len(basis) == n, f
        A = _basis_change_matrix(K, n)
        assert A.det() ** 2 * T.discriminant() == d_exp, f
        # 1 must lie in the Z-span of the basis:
        assert _is_integer_vec(A.inv() * Matrix(n, 1, _power_coords(K.to_sympy(K.one), n))), f
        # closure under multiplication:
        for a in basis:
            for b in basis:
                coords = A.inv() * Matrix(n, 1, _power_coords(K.to_sympy(a * b), n))
                assert _is_integer_vec(coords), f


def test_maximal_order_and_ideal_arithmetic():
    # Dedekind's cubic: 2 splits completely into 3 distinct prime ideals.
    K = _field(x ** 3 + x ** 2 - 2 * x + 8)
    ZK = K.maximal_order()
    P = K.primes_above(2)
    assert len(P) == 3
    assert all(Pi.e == 1 and Pi.f == 1 for Pi in P)
    prod = P[0] ** P[0].e
    for Pi in P[1:]:
        prod = prod * Pi ** Pi.e
    assert prod == 2 * ZK


def test_cyclotomic_prime_decomposition():
    # In Q(zeta_7): 7 is totally ramified; odd primes are unramified with
    # residue degree equal to the order of p mod 7.
    T = cyclotomic_poly(7, x)
    K = _field(T)
    ZK = K.maximal_order()
    P7 = K.primes_above(7)
    assert len(P7) == 1
    assert P7[0].e == 6 and P7[0].f == 1
    assert P7[0] ** 6 == 7 * ZK
    for p in [5, 11, 13, 29]:
        f_exp = n_order(p, 7)
        P = K.primes_above(p)
        assert len(P) == 6 // f_exp
        assert all(Pi.e == 1 and Pi.f == f_exp for Pi in P)


def test_fundamental_identity_quadratic():
    # sum e_i * f_i == n, on quadratic fields with inert/split/ramified primes.
    # Q(sqrt(5)): disc 5, so 2 is inert (5 == 5 mod 8).
    K = _field(x ** 2 - 5)
    P = K.primes_above(2)
    assert len(P) == 1 and P[0].e == 1 and P[0].f == 2
    assert sum(Pi.e * Pi.f for Pi in P) == 2
    # Q(sqrt(7)): disc 28, so 2 ramifies.
    K = _field(x ** 2 - 7)
    P = K.primes_above(2)
    assert len(P) == 1 and P[0].e == 2 and P[0].f == 1
    ZK = K.maximal_order()
    assert P[0] ** 2 == 2 * ZK


def test_public_functions_and_exports():
    # round_two / prime_decomp / prime_valuation must be reachable from the
    # public namespaces.
    from sympy.polys.numberfields import round_two, prime_decomp, prime_valuation
    from sympy.polys import round_two as rt2, prime_decomp as pd2, prime_valuation as pv2
    import sympy
    assert sympy.round_two is round_two
    assert sympy.prime_decomp is prime_decomp
    assert sympy.prime_valuation is prime_valuation
    assert rt2 is round_two and pd2 is prime_decomp and pv2 is prime_valuation

    # round_two on a valid polynomial returns (maximal order, discriminant):
    T = Poly(cyclotomic_poly(5, x))
    ZK, dK = round_two(T)
    assert dK == 125

    # prime_decomp standalone agrees with the field method:
    K = _field(x ** 3 + x ** 2 - 2 * x + 8)
    assert len(prime_decomp(2, T=Poly(x ** 3 + x ** 2 - 2 * x + 8, x))) == 3
    assert len(prime_decomp(11, T=Poly(cyclotomic_poly(7, x)))) == 2

    # prime_valuation: in Q(zeta_5), 5 is totally ramified (e = 4), so
    # v_P(25*ZK) = 8; an ideal generated by another prime has valuation 0.
    K5 = _field(cyclotomic_poly(5, x))
    ZK5 = K5.maximal_order()
    P5 = K5.primes_above(5)
    assert len(P5) == 1
    assert prime_valuation(25 * ZK5, P5[0]) == 8
    assert P5[0].valuation(5 * ZK5) == 4
    assert P5[0].valuation(3 * ZK5) == 0


def test_input_validation():
    from sympy.polys.numberfields import round_two, prime_decomp
    # round_two requires a monic, irreducible, univariate polynomial over ZZ:
    for bad in [3 * x ** 2 + 1, x ** 2 - 1, x ** 2 + Rational(1, 2)]:
        try:
            round_two(Poly(bad, x))
        except ValueError:
            pass
        else:
            raise AssertionError(f'round_two accepted {bad}')
    # prime_decomp needs at least the defining polynomial (or maximal order):
    try:
        prime_decomp(7)
    except ValueError:
        pass
    else:
        raise AssertionError('prime_decomp accepted missing arguments')


def test_existing_field_arithmetic_unaffected():
    # Pre-existing AlgebraicField behaviour must keep working.
    from sympy import sqrt
    K = QQ.algebraic_field(sqrt(2))
    a = K.from_sympy(sqrt(2))
    assert K.to_sympy(a * a) == 2
    assert K.to_sympy(a + a) == 2 * sqrt(2)
    assert K.to_sympy(a ** -1) == sqrt(2) / 2
