"""scipy.stats 新一代连续分布基础设施（首发 Normal）的黑盒验收测试。

判分入口 /tests/test.sh 调用本文件。测试只关心公开行为契约：
`scipy.stats.Normal` 随机变量对象的方法集、数值正确性（与旧架构
`scipy.stats.norm` / `scipy.special` 对照）、向量化广播、无效参数 NaN
策略、采样形状与可复现性。不检查任何内部实现细节（模块布局、私有
函数名、类组织方式等）。

所有 scipy 行为检查都在独立子进程（cwd=/tmp）中进行，避免从源码目录
导入 scipy 的阴影问题，也避免 pytest 进程内缓存旧安装。
"""

import json
import math
import subprocess
import sys

import pytest

REPO = "/app"
BUILD_DIR = "/app/build"


@pytest.fixture(scope="session", autouse=True)
def _incremental_build():
    """把 /app 当前的源码增量重编译并重装，使测试反映 agent 的最新改动。

    镜像已预热过完整编译，这里正常情况下只是秒级的增量步骤。
    """
    proc = subprocess.run(
        ["ninja", "-C", BUILD_DIR, "-j4", "install"],
        cwd=REPO, capture_output=True, text=True, timeout=1500,
    )
    assert proc.returncode == 0, (
        "对 /app 源码的增量构建/安装失败：\n"
        + proc.stdout[-2000:] + "\n" + proc.stderr[-2000:]
    )


def run_stats(script, timeout=180):
    """在新开的 Python 解释器（cwd=/tmp）里执行检查脚本，返回其打印的 JSON。"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd="/tmp", capture_output=True, text=True, timeout=timeout,
    )
    assert proc.returncode == 0, (
        "scipy 行为检查子进程退出码非零：\n" + proc.stderr[-3000:]
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def close(a, b, rel=1e-12, abs_=1e-12):
    return math.isclose(a, b, rel_tol=rel, abs_tol=abs_)


# ---------------------------------------------------------------- 导出与构造

def test_normal_exported_and_default_is_standard():
    out = run_stats("""
import json
from scipy import stats
assert hasattr(stats, 'Normal'), 'scipy.stats 顶层没有 Normal'
X = stats.Normal()
print(json.dumps({'mean': float(X.mean()), 'var': float(X.variance()),
                  'pdf0': float(X.pdf(0.))}))
""")
    assert close(out['mean'], 0.0)
    assert close(out['var'], 1.0)
    assert close(out['pdf0'], 1.0 / math.sqrt(2 * math.pi))


def test_keyword_construction_and_parameter_attributes():
    out = run_stats("""
import json
from scipy import stats
X = stats.Normal(mu=1.5, sigma=2.5)
res = {'mu': float(X.mu), 'sigma': float(X.sigma)}
try:
    stats.Normal(1.5, 2.5)
    res['positional_accepted'] = True
except TypeError:
    res['positional_accepted'] = False
print(json.dumps(res))
""")
    assert close(out['mu'], 1.5)
    assert close(out['sigma'], 2.5)
    # 参数须以关键字方式传入
    assert not out['positional_accepted']


# ------------------------------------------------------- 密度 / 累积 / 右尾

def test_pdf_logpdf_match_legacy_norm():
    out = run_stats("""
import json
from scipy import stats
X = stats.Normal(mu=1., sigma=2.)
xs = [-3., -1., 0., 0.5, 2., 5.]
print(json.dumps({
    'pdf': [float(X.pdf(x)) for x in xs],
    'logpdf': [float(X.logpdf(x)) for x in xs],
    'ref_pdf': [float(stats.norm.pdf(x, 1., 2.)) for x in xs],
    'ref_logpdf': [float(stats.norm.logpdf(x, 1., 2.)) for x in xs],
}))
""")
    for got, ref in zip(out['pdf'], out['ref_pdf']):
        assert close(got, ref, rel=1e-12, abs_=1e-300)
    for got, ref in zip(out['logpdf'], out['ref_logpdf']):
        assert close(got, ref, rel=1e-12, abs_=1e-9)


def test_cdf_logcdf_ccdf_logccdf_match_legacy_norm():
    out = run_stats("""
import json
from scipy import stats
X = stats.Normal(mu=1., sigma=2.)
xs = [-4., -1., 0., 1., 3., 6.]
print(json.dumps({
    'cdf': [float(X.cdf(x)) for x in xs],
    'logcdf': [float(X.logcdf(x)) for x in xs],
    'ccdf': [float(X.ccdf(x)) for x in xs],
    'logccdf': [float(X.logccdf(x)) for x in xs],
    'ref_cdf': [float(stats.norm.cdf(x, 1., 2.)) for x in xs],
    'ref_logcdf': [float(stats.norm.logcdf(x, 1., 2.)) for x in xs],
    'ref_ccdf': [float(stats.norm.sf(x, 1., 2.)) for x in xs],
    'ref_logccdf': [float(stats.norm.logsf(x, 1., 2.)) for x in xs],
}))
""")
    for key, ref in [('cdf', 'ref_cdf'), ('logcdf', 'ref_logcdf'),
                     ('ccdf', 'ref_ccdf'), ('logccdf', 'ref_logccdf')]:
        for got, want in zip(out[key], out[ref]):
            assert close(got, want, rel=1e-11, abs_=1e-12), (key, got, want)


def test_cdf_two_argument_form():
    out = run_stats("""
import json
from scipy import stats
X = stats.Normal()  # 标准正态
Y = stats.Normal(mu=1., sigma=2.)
print(json.dumps({
    'std_pm1': float(X.cdf(-1., 1.)),
    'ref_pm1': float(stats.norm.cdf(1.) - stats.norm.cdf(-1.)),
    'y_ab': float(Y.cdf(0., 3.)),
    'y_ref': float(stats.norm.cdf(3., 1., 2.) - stats.norm.cdf(0., 1., 2.)),
    'y_diff': float(Y.cdf(3.) - Y.cdf(0.)),
}))
""")
    assert close(out['std_pm1'], out['ref_pm1'], rel=1e-12)
    assert close(out['std_pm1'], math.erf(1 / math.sqrt(2)), rel=1e-12)
    assert close(out['y_ab'], out['y_ref'], rel=1e-12)
    assert close(out['y_ab'], out['y_diff'], rel=1e-12)


# ---------------------------------------------------------------- 逆函数

def test_inverse_cdf_functions_match_legacy():
    out = run_stats("""
import json
from scipy import stats
X = stats.Normal(mu=1., sigma=2.)
ps = [1e-6, 1e-3, 0.1, 0.5, 0.9, 0.999, 1 - 1e-6]
print(json.dumps({
    'icdf': [float(X.icdf(p)) for p in ps],
    'ref_icdf': [float(stats.norm.ppf(p, 1., 2.)) for p in ps],
    'iccdf': [float(X.iccdf(p)) for p in ps],
    'ref_iccdf': [float(stats.norm.isf(p, 1., 2.)) for p in ps],
    'roundtrip': [float(X.icdf(X.cdf(x))) for x in [-2., 0., 1., 3.]],
    'roundtrip_x': [-2., 0., 1., 3.],
}))
""")
    for got, want in zip(out['icdf'], out['ref_icdf']):
        assert close(got, want, rel=1e-12, abs_=1e-10)
    for got, want in zip(out['iccdf'], out['ref_iccdf']):
        assert close(got, want, rel=1e-12, abs_=1e-10)
    for got, want in zip(out['roundtrip'], out['roundtrip_x']):
        assert close(got, want, rel=1e-11, abs_=1e-9)


def test_log_domain_inverses_deep_tail():
    out = run_stats("""
import json, math
from scipy import stats
X = stats.Normal()  # 标准正态
xs = [-8., -20., -35.]
ys = [8., 20., 35.]
print(json.dumps({
    'rt_left': [float(X.ilogcdf(X.logcdf(x))) for x in xs],
    'xs': xs,
    'rt_right': [float(X.ilogccdf(X.logccdf(y))) for y in ys],
    'ys': ys,
    'finite_left': math.isfinite(float(X.ilogcdf(-700.))),
    'finite_right': math.isfinite(float(X.ilogccdf(-700.))),
}))
""")
    for got, want in zip(out['rt_left'], out['xs']):
        assert close(got, want, rel=1e-9, abs_=1e-7)
    for got, want in zip(out['rt_right'], out['ys']):
        assert close(got, want, rel=1e-9, abs_=1e-7)
    assert out['finite_left']
    assert out['finite_right']


# ---------------------------------------------------------------- 矩与特征量

def test_moments_and_summary_statistics():
    out = run_stats("""
import json
from scipy import stats
X = stats.Normal(mu=1., sigma=2.)
print(json.dumps({
    'mean': float(X.mean()),
    'variance': float(X.variance()),
    'std': float(X.standard_deviation()),
    'skew': float(X.skewness()),
    'kurt': float(X.kurtosis()),
    'kurt_excess': float(X.kurtosis(convention='excess')),
    'm4_central': float(X.moment(4, kind='central')),
    'm3_standard': float(X.moment(3, kind='standardized')),
    'm2_raw': float(X.moment(2, kind='raw')),
    'm1_raw': float(X.moment(1, kind='raw')),
}))
""")
    assert close(out['mean'], 1.0)
    assert close(out['variance'], 4.0)
    assert close(out['std'], 2.0)
    assert close(out['skew'], 0.0, abs_=1e-12)
    assert close(out['kurt'], 3.0)            # 默认非超额（Pearson）约定
    assert close(out['kurt_excess'], 0.0, abs_=1e-12)
    assert close(out['m4_central'], 3 * 2.0 ** 4)   # 3 sigma^4
    assert close(out['m3_standard'], 0.0, abs_=1e-12)
    assert close(out['m2_raw'], 4.0 + 1.0)          # sigma^2 + mu^2
    assert close(out['m1_raw'], 1.0)


def test_median_mode_entropy_logentropy():
    out = run_stats("""
import json, math
from scipy import stats
X = stats.Normal(mu=1., sigma=2.)
print(json.dumps({
    'median': float(X.median()),
    'mode': float(X.mode()),
    'entropy': float(X.entropy()),
    'logentropy': float(X.logentropy()),
    'ref_entropy': 0.5 * math.log(2 * math.pi * math.e * 2.0 ** 2),
}))
""")
    assert close(out['median'], 1.0)
    assert close(out['mode'], 1.0)
    assert close(out['entropy'], out['ref_entropy'], rel=1e-12)
    assert close(out['logentropy'], math.log(out['ref_entropy']), rel=1e-12)


def test_support_is_whole_real_line():
    out = run_stats("""
import json, math
from scipy import stats
a, b = stats.Normal(mu=1., sigma=2.).support()
print(json.dumps({'a': float(a), 'b': float(b)}))
""")
    assert math.isinf(out['a']) and out['a'] < 0
    assert math.isinf(out['b']) and out['b'] > 0


# ------------------------------------------------------------ 向量化与健壮性

def test_vectorized_parameters_broadcast():
    out = run_stats("""
import json
import numpy as np
from scipy import stats
X = stats.Normal(mu=np.array([0., 1., 2.]), sigma=2.)
Y = stats.Normal(mu=np.zeros((2, 1)), sigma=np.ones(3))
print(json.dumps({
    'mean_shape': list(np.shape(X.mean())),
    'mean_vals': [float(v) for v in np.atleast_1d(X.mean())],
    'pdf_shape': list(np.shape(X.pdf(0.))),
    'pdf0': float(np.atleast_1d(X.pdf(0.))[0]),
    'y_mean_shape': list(np.shape(Y.mean())),
}))
""")
    assert out['mean_shape'] == [3]
    assert out['mean_vals'] == [0.0, 1.0, 2.0]
    assert out['pdf_shape'] == [3]
    assert close(out['pdf0'], 1.0 / (2.0 * math.sqrt(2 * math.pi)))
    assert out['y_mean_shape'] == [2, 3]   # (2,1) 与 (3,) 广播


def test_invalid_parameters_yield_nan_not_raise():
    out = run_stats("""
import json
import numpy as np
from scipy import stats
bad = stats.Normal(mu=0., sigma=-1.)   # sigma 必须为正
res = {'mean_nan': bool(np.isnan(bad.mean())),
       'pdf_nan': bool(np.isnan(bad.pdf(0.))),
       'cdf_nan': bool(np.isnan(bad.cdf(0.)))}
mixed = stats.Normal(mu=0., sigma=np.array([1., -1.]))
m = np.atleast_1d(mixed.mean())
p = np.atleast_1d(mixed.pdf(0.))
res['mixed_mean'] = [float(m[0]), bool(np.isnan(m[1]))]
res['mixed_pdf'] = [float(p[0]), bool(np.isnan(p[1]))]
print(json.dumps(res))
""")
    assert out['mean_nan'] and out['pdf_nan'] and out['cdf_nan']
    # 数组中无效元素只影响对应位置，其余位置正常
    assert close(out['mixed_mean'][0], 0.0)
    assert out['mixed_mean'][1] is True
    assert close(out['mixed_pdf'][0], 1.0 / math.sqrt(2 * math.pi))
    assert out['mixed_pdf'][1] is True


# ---------------------------------------------------------------- 采样

def test_sample_shape_and_reproducibility():
    out = run_stats("""
import json
import numpy as np
from scipy import stats
X = stats.Normal(mu=1., sigma=2.)
a = X.sample((4, 3), rng=42)
b = X.sample((4, 3), rng=42)
Y = stats.Normal(mu=np.zeros(2), sigma=1.)
s = Y.sample((5,), rng=1)
print(json.dumps({
    'shape': list(a.shape),
    'reproducible': bool(np.array_equal(a, b)),
    'vec_shape': list(s.shape),
}))
""")
    assert out['shape'] == [4, 3]
    assert out['reproducible']          # 同一种子结果可复现
    assert out['vec_shape'] == [5, 2]   # shape + 参数广播形状


def test_sample_statistics_close_to_parameters():
    out = run_stats("""
import json
from scipy import stats
X = stats.Normal(mu=1., sigma=2.)
s = X.sample(200_000, rng=0)
print(json.dumps({'mean': float(s.mean()), 'std': float(s.std())}))
""", timeout=300)
    # 固定种子下容差约为蒙特卡洛标准误差的十倍，宽松但足以区分对错
    assert close(out['mean'], 1.0, abs_=0.05)
    assert close(out['std'], 2.0, abs_=0.05)
