# Character turnaround sheet and prompt templates

Lock character identity as pixels, not text. Always generate a 16:9 3-panel turnaround sheet with neutral lighting, a plain light-gray background, and **zero held props**.

> **Avoid "blank signs" (`LITERAL_BLANK_SIGN`):** Never put `"blank signs"` in image or video prompts. Image models often print the literal text `"BLANK"` or `"ADSPACE AVAILABLE"` on storefronts and vehicles. Write `"unadorned facades, plain empty surfaces, zero letters, zero digits, zero signage"` instead.

## 1A. 2D stickman-in-3D turnaround sheet

```text
Clean 3-panel character turnaround sheet of [CHARACTER_NAME], a minimalist flat 2D cartoon stickman with a round white egg-shaped head, simple black dot eyes, thin black stick arms and legs, and [DISTINCTIVE_OUTFIT_AND_HEADWEAR_ONLY].
Panel 1 (left): front full-body view.
Panel 2 (center): three-quarter full-body view.
Panel 3 (right): back full-body view.
Thick clean black 2D ink outlines, flat cel colors, neutral standing pose, arms slightly away from torso, empty hands with zero held objects or props, plain solid light-gray studio background, uniform flat lighting, identical proportions and outfit details across all three panels, unadorned surfaces, zero letters, zero digits, zero signage, no watermarks.
```

## 1B. Official product mascot turnaround sheet

Use whenever featuring a real brand or product mascot (for example, a plush AI companion or official software mascot). Attach the verified Stage 0 reference photo (`-i <ref_mascot_image>` from `research_characters.py --user-ref` or web search):

```text
Professional 3-panel character turnaround sheet of the exact official mascot shown in the attached reference image ([MASCOT_NAME]).
Panel 1 (left): front full-body view.
Panel 2 (center): three-quarter full-body view.
Panel 3 (right): back full-body view.
Faithfully preserve the EXACT silhouette, head-to-body proportions, ears/appendages, eye shape, facial expression, surface texture/material ([e.g., soft plush fabric / matte vinyl / glossy 3D clay]), and exact color palette from the attached reference image. Do NOT invent extra limbs, different species traits, or new accessories. Neutral standing pose, arms slightly away from torso, empty hands with zero held objects or props, plain solid light-gray studio background, soft even three-point studio lighting, identical proportions across all three panels, unadorned surfaces, zero letters, zero digits, zero signage, no watermarks.
```

## 1C. Stylized 3D real-person turnaround sheet

Use for public figures (CEOs, executives, scientists, commentators) after verifying their portrait in Stage 0 with `scripts/research_characters.py`.

**Primary path (when photo-to-image is permitted):** Attach the verified Stage 0 portrait (`-i <ref_portrait>`) and run:

```text
Professional 3-panel stylized 3D animated character turnaround sheet of [PERSON_NAME] matching the attached reference portrait (modern Pixar/vinyl-clay stylized 3D aesthetic, clean readable caricature, respectful authentic likeness).
Panel 1 (left): front full-body view.
Panel 2 (center): three-quarter full-body view.
Panel 3 (right): back full-body view.
Accurately preserve the exact hairstyle and hair color, hairline, facial structure, age cues, skin tone, eyewear (if worn in the reference), and signature attire ([SIGNATURE_ATTIRE_AND_COLORS]) from the reference portrait. Neutral standing pose, arms slightly away from torso, empty hands with zero held objects or props, plain solid light-gray studio background, soft even three-point studio lighting, identical proportions and wardrobe across all three panels, unadorned surfaces, zero letters, zero digits, zero signage, no watermarks.
```

**Celebrity-filter fallback (when an image model blocks direct photo-to-image on a public figure):**
1. Inspect the Stage 0 portrait with `read_file` or `view_file`.
2. Do **not** upload the raw portrait photo. Fill every observable trait slot in the prompt below:
   ```text
   Professional 3-panel stylized 3D animated character turnaround sheet of a [AGE_RANGE] [GENDER/BUILD] tech executive character (modern matte-vinyl and soft clay-render 3D animation style, crisp readable features).
   Visual traits locked from reference inspection: [EXACT_HAIR_COLOR_LENGTH_AND_STYLE], [EXACT_FACIAL_STRUCTURE_JAW_AND_EXPRESSION], [SKIN_TONE], [EXACT_EYEWEAR_OR_NO_GLASSES], wearing [EXACT_SIGNATURE_OUTFIT_E.G._BLACK_MOTO_LEATHER_JACKET_OVER_BLACK_CREWNECK_AND_DARK_JEANS].
   Panel 1 (left): front full-body view.
   Panel 2 (center): three-quarter full-body view.
   Panel 3 (right): back full-body view.
   Neutral standing pose, arms slightly away from torso, empty hands with zero held props, plain solid light-gray studio background, soft even three-point studio lighting, identical proportions and wardrobe across all three panels, unadorned surfaces, zero letters, zero digits, zero signage, no watermarks.
   ```
3. Inspect the generated 3-panel turnaround sheet **side by side** against the Stage 0 portrait (`read_file` or `view_file`) to confirm zero `LIKENESS_DRIFT`, then set `likeness_verified: true`.
4. Anchor **every** Stage 3 shot to this approved 3-panel sheet only.

## 2. 2D-to-3D upgrade turnaround sheet

Pass the approved 2D turnaround sheet as reference image 1 (`-i <char>_2d.png`):

```text
Professional 3-panel 3D character model sheet converting the 2D stickman character from the attached reference image into a stylized 3D animated character (matte vinyl and soft clay-render aesthetic, subtle subsurface scattering, smooth rounded geometry).
Panel 1 (left): front full-body view.
Panel 2 (center): three-quarter full-body view.
Panel 3 (right): back full-body view.
Preserve the exact silhouette, round white head, simple black dot eyes, thin stick limbs, outfit geometry, and signature colors from the reference image. Neutral standing pose, empty hands with zero held props, plain solid light-gray studio background, soft even three-point studio lighting, identical proportions across all three panels, unadorned surfaces, zero letters, zero digits, zero signage, no watermarks.
```

## 3. Verbatim style bible starters

### 2D stickman-in-3D
```text
Flat 2D thick-line cartoon stickman character composited naturally with soft contact shadows into a rich, cinematic photoreal 3D environment. Crisp daylight/practical lighting, shallow depth of field, vibrant high-contrast colors, energetic comedic timing, vertical 9:16 framing with subject centered in the middle 50% safe zone. Unadorned facades, plain empty surfaces, zero letters, zero digits, zero signage, no subtitles, no watermarks.
```

### Stylized 3D
```text
Modern stylized 3D animated short film look (matte vinyl and soft clay shading, crisp rim lighting, expressive squash-and-stretch motion) set in a detailed 3D environment. Vibrant high-contrast colors, dynamic handheld-style camera moves, vertical 9:16 framing with subject centered in the middle 50% safe zone. Unadorned facades, plain empty surfaces, zero letters, zero digits, zero signage, no subtitles, no watermarks.
```

## 4. Dual-anchor loop prompt suffix (final shot)

When generating the final shot (`dual_anchor_loop: true`), attach **Image 1 = turnaround sheet** and **Image 2 = `scenes/shot_01.png`** (or Shot 1's opening frame), then append:

```text
Match the exact camera angle, background architecture, lighting palette, and stage placement from the second reference image (opening shot) while keeping the character identity from the first reference image (turnaround sheet), so cutting back to frame 0 creates a continuous replay loop.
```

## 5. Solo-character override clause (prevents `SOLO_CHAR_BLEED`)

For a single-character shot, pass **only** that character's turnaround sheet and prepend this isolation clause before the scene description. It prevents `style_bible` or cast descriptions from leaking a second character or co-host into the frame:

```text
SOLO character: ONLY [CHARACTER_NAME] (matching the attached turnaround sheet) appears in this frame; no other characters, mascots, co-hosts, or background figures appear anywhere in the scene.
```
