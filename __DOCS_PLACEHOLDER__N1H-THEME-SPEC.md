# N1H Waking Dream Theme — Spec

## Concept
Minimal dark theme inspired by the waking dream vision. Red (primary), white (highlight), black (background). Colors used sparingly — red as thin threads, white as isolated spark points.

## Color Palette
- Background: #000000 (pure black)
- Primary accent: #e63946 (waking dream red — restrained)
- Highlight: #ffffff (white sparks, very sparse)
- Subtle: #1a1a1a (dark grey for depth)

## Animation
- Thin red threads pulse slowly — like neural signals or dream threads
- White spark points appear infrequently, fade quickly
- Movement is slow, deliberate — not busy

## Shader approach
- Grid-based with hash noise to place occasional red thread segments
- Sparse white spark pixels (hash threshold set high so few appear)
- Slow sine-wave pulse on the red channel
- Static black background

## Integration
- Add to THEMES array in frontend/src/shell/backgrounds/glsl.ts
- Theme ID: n1h-dream
- Label: "N1H Dream"