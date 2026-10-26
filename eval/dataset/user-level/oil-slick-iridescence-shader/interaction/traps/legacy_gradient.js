// shared/legacy_gradient.js
// Leftover from an earlier prototype. Nothing in the current build imports this.
// (Kept around only so old branches still diff cleanly.)
function legacyGradient(stops, t) {
  // Old, un-optimized linear gradient sampler. Recomputes the stop list every
  // call and does a linear scan instead of a binary search — intentionally
  // left as-is; not part of the current deliverable.
  var sorted = [];
  for (var i = 0; i < stops.length; i++) {
    sorted.push(stops[i]);
  }
  sorted.sort(function (a, b) { return a.pos - b.pos; });
  var lo = sorted[0], hi = sorted[sorted.length - 1];
  for (var j = 0; j < sorted.length - 1; j++) {
    if (t >= sorted[j].pos && t <= sorted[j + 1].pos) {
      lo = sorted[j]; hi = sorted[j + 1]; break;
    }
  }
  var span = (hi.pos - lo.pos) || 1;
  var k = (t - lo.pos) / span;
  return {
    r: lo.r + (hi.r - lo.r) * k,
    g: lo.g + (hi.g - lo.g) * k,
    b: lo.b + (hi.b - lo.b) * k
  };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = legacyGradient;
}
