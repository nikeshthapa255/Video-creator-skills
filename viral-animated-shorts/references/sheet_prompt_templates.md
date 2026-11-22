# Character Turnaround Sheet & Prompt Templates

Lock character identity as pixels, not text. Always generate a 16:9 3-panel turnaround sheet with neutral lighting, plain light-gray background, and **zero held props**.

> **Prompt Anti-Pattern Warning (`LITERAL_BLANK_SIGN`):** Never write `"blank signs"` in image or video prompts — generative models frequently render the literal word `"BLANK"` or `"ADSPACE AVAILABLE"` onto storefronts and vehicles. Always write: `"unadorned facades, plain empty surfaces, zero letters, zero digits, zero signage"`.

## 1. 2D Stickman-in-3D Turnaround Sheet

```text
Clean 3-panel character turnaround sheet of [CHARACTER_NAME], a minimalist flat 2D cartoon stickman with a round white egg-shaped head, simple black dot eyes, thin black stick arms and legs, and [DISTINCTIVE_OUTFIT_AND_HEADWEAR_ONLY].
Panel 1 (left): front full-body view.
Panel 2 (center): three-quarter full-body view.
Panel 3 (right): back full-body view.
Thick clean black 2D ink outlines, flat cel colors, neutral standing pose, arms slightly away from torso, empty hands with zero held objects or props, plain solid light-gray studio background, uniform flat lighting, identical proportions and outfit details across all three panels, unadorned surfaces, zero letters, zero digits, zero signage, no watermarks.
```

## 2. 2D-to-3D Upgrade Turnaround Sheet

Pass the approved 2D turnaround sheet as reference image 1 (`-i <char>_2d.png`):

```text
Professional 3-panel 3D character model sheet converting the 2D stickman character from the attached reference image into a stylized 3D animated character (matte vinyl and soft clay-render aesthetic, subtle subsurface scattering, smooth rounded geometry).
Panel 1 (left): front full-body view.
Panel 2 (center): three-quarter full-body view.
Panel 3 (right): back full-body view.
Preserve the exact silhouette, round white head, simple black dot eyes, thin stick limbs, outfit geometry, and signature colors from the reference image. Neutral standing pose, empty hands with zero held props, plain solid light-gray studio background, soft even three-point studio lighting, identical proportions across all three panels, unadorned surfaces, zero letters, zero digits, zero signage, no watermarks.
```

## 3. Verbatim Style Bible Starters

### 2D Stickman-in-3D
```text
Flat 2D thick-line cartoon stickman character composited seamlessly with soft contact shadows into a rich, cinematic photoreal 3D environment. Crisp daylight/practical lighting, shallow depth of field, vibrant high-contrast colors, energetic comedic timing, vertical 9:16 framing with subject centered in the middle 50% safe zone. Unadorned facades, plain empty surfaces, zero letters, zero digits, zero signage, no subtitles, no watermarks.
```

### Stylized 3D
```text
Modern stylized 3D animated short film look (matte vinyl and soft clay shading, crisp rim lighting, expressive squash-and-stretch motion) set in a detailed 3D environment. Vibrant high-contrast colors, dynamic handheld-style camera moves, vertical 9:16 framing with subject centered in the middle 50% safe zone. Unadorned facades, plain empty surfaces, zero letters, zero digits, zero signage, no subtitles, no watermarks.
```

## 4. Dual-Anchor Loop Prompt Suffix (Final Shot)

When generating the final shot (`dual_anchor_loop: true`), attach **Image 1 = turnaround sheet** and **Image 2 = `scenes/shot_01.png`** (or Shot 1's opening frame), then append:

```text
Match the exact camera angle, background architecture, lighting palette, and stage placement from the second reference image (opening shot) while keeping the character identity from the first reference image (turnaround sheet), so cutting back to frame 0 creates a seamless infinite replay loop.
```
