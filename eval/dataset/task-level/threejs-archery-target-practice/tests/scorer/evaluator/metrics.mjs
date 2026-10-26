export function assertionsOfTransition(transition) {
  const verifier = transition?.verifier || transition?.verification || transition?.verify || {};
  const values = verifier.checks ?? transition?.checks ?? transition?.assertions ?? [];
  return (Array.isArray(values) ? values : (values ? [values] : [])).filter(Boolean);
}

export function contractAssertions(contract) {
  return (contract?.transitions || []).flatMap(assertionsOfTransition);
}

export function assertionCoverage(checks) {
  const values = (checks || []).filter(Boolean);
  const passed = values.filter(check => check.pass === true || check.passed === true).length;
  const total = values.length;
  return { passed, total, percent: total > 0 ? passed / total * 100 : 0 };
}

export function zeroAssertionCoverage(contract) {
  return { passed: 0, total: contractAssertions(contract).length, percent: 0 };
}
