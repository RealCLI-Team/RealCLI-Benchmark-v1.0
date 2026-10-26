/**
 * Layer 2: StateProbe Protocol — standardised state extraction from 3D scenes.
 *
 * Responsibilities:
 *   - Detect whether the page exposes window.__3D_STATE__
 *   - Diagnose failure mode (RUNTIME_CRASH vs PROBE_MISSING)
 *   - Snapshot before/after state for each transition
 *   - Time-series sampling for physics checks
 */

export class StateProbeProtocol {
  constructor(page) {
    this.page = page;
  }

  async detect() {
    let hasProbe = false;
    try {
      hasProbe = await this.page.evaluate(
        () => typeof window.__3D_STATE__ === 'object' && window.__3D_STATE__ !== null
      );
    } catch { /* swallow */ }

    if (hasProbe) return { available: true, failureMode: null };

    const diagnosis = await this.page.evaluate(() => {
      for (const s of document.querySelectorAll('script')) {
        if (s.textContent.includes('__3D_STATE__')) return { hasProbeCode: true };
      }
      return { hasProbeCode: false };
    }).catch(() => ({ hasProbeCode: false }));

    return {
      available: false,
      failureMode: diagnosis.hasProbeCode ? 'RUNTIME_CRASH' : 'PROBE_MISSING',
    };
  }

  async snapshot() {
    return this.page.evaluate(() => JSON.parse(JSON.stringify(window.__3D_STATE__)));
  }

  /**
   * Sample __3D_STATE__ every `intervalMs` for `count` frames.
   * Returns an array of snapshots for time-series analysis.
   */
  async sampleTimeSeries(count = 10, intervalMs = 100) {
    const samples = [];
    for (let i = 0; i < count; i++) {
      samples.push(await this.snapshot());
      if (i < count - 1) await this.page.waitForTimeout(intervalMs);
    }
    return samples;
  }

  /**
   * Evaluate a JS expression in page context with access to `before`, `after`,
   * and the live `window.__3D_STATE__`.
   */
  async evalCheck(expression, before, after) {
    try {
      const passed = await this.page.evaluate(
        ({ check, before, after }) => eval(check),
        { check: expression, before, after }
      );
      return { passed: !!passed, detail: expression };
    } catch (e) {
      return { passed: false, detail: `${expression} → Error: ${e.message}` };
    }
  }
}
