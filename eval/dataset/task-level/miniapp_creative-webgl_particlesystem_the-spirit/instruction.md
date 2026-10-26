Build the following frontend project: The Spirit – Interactive WebGL Particle Simulation.

I want a cool full-screen animated art piece you can just open in a browser and watch. It should show thousands of tiny glowing particles flowing around like smoke or a ghost – they should follow your mouse so it feels interactive. There should be a nice logo in the corner that fades in when the page loads, some small links at the bottom to my site, GitHub, and a video, and a panel of sliders so visitors can tweak how the particles look and move. If someone opens it on a phone, show them a friendly message saying it works best on desktop, but let them try it anyway if they want.

Frontend plan:
{
  "pages": [
    {
      "name": "Main Experience",
      "route": "/",
      "description": "Single-page full-screen WebGL particle experience. On desktop a full 3D simulation fills the viewport. On mobile a fallback message is shown instead, with an optional bypass link to force-load the experience.",
      "layout": {
        "header": false,
        "footer": true,
        "sections": [
          {
            "name": "mobile-fallback",
            "components": [
              "MobileFallbackPanel",
              "MobileBypassLink"
            ]
          },
          {
            "name": "webgl-canvas",
            "components": [
              "WebGLRenderer",
              "Scene3D",
              "ParticleSystem",
              "FloorMesh",
              "LightRig",
              "OrbitCamera"
            ]
          },
          {
            "name": "overlay-logo",
            "components": [
              "LogoBadge"
            ]
          },
          {
            "name": "instruction-bar",
            "components": [
              "InstructionText"
            ]
          },
          {
            "name": "dat-gui-panel",
            "components": [
              "SimulatorGUIFolder",
              "RenderingGUIFolder",
              "PostProcessingGUIFolder"
            ]
          },
          {
            "name": "footer-bar",
            "components": [
              "FooterLinks"
            ]
          }
        ]
      },
      "content": [
        {
          "type": "webgl",
          "description": "Full-screen real-time 3D particle simulation rendered with WebGL. Tens of thousands of particles flow in curl-noise fields, casting and receiving soft shadows on a reflective metallic floor plane."
        },
        {
          "type": "image",
          "description": "Centered logo image (The Spirit branding) that appears with animated scale and blur fade-in on page load, positioned toward the right on wide screens."
        },
        {
          "type": "text",
          "description": "Instruction hint at the bottom of the screen: 'Press space key to toggle movement'."
        },
        {
          "type": "text",
          "description": "Mobile fallback message explaining the experiment is designed for desktop, providing an optional bypass link, a YouTube video link, and a GitHub source link."
        },
        {
          "type": "links",
          "description": "Footer links to the author's personal website (Edan Kwan), the GitHub repository, and the YouTube video capture of the experiment."
        },
        {
          "type": "controls",
          "description": "dat.GUI debug panel exposing: Simulator folder (particle amount selector with page reload, speed, die speed, radius, curl size, attraction strength, follow-mouse toggle); Rendering folder (shadow darkness, new-particle toggle, base color picker, fade color picker, background color picker); Post-Processing folder (FXAA toggle, motion blur toggle with distance/multiplier/quality controls, bloom toggle with radius and amount controls)."
        }
      ],
      "navigationLinks": [
        {
          "label": "Edan Kwan",
          "targetRoute": "http://www.edankwan.com/",
          "when": "footer link click – opens in new tab"
        },
        {
          "label": "Github",
          "targetRoute": "https://github.com/edankwan/The-Spirit",
          "when": "footer link click – opens in new tab"
        },
        {
          "label": "Youtube",
          "targetRoute": "https://www.youtube.com/watch?v=lcHAtTGzQE8",
          "when": "footer link click – opens in new tab"
        },
        {
          "label": "here (bypass)",
          "targetRoute": "/",
          "when": "mobile fallback bypass link click – removes fallback overlay and starts the WebGL experience"
        }
      ]
    }
  ],
  "sharedComponents": [
    {
      "name": "WebGLRenderer",
      "purpose": "Three.js WebGLRenderer canvas filling the full viewport. Configured with soft PCF shadow maps, anti-aliasing, and a dark background clear color."
    },
    {
      "name": "Scene3D",
      "purpose": "Root Three.js scene containing exponential fog, the particle container, the floor mesh, and the light rig."
    },
    {
      "name": "ParticleSystem",
      "purpose": "GPU-simulated particle mesh supporting two render modes: classic point sprites and triangle-based 'new particles'. Each particle reads its position from a GPGPU floating-point render texture updated each frame."
    },
    {
      "name": "GPGPUSimulator",
      "purpose": "Off-screen ping-pong render-target simulator. Each frame runs a GLSL fragment shader that advances particle positions using curl noise, attraction toward a 3D mouse target or auto-moving follow point, life decay, and an animated spawn animation."
    },
    {
      "name": "FloorMesh",
      "purpose": "Large horizontal plane with a dark metallic standard material. Receives shadows from the particle system."
    },
    {
      "name": "LightRig",
      "purpose": "Three-light setup: ambient light, a white point light with high-resolution shadow map, and two colored directional lights (warm rose and cool teal) for directional shading."
    },
    {
      "name": "OrbitCamera",
      "purpose": "Perspective camera with orbit controls (left-drag to rotate, scroll to zoom). Polar angle and max zoom distance are constrained; panning is disabled."
    },
    {
      "name": "PostProcessingPipeline",
      "purpose": "Effect composer that queues FXAA, motion blur, and bloom post-processing passes on the rendered scene before output to screen."
    },
    {
      "name": "MotionBlurEffect",
      "purpose": "Per-object velocity-buffer motion blur using GPU line-segment rasterization or multi-sample fallback. Configurable max distance, motion multiplier, and quality scale."
    },
    {
      "name": "BloomEffect",
      "purpose": "Two-pass (horizontal + vertical) Gaussian blur combined additively with the original render to produce a configurable bloom glow."
    },
    {
      "name": "FXAAEffect",
      "purpose": "Full-screen FXAA anti-aliasing pass (standard or low-quality variant)."
    },
    {
      "name": "LogoBadge",
      "purpose": "Absolutely positioned logo image that fades in and scales up during the init animation, shifting right on wide screens. Hidden on mobile."
    },
    {
      "name": "InstructionText",
      "purpose": "Small overlay text at the bottom of the screen hinting at the spacebar toggle. Slides up from below during the init animation. Hidden on mobile."
    },
    {
      "name": "FooterLinks",
      "purpose": "Row of links (author site, GitHub, YouTube) aligned bottom-right. Each span slides up from below with a staggered delay during the init animation."
    },
    {
      "name": "MobileFallbackPanel",
      "purpose": "Full-screen centered overlay shown on mobile devices, explaining the desktop-only experience and providing alternative links (video, GitHub)."
    },
    {
      "name": "MobileBypassLink",
      "purpose": "Inline clickable span inside the mobile fallback panel. Clicking it removes the panel and starts the WebGL experience regardless of device."
    },
    {
      "name": "SimulatorGUIFolder",
      "purpose": "dat.GUI folder for particle simulation parameters: amount (64×64 to 2048×2048 with page reload), speed, die speed, radius, curl size, attraction, and follow-mouse toggle."
    },
    {
      "name": "RenderingGUIFolder",
      "purpose": "dat.GUI folder for visual parameters: shadow darkness, particle style toggle (points vs. triangles), base color, fade color, and background color."
    },
    {
      "name": "PostProcessingGUIFolder",
      "purpose": "dat.GUI folder for post-processing toggles and settings: FXAA on/off, motion blur on/off with distance/multiplier/quality controls, bloom on/off with radius and amount controls."
    }
  ],
  "stateManagement": "All runtime state is stored in a plain CommonJS settings module (singleton object with exported mutable properties). The dat.GUI panel reads and writes directly to this module. Frame state is managed by a requestAnimationFrame loop; GPGPU particle positions are stored in WebGL floating-point render targets. No framework or reactive state library is used.",
  "accessibilityAndUX": [
    "Mobile detection via user-agent string; mobile users see a fallback message instead of the heavy WebGL scene",
    "Optional mobile bypass link allows capable mobile devices to force-load the experience",
    "Spacebar keyboard shortcut to toggle particle movement on/off",
    "Viewport meta tag prevents user scaling on mobile",
    "Open Graph and Twitter Card meta tags for rich social sharing previews",
    "Smooth animated entrance: logo scales and blurs in, footer links and instruction text slide up with staggered timing",
    "Logo scales down by 50% on narrow screens (< 580 px) to avoid overflow",
    "Logo repositions to the right side on screens wider than 960 px to not obscure the center particle formation",
    "Orbit controls constrained to prevent gimbal lock and disorienting panning; zoom distance limited to 1000 units",
    "dat.GUI checkbox and select inputs have keyboard events suppressed to avoid conflicts with spacebar toggle",
    "Exponential scene fog and background color are interpolated each frame for smooth color transitions when background color is changed at runtime"
  ]
}
