Create a single HTML file with an interactive 3D archery target practice game using Three.js with a physics debug overlay.

## Scene Setup
- Use Three.js with GLTFLoader. The page is graded in a local headless Chromium environment (offline-first): the CDN is reachable but not guaranteed. For a stable score, download three.module.js and any addons you use (e.g. GLTFLoader) into your workspace at runtime (e.g. a `vendor/` subdirectory — subdirectories of the workspace are served by the static file server) and reference them via your own importmap or relative paths. If you instead rely on the CDN and bare `three` imports without your own importmap, the grader will inject an importmap pointing at three@0.170.0 — write against the r170 API in that case
- Camera: PerspectiveCamera at position (0, 2, 8), looking toward (0, 2, -20)
- Lighting: Ambient light (0x404040, intensity 0.6) + DirectionalLight (0xffffff, intensity 1.0) at position (5, 10, 5) with castShadow enabled
- Ground: Large flat plane (100x100) with green material (0x228B22), receiveShadow, at y=0
- Sky: Set renderer.setClearColor(0x87CEEB)
- Enable shadows on the renderer

## Archer Character
- Load the 3D model 'Xbot.glb' using GLTFLoader and place it at position (0, 0, 6), facing negative Z direction (toward target). Note: 'Xbot.glb' is not present in your workspace; it is provided by the grader at scoring time, served by bare filename — reference it as exactly `'Xbot.glb'` (no directory prefix). If loading fails, degrade gracefully: the page's initial rendering must not be blocked by a missing model
- Scale the model to 1.5 units tall approximately (adjust scale as needed)
- The character represents the archer

## Target
- Create a circular archery target at position (0, 2.5, -15) facing positive Z (toward the archer)
- The target consists of 5 concentric cylinders (very thin, radius decreasing):
  - Ring 0.8 (outermost): radius 1.6, color white (0xFFFFFF) = 1 point
  - Ring 2: radius 1.28, color black (0x222222) = 3 points
  - Ring 3: radius 0.96, color blue (0x0066FF) = 5 points
  - Ring 4: radius 0.64, color red (0xFF0000) = 7 points
  - Ring 5 (bullseye): radius 0.32, color gold (0xFFD700) = 10 points
- Each ring is a CylinderGeometry with height 0.05, stacked slightly in Z to prevent z-fighting

## Arrow Mechanics
- Arrows are thin cylinders (radius 0.02, length 1.5) colored brown (0x8B4513) with a small cone tip (0x888888)
- The player aims using mouse position: map mouse screen coordinates to an aim offset. The arrow launch direction should deviate from straight forward based on mouse position relative to screen center. Horizontal mouse = horizontal aim, vertical mouse = vertical aim adjustment.
- Press SPACE to shoot an arrow. The arrow spawns at the archer position (0, 2.0, 5.5) and travels with projectile physics:
  - Initial speed: 24 units/second in the aimed direction
  - Gravity: -7.85 m/s² on Y axis
  - No air resistance needed
- When an arrow's Z position reaches <= -14.8 (target plane), check if it hits the target by measuring XY distance from target center (0, 2.5). Assign points based on which ring it lands in. If it misses all rings (distance > 2.0), score 0 for that shot.
- Arrows stick where they land (stop moving and remain visible in the scene)
- Maximum 5 arrows per round

## Aim Indicator
- Show a small crosshair reticle on screen (DOM overlay) that follows the mouse cursor, made with a '+' character or CSS crosshair, colored red, size 30px

## Physics Debug Overlay
- Create a toggleable debug overlay (DOM div, id='debugOverlay') with semi-transparent dark background, positioned top-right, width 300px
- Toggle with 'D' key
- Display in real-time:
  - Current arrow velocity (vx, vy, vz) if arrow is in flight
  - Arrow position (x, y, z) if in flight
  - Gravity applied per frame
  - Trajectory angle in degrees
  - Time of flight for current arrow
  - Last hit distance from center
- Initially the debug overlay is hidden (display:none)

## HUD Elements
- Top-left HUD (DOM) showing:
  - 'Score: X' (id='scoreDisplay')
  - 'Arrows: X/5' (id='arrowDisplay')
  - 'Best Shot: X pts' (id='bestShotDisplay')
- A 'Reset Round' button (id='resetBtn') at bottom-center that resets score to 0, arrows to 5, removes all arrow meshes, and resets best shot

## Wind System
- Implement a simple wind effect: a random wind vector (x component between -2 and 2, z component 0) that applies a constant horizontal force to arrows in flight
- Display wind direction and strength on HUD (id='windDisplay'), e.g., 'Wind: 1.3 →' or 'Wind: -0.8 ←'
- Wind changes randomly each time a new round starts (on reset)

## State Management
Expose window.__3D_STATE__ with these fields updated every frame:
- arrowsRemaining: number (starts at 5)
- score: number (starts at 0)
- bestShot: number (best single arrow score, starts at 0)
- arrowInFlight: boolean
- currentArrowPos: {x, y, z} or null
- currentArrowVel: {x, y, z} or null
- lastHitDistance: number or null (distance from target center of last landed arrow)
- lastHitScore: number (points from last arrow, 0 if miss)
- debugVisible: boolean (is debug overlay visible)
- windX: number (current wind x force)
- totalArrowsFired: number (cumulative arrows shot)
- modelLoaded: boolean (true once Xbot.glb loads)
- roundNumber: number (starts at 1, increments on reset)

Ensure all physics values are NaN-free. Clamp positions to reasonable bounds. The animation loop should use requestAnimationFrame with delta time from THREE.Clock.

## Physics Constraints
Projectile motion: arrows follow parabolic trajectory under gravity (-7.85 m/s²) with constant wind force on X axis. Initial arrow speed 24 units/s. Arrows stop upon reaching target plane (z <= -14.8). No collision with ground needed but arrows should not go below y=0. All velocity and position values must remain finite (no NaN/Infinity).

## Output (required)
Write your complete solution as a single self-contained file named exactly `index.html` at the workspace root (i.e. `index.html`, not `archery.html` or any other name). The grader loads `index.html` and nothing else; any other filename scores zero.
