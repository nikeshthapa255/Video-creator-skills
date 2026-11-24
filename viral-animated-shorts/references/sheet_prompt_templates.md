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

## 1D. Multi-state character variant sheet (long-form continuity)

Use in long-form episodes whenever a character holds a distinct visual state across multiple Acts (for example, wearing a face veil in Acts 1-3 before an Act 4 reveal, wearing armor vs formal attire, or carrying a persistent costume change). Attach the approved base 3-panel turnaround sheet (`-i sheets/<char>.png`) and save as `sheets/<char>_<state>.png`:

```text
Professional 3-panel character turnaround sheet of the EXACT same character from the attached turnaround sheet ([CHARACTER_NAME]), keeping identical face structure, hair, proportions, height, lighting, and base costume geometry, with ONLY this locked state variation applied across all three panels: [EXACT_STATE_CHANGE_E.G._TRANSLUCENT_PEARL_BEADED_SILK_FACE_VEIL_COVERING_LOWER_FACE].
Panel 1 (left): front full-body view.
Panel 2 (center): three-quarter full-body view.
Panel 3 (right): back full-body view.
Neutral standing pose, arms slightly away from torso, empty hands with zero held objects or props, plain solid light-gray studio background, soft even three-point studio lighting, identical proportions and state details across all three panels, unadorned surfaces, zero letters, zero digits, zero signage, no watermarks.
```

Register both sheets under `characters[].state_sheets` in `shot_plan.json` and select the active state per shot via `shots[].character_states`.

## 1E. Master location / set anchor plate (long-form continuity)

Use in long-form episodes for any recurring interior or exterior set (Throne Hall, Study, Newsroom, Courtyard) so parallel Act subagents share identical architecture, palette, and key-light direction. Save as `sheets/loc_<id>.png` and register under `locations.<id>.sheet_path` in `shot_plan.json`:

```text
Wide establishing architectural plate of [LOCATION_NAME] in [STYLE_BIBLE_LOOK].
Empty set with ZERO characters, ZERO people, and ZERO moving figures.
Lock clear architectural landmarks: [WALL_MATERIALS_PILLARS_WINDOWS_FURNITURE_PLACEMENT], consistent key lighting from [UPPER_LEFT_OR_RIGHT_LIGHT_DIRECTION] with [WARM_OR_COOL_COLOR_TEMPERATURE] rim fill, shallow atmospheric depth.
[ASPECT_RATIO_FRAMING_NOTE]. Unadorned facades, plain empty surfaces, zero letters, zero digits, zero signage, no subtitles, no watermarks.
```

In Stage 3, attach `Image 1 = Character turnaround/state sheet` and `Image 2 = Location anchor plate` so recurring scenes never drift in architecture or lighting.

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

### 2D stickman-in-3D (9:16 vertical)
```text
Flat 2D thick-line cartoon stickman character composited naturally with soft contact shadows into a rich, cinematic photoreal 3D environment. Crisp daylight/practical lighting, shallow depth of field, vibrant high-contrast colors, energetic comedic timing, vertical 9:16 framing with eyes in the upper-middle 32-42% safe band and subject above the bottom 35% UI/caption zone. Unadorned facades, plain empty surfaces, zero letters, zero digits, zero signage, no subtitles, no watermarks.
```

### Stylized 3D (9:16 vertical Short or micro-drama)
```text
Modern stylized 3D animated film look (refined porcelain/matte-vinyl shading, subtle subsurface skin scattering, crisp rim lighting, expressive motion) set in a detailed 3D environment. Vibrant high-contrast colors, shallow cinematic depth of field, vertical 9:16 framing with eyes in the upper-middle 32-42% safe band and focal action above the bottom 35% UI/caption zone. Unadorned facades, plain empty surfaces, zero letters, zero digits, zero signage, no subtitles, no watermarks.
```

### Stylized 3D (16:9 widescreen long-form episode)
```text
Cinematic stylized 3D animated feature film look (refined matte-vinyl and soft clay shading, luminous subsurface skin scattering, rich practical key lighting against atmospheric rim shadows, detailed 3D architecture and props). Shallow cinematic depth of field, horizontal 16:9 widescreen rule-of-thirds composition with eyes on the upper third line, generous lead room in the gaze direction, and bottom 16% clear for subtitles. Unadorned facades, plain empty surfaces, zero letters, zero digits, zero signage, no subtitles, no watermarks.
```

> **Cultural and historical sets:** Explicitly ban local scripts on props and architecture too (for example, `"unadorned palace pillars, plain silk banners, plain scrolls, zero letters, zero Chinese characters, zero digits, zero signage"`).

## 4. Dual-anchor loop prompt suffix (Short final shot only)

When generating the final shot of a Short (`dual_anchor_loop: true`), attach **Image 1 = turnaround sheet** and **Image 2 = `scenes/shot_01.png`** (or Shot 1's opening frame), then append:

```text
Match the exact camera angle, background architecture, lighting palette, and stage placement from the second reference image (opening shot) while keeping the character identity from the first reference image (turnaround sheet), so cutting back to frame 0 creates a continuous replay loop.
```

Omit this suffix on long-form episodes (`ending_style: "cliffhanger"` or `"outro"`, `dual_anchor_loop: false`).

## 5. Cast isolation and staging clauses (prevents `SOLO_CHAR_BLEED`, `MULTI_CHAR_BLEED`, and `EYELINE_CROSS`)

### Solo character (~70% of long-form shots)
Pass **only** that character's active turnaround/state sheet (plus optional location plate) and prepend:

```text
SOLO character: ONLY [CHARACTER_NAME] (matching the first attached turnaround sheet) appears in this frame, positioned on [CENTER / LEFT_THIRD / RIGHT_THIRD] looking [SCREEN_RIGHT / SCREEN_LEFT / AT_CAMERA]; no other named characters, mascots, or co-hosts appear in the foreground.
```

### Over-the-Shoulder (OTS) dialogue shot (`16:9` intimacy without bleed)
Attach **only** the facing speaker's turnaround sheet (not the foreground listener's!) and prepend:

```text
Over-the-shoulder widescreen shot: dark, soft-blurred back-of-shoulder silhouette of an off-camera listener anchoring the extreme [LEFT / RIGHT] foreground edge; ONLY [SPEAKER_NAME] (matching the attached turnaround sheet) is in sharp focus on the opposite third looking toward the foreground shoulder.
```

### Zero-lead insert / establishing wide (~15-20% of long-form shots)
Attach **no** lead turnaround sheets (optionally attach the location plate) and prepend:

```text
No named lead characters appear in the foreground of this frame.
```

### Two-character confrontation (<= 12% of long-form shots; never 3+ leads)
Attach **only** the two characters' active sheets in left-to-right order and prepend:

```text
Two-character confrontation: [CHARACTER_A] (matching the first attached turnaround sheet) positioned on the left looking right and [CHARACTER_B] (matching the second attached turnaround sheet) positioned on the right looking left, with clear spatial separation and consistent relative height to prevent costume or face bleed.
```
