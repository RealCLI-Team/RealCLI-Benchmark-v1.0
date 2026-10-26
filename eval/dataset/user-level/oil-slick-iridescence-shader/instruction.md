Create a single self-contained HTML file that demonstrates an oil slick pearlescent color shift shader on a 3D surface (a Three.js-style scene: camera, plane/sphere meshes, custom shaders, orbit-style drag controls). The automated grader loads this one file headless in a fully offline sandbox, so everything must be inlined in the single file — no CDN imports and no network fetches of any kind. Requirements:

## Scene Setup
- Black background (0x000000).
- PerspectiveCamera at position (0, 0, 3) looking at origin.
- A large flat plane geometry (PlaneGeometry, 4x4, 128x128 segments) centered at origin, slightly tilted (rotate -0.3 radians on X axis) so the viewer sees it at an angle.
- A secondary smaller sphere (SphereGeometry radius 0.4, 64 segments) floating above the plane at (0, 0.8, 0) also using the same pearlescent shader.
- Ambient light (intensity 0.3) and one DirectionalLight at (2, 3, 2) with intensity 1.0.
- OrbitControls enabled for camera interaction.

## Pearlescent Shader (ShaderMaterial)
- Write custom vertex and fragment shaders implementing thin-film interference / iridescence.
- The vertex shader must pass the world-space normal, world-space position, and UV coordinates to the fragment shader.
- The fragment shader must compute a view-dependent color shift based on the dot product between the view direction and the surface normal (Fresnel-like term).
- Use the Fresnel term to index into a rainbow color palette: map the dot product (clamped 0-1) through a function that produces hues cycling through magenta -> blue -> cyan -> green -> yellow -> red as the angle changes.
- Add a time-based animation uniform `uTime` (incremented each frame) that slowly shifts the color palette offset, creating a flowing oil-slick shimmer. The shift speed should be controlled by a uniform `uSpeed` (default 0.5).
- Add a uniform `uFilmThickness` (default 1.0, range 0.2 to 3.0) that scales the frequency of color bands — higher values produce more tightly packed color fringes.
- Add a uniform `uIntensity` (default 1.0, range 0.0 to 2.0) that multiplies the final iridescent color brightness.

## User Interactions
1. A slider labeled 'Film Thickness' (id='thicknessSlider', min=0.2, max=3.0, step=0.1, default=1.0) that updates `uFilmThickness`.
2. A slider labeled 'Speed' (id='speedSlider', min=0.0, max=2.0, step=0.1, default=0.5) that updates `uSpeed`.
3. A slider labeled 'Intensity' (id='intensitySlider', min=0.0, max=2.0, step=0.1, default=1.0) that updates `uIntensity`.
4. A toggle button (id='pauseBtn') that pauses/resumes the time animation. When paused, `uTime` stops incrementing. Button text shows 'Pause' or 'Resume'.
5. A button (id='resetBtn') labeled 'Reset' that resets all uniforms to defaults and resumes animation if paused.
6. Pressing keyboard key 'T' toggles wireframe mode on both meshes.

## HUD / UI
- Place all controls in a panel (id='controlPanel') positioned top-left with semi-transparent dark background.
- Display current values: a span (id='timeDisplay') showing the current uTime value rounded to 1 decimal, a span (id='thicknessDisplay') showing current film thickness, a span (id='speedDisplay') showing current speed, and a span (id='intensityDisplay') showing current intensity.

## State Management
- Expose `window.__3D_STATE__` with the following fields updated every frame:
  - `time` (number): current uTime value.
  - `filmThickness` (number): current uFilmThickness uniform value.
  - `speed` (number): current uSpeed uniform value.
  - `intensity` (number): current uIntensity uniform value.
  - `paused` (boolean): whether animation is paused.
  - `wireframe` (boolean): whether wireframe mode is active.
  - `planeExists` (boolean): true if plane mesh is in the scene.
  - `sphereExists` (boolean): true if sphere mesh is in the scene.
  - `shaderCompiled` (boolean): true if the ShaderMaterial compiled without errors (check material.program after first render).
  - `cameraZ` (number): camera z position.
  - `fresnelUniformsBound` (boolean): true if the shader material has uTime, uSpeed, uFilmThickness, uIntensity uniforms defined.

Ensure the animation loop uses requestAnimationFrame and updates uTime by delta * speed each frame when not paused.

## State contract (required for grading)

In addition to the DOM elements described above, expose the following fields on `window.__3D_STATE__` (an object) so the grader can inspect runtime state. These field names are the exact keys the grader reads:

- `cameraZ`
- `filmThickness`
- `fresnelUniformsBound`
- `intensity`
- `paused`
- `planeExists`
- `shaderCompiled`
- `speed`
- `sphereExists`
- `time`
- `wireframe`

## DOM contract (required for grading)

Use exactly these DOM element ids/selectors — the grader locates elements by these literal selectors, and alternative names or ids will not be discovered:

- `#thicknessSlider` — ThicknessSlider
- `#speedSlider` — SpeedSlider
- `#intensitySlider` — IntensitySlider
- `#pauseBtn` — PauseButton
- `#resetBtn` — ResetButton
- `#timeDisplay` — TimeDisplay
- `#thicknessDisplay` — ThicknessDisplay
- `#speedDisplay` — SpeedDisplay
- `#intensityDisplay` — IntensityDisplay
- `#controlPanel` — ControlPanel

## Output (required)
Write your complete solution as a single self-contained file named exactly `index.html` at the workspace root (i.e. `index.html`, not `archery.html` or any other name). The grader loads `index.html` and nothing else; any other filename scores zero.
