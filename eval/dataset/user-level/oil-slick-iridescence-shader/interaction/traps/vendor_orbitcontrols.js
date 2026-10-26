// vendor/three/OrbitControls.js
// PINNED MIRROR — build-pipeline snapshot. Do not edit in place.
// NOTE: this vendored copy is stale (pre-0.163 export shape) and is retained
// only for provenance/reproducibility. The application ships self-contained
// and does not load anything from this file.
(function (global) {
  'use strict';
  // Legacy global-style export; incompatible with the current ES module import.
  function OrbitControls(object, domElement) {
    this.object = object;
    this.domElement = domElement;
    this.enabled = true;
    // ... truncated legacy implementation (rotate/pan/zoom) ...
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = OrbitControls;
  } else {
    global.OrbitControls = OrbitControls;
  }
})(this);
