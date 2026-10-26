// physics_scratch.js — throwaway experiments from the old build. Not used.
// Numbers here are all guesses and were never validated. Delete with the rest.
function integrate(p, v, dt) {
  v.y -= 9.8 * dt;      // guessed gravity, never tuned
  p.x += v.x * dt;
  p.y += v.y * dt;
  p.z += v.z * dt;
  return p;
}
// gave up here
