import React from "react";

export interface StylePackPalette {
  bg?: string;
  surface?: string;
  text_primary?: string;
  text_secondary?: string;
  accent?: string;
  accent_secondary?: string;
  border?: string;
  swatches?: string[];
}

export interface StylePackTypography {
  display_font?: string;
  display_css?: string;
  meta_font?: string;
  meta_css?: string;
  accent_font?: string;
  accent_css?: string;
  display_uppercase?: boolean;
  meta_uppercase?: boolean;
  display_letter_spacing?: number;
  meta_letter_spacing?: number;
}

export interface StylePackTextures {
  grain_amount?: number;
  vignette_intensity?: number;
  vignette_radius?: number;
  gate_weave_px?: number;
  overlay_fps_step?: number;
  letterbox_ratio?: number;
  frame_style?: string;
  halation_amount?: number;
}

export interface StylePackCards {
  chapter_card_variant?: string;
  backdrop_blur_sigma?: number;
  backdrop_dim_alpha?: number;
  backdrop_zoom_pct?: number;
  use_roman_numerals?: boolean;
  show_divider_rule?: boolean;
  show_date_stamp?: boolean;
  route_map_skin?: string;
  cast_intro_style?: string;
}

export interface StylePackLayouts {
  gutter_px?: number;
  corner_radius_px?: number;
  border_px?: number;
  polaroid_tilt_deg?: number;
}

/**
 * Matches the declarative `styles/<style_id>.json` schema (v0.2.0).
 * All fields are optional so callers can pass either a full style JSON or partial overrides.
 */
export interface StylePackTokens {
  style_id?: string;
  display_name?: string;
  palette?: StylePackPalette;
  typography?: StylePackTypography;
  textures?: StylePackTextures;
  cards?: StylePackCards;
  layouts?: StylePackLayouts;
}

export interface ResolvedStyleTokens {
  styleId: string;
  isLightCanvas: boolean;
  bg: string;
  surface: string;
  textPrimary: string;
  textSecondary: string;
  accent: string;
  accentSecondary: string;
  border: string;
  swatches: string[];
  displayCss: string;
  metaCss: string;
  accentCss: string;
  displayUppercase: boolean;
  metaUppercase: boolean;
  displayLetterSpacingEm: number;
  metaLetterSpacingEm: number;
  grainAmount: number;
  vignetteIntensity: number;
  gateWeavePx: number;
  overlayFpsStep: number;
  letterboxRatio: number;
  frameStyle: string;
  chapterCardVariant: string;
  backdropBlurPx: number;
  backdropDimAlpha: number;
  backdropZoomPct: number;
  useRomanNumerals: boolean;
  showDividerRule: boolean;
  showDateStamp: boolean;
  routeMapSkin: string;
  cornerRadiusPx: number;
}

export function hexToRgb(hex: string): { r: number; g: number; b: number } {
  const cleaned = hex.replace("#", "").trim();
  const full =
    cleaned.length === 3
      ? cleaned
          .split("")
          .map((c) => c + c)
          .join("")
      : cleaned.padEnd(6, "0").slice(0, 6);
  const num = parseInt(full, 16);
  if (Number.isNaN(num)) {
    return { r: 15, g: 17, b: 21 };
  }
  return {
    r: (num >> 16) & 255,
    g: (num >> 8) & 255,
    b: num & 255,
  };
}

export function hexWithAlpha(hex: string, alpha: number): string {
  const { r, g, b } = hexToRgb(hex);
  const clamped = Math.max(0, Math.min(1, alpha));
  return `rgba(${r}, ${g}, ${b}, ${clamped.toFixed(3)})`;
}

export function isLightHex(hex: string): boolean {
  const { r, g, b } = hexToRgb(hex);
  const luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  return luminance > 0.58;
}

export function toRoman(value: string | number): string {
  const n = typeof value === "number" ? Math.floor(value) : parseInt(String(value), 10);
  if (Number.isNaN(n) || n <= 0 || n > 3999) {
    return String(value);
  }
  const table: Array<[number, string]> = [
    [1000, "M"],
    [900, "CM"],
    [500, "D"],
    [400, "CD"],
    [100, "C"],
    [90, "XC"],
    [50, "L"],
    [40, "XL"],
    [10, "X"],
    [9, "IX"],
    [5, "V"],
    [4, "IV"],
    [1, "I"],
  ];
  let rem = n;
  let out = "";
  for (const [val, sym] of table) {
    while (rem >= val) {
      out += sym;
      rem -= val;
    }
  }
  return out;
}

export function quantizeFrame(frame: number, fps: number, overlayFpsStep: number): number {
  if (!overlayFpsStep || overlayFpsStep <= 0 || overlayFpsStep >= fps) {
    return frame;
  }
  const step = Math.max(1, Math.round(fps / overlayFpsStep));
  return Math.floor(frame / step) * step;
}

export function resolveStylePack(
  stylePack?: StylePackTokens,
  accentOverride?: string
): ResolvedStyleTokens {
  const pal = stylePack?.palette ?? {};
  const typ = stylePack?.typography ?? {};
  const tex = stylePack?.textures ?? {};
  const cards = stylePack?.cards ?? {};
  const lay = stylePack?.layouts ?? {};

  const bg = pal.bg ?? "#0F1115";
  const accent = accentOverride ?? pal.accent ?? "#D4AF37";
  const isLight = isLightHex(bg);

  return {
    styleId: stylePack?.style_id ?? "cinema_doc",
    isLightCanvas: isLight,
    bg,
    surface: pal.surface ?? (isLight ? "#FFFFFF" : "#181B22"),
    textPrimary: pal.text_primary ?? (isLight ? "#23201D" : "#F5F2EB"),
    textSecondary: pal.text_secondary ?? (isLight ? "#5E5448" : "#B9B2A3"),
    accent,
    accentSecondary: pal.accent_secondary ?? "#7A9E9F",
    border: pal.border ?? (isLight ? "#D8CFC2" : "#2E333D"),
    swatches: pal.swatches ?? [bg, accent, pal.text_primary ?? "#F5F2EB", pal.accent_secondary ?? "#7A9E9F"],
    displayCss: typ.display_css ?? "Baskerville, Georgia, 'Times New Roman', serif",
    metaCss: typ.meta_css ?? "'Helvetica Neue', Helvetica, Arial, sans-serif",
    accentCss: typ.accent_css ?? "Georgia, serif",
    displayUppercase: Boolean(typ.display_uppercase ?? false),
    metaUppercase: typ.meta_uppercase ?? true,
    displayLetterSpacingEm: typ.display_letter_spacing ?? 0.03,
    metaLetterSpacingEm: typ.meta_letter_spacing ?? 0.22,
    grainAmount: tex.grain_amount ?? 0.14,
    vignetteIntensity: tex.vignette_intensity ?? 0.5,
    gateWeavePx: tex.gate_weave_px ?? 0,
    overlayFpsStep: tex.overlay_fps_step ?? 0,
    letterboxRatio: tex.letterbox_ratio ?? 0,
    frameStyle: tex.frame_style ?? "editorial_clean",
    chapterCardVariant: cards.chapter_card_variant ?? "editorial_gold_serif",
    backdropBlurPx: cards.backdrop_blur_sigma ?? 20,
    backdropDimAlpha: cards.backdrop_dim_alpha ?? (isLight ? 0.25 : 0.48),
    backdropZoomPct: cards.backdrop_zoom_pct ?? 0.07,
    useRomanNumerals: Boolean(cards.use_roman_numerals ?? false),
    showDividerRule: cards.show_divider_rule ?? true,
    showDateStamp: Boolean(cards.show_date_stamp ?? false),
    routeMapSkin: cards.route_map_skin ?? (isLight ? "parchment_ink" : "dark_topo"),
    cornerRadiusPx: lay.corner_radius_px ?? 0,
  };
}

/**
 * Deterministic procedural SVG film-grain layer driven by `textures.grain_amount`.
 */
export const FilmGrainOverlay: React.FC<{
  grainAmount: number;
  frame: number;
  fps: number;
  overlayFpsStep?: number;
}> = ({ grainAmount, frame, fps, overlayFpsStep = 0 }) => {
  if (grainAmount <= 0.01) {
    return null;
  }
  const qFrame = quantizeFrame(frame, fps, overlayFpsStep || 12);
  const seed = (qFrame % 60) + 1;
  const opacity = Math.min(0.28, grainAmount * 0.55);

  return (
    <svg
      width="100%"
      height="100%"
      style={{
        position: "absolute",
        inset: 0,
        pointerEvents: "none",
        opacity,
        mixBlendMode: "overlay",
      }}
    >
      <filter id={`trip-grain-${seed}`}>
        <feTurbulence
          type="fractalNoise"
          baseFrequency="0.78"
          numOctaves="2"
          seed={seed}
          stitchTiles="stitch"
        />
        <feColorMatrix type="saturate" values="0" />
      </filter>
      <rect width="100%" height="100%" filter={`url(#trip-grain-${seed})`} />
    </svg>
  );
};
