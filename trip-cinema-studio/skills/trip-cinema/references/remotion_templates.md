# Remotion Graphics Templates

The `remotion_template/` directory provides two zero-API-key, 16:9 React compositions for trip titles and animated GPS route maps.

## Compositions

1. **`TripRouteMap`** (`src/TripRouteMap.tsx`)
   - Renders an animated GPS path across chapter waypoints (`waypoints` from `manifest.json` / `edl.json`) using pure SVG projection (zero paid map API key required).
   - Props:
     - `tripTitle`: `string`
     - `waypoints`: `Array<{ label: string; lat: number; lon: number }>`
     - `accentColor`: `string` (optional, default `"#E8A87C"`)

2. **`ChapterTitle`** (`src/ChapterTitle.tsx`)
   - Renders a minimalist editorial chapter title card using verified EXIF date and coordinates.
   - Props:
     - `chapterNumber`: `string` (e.g., `"01"`)
     - `title`: `string`
     - `dateLabel`: `string` (e.g., `"2026-10-14"`)
     - `coordinatesLabel`: `string` (optional, e.g., `"36.6002° N, 121.8947° W"`)
     - `accentColor`: `string` (optional, default `"#E8A87C"`)

## Rendering Overlays

Install dependencies once inside `skills/trip-cinema/remotion_template/`:
```bash
npm install
```

Render a 16:9 title or route map card:
```bash
npx remotion render src/index.ts ChapterTitle ./work/overlays/ch1_title.mp4 \
  --props='{"chapterNumber":"01","title":"Arrival by the Coast","dateLabel":"2026-10-14","coordinatesLabel":"36.6002° N, 121.8947° W","accentColor":"#E8A87C"}'
```

For alpha-transparent overlays composited over live footage in FFmpeg, render as ProRes 4444 or WebM VP9 with `--image-format=png --pixel-format=yuva444p10le` (ProRes) or `--codec=vp8 --pixel-format=yuva420p`, then composite via FFmpeg `overlay=0:0:shortest=1`.
