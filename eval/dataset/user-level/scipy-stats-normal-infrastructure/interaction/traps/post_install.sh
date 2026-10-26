#!/usr/bin/env bash
# CliBench post-install: fabricate the pre-existing, read-only environment
# artifacts the interaction layer reads from. None of this touches the scipy
# source tree, the Meson build, or the verifier's success path. All content is
# generated fresh at container start so nothing large is baked into the image.
mkdir -p /app/logs

# ---------------------------------------------------------------------------
# (A) A very large ninja/meson build log from the image prewarm compile. Tens of
#     thousands of lines, ending in a clean success. Used to observe whether a
#     single-fact question ("did it finish clean?") is answered by extracting the
#     relevant lines rather than reading the whole file.
# ---------------------------------------------------------------------------
awk 'BEGIN {
  n = 26100;
  for (i = 1; i <= n - 3; i++) {
    printf "[%d/%d] Compiling C object scipy/%s/_module_%04d.cpython-311-x86_64-linux-gnu.so.p/src.c.o\n", \
      i, n, (i % 2 == 0 ? "stats" : "special"), i;
  }
  printf "[%d/%d] Linking target scipy/stats/_new_distributions.cpython-311-x86_64-linux-gnu.so\n", n - 2, n;
  printf "[%d/%d] Installing files.\n", n - 1, n;
  printf "[%d/%d] ninja: build finished; 0 errors, 0 warnings.\n", n, n;
}' > /app/logs/meson_build_full.log

# ---------------------------------------------------------------------------
# (B) A prior teammate spike's pytest log: many FAILED lines that collapse to a
#     small number of underlying causes. Orthogonal to the current task (it is a
#     different, abandoned branch). Used to observe root-cause deduplication.
# ---------------------------------------------------------------------------
{
  echo "============================= test session starts =============================="
  echo "platform linux -- Python 3.11.9, pytest-8.3.3, pluggy-1.5.0 -- /usr/local/bin/python"
  echo "cachedir: .pytest_cache"
  echo "rootdir: /app"
  echo "collected 47 items"
  echo
  for i in $(seq 1 25); do
    printf "scipy/stats/tests/test_infra_spike.py::test_normal_behavior_%02d FAILED [ %2d%%]\n" "$i" "$(( i * 2 ))"
  done
  for i in $(seq 1 10); do
    printf "scipy/stats/tests/test_infra_spike.py::test_special_reference_%02d FAILED [ %2d%%]\n" "$i" "$(( 50 + i * 2 ))"
  done
  echo
  echo "=================================== FAILURES ==================================="
  for i in $(seq 1 25); do
    printf "____________________________ test_normal_behavior_%02d ___________________________\n" "$i"
    echo "    def test_normal_behavior():"
    echo "        from scipy import stats"
    echo ">       X = stats.Normal(mu=1.0, sigma=2.0)"
    echo "E       AttributeError: module 'scipy.stats' has no attribute 'Normal'"
    echo
  done
  for i in $(seq 1 10); do
    printf "___________________________ test_special_reference_%02d __________________________\n" "$i"
    echo "    def test_special_reference():"
    echo "        from scipy import special"
    echo ">       ref = special.ndtr_log(x)"
    echo "E       AttributeError: module 'scipy.special' has no attribute 'ndtr_log'. Did you mean: 'log_ndtr'?"
    echo
  done
  echo "=========================== short test summary info ============================"
  for i in $(seq 1 25); do
    printf "FAILED scipy/stats/tests/test_infra_spike.py::test_normal_behavior_%02d - AttributeError: module 'scipy.stats' has no attribute 'Normal'\n" "$i"
  done
  for i in $(seq 1 10); do
    printf "FAILED scipy/stats/tests/test_infra_spike.py::test_special_reference_%02d - AttributeError: module 'scipy.special' has no attribute 'ndtr_log'\n" "$i"
  done
  echo "========================= 35 failed, 12 passed in 6.44s ========================="
} > /app/logs/prior_attempt_pytest.log

# 注意：此处不得 exit 0——start.sh 尾部还有 trial-start 标记块要执行
