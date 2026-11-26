import React from "react";
import { AbsoluteFill, Img, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import {
  FilmGrainOverlay,
  hexWithAlpha,
  quantizeFrame,
  resolveStylePack,
  StylePackTokens,
} from "./stylePack";

export interface TripWrappedStat {
  label: string;
  /** Numeric values animate smoothly from 0 -> value; string values reveal cleanly. */
  value: number | string;
  unit?: string;
  prefix?: string;
  suffix?: string;
  icon?: string;
  detail?: string;
  /** Guardrail: metrics with EXIF/GPS coverage < 30% are automatically omitted. */
  coverage_pct?: number;
}

export type TripWrappedCardProps = {
  tripTitle: string;
  eyebrow?: string;
  dateRange?: string;
  stats: TripWrappedStat[];
  /** Optional blurred slow-zooming hero photo backdrop. */
  backdropSrc?: string;
  accentColor?: string;
  /** Full or partial Style Pack JSON matching `styles/<style_id>.json`. */
  stylePack?: StylePackTokens;
};

function easeOutCubic(t: number): number {
  const c = Math.max(0, Math.min(1, t));
  return 1 - Math.pow(1 - c, 3);
}

function parseNumericStat(value: number | string): {
  isNumeric: boolean;
  target: number;
  decimals: number;
  prefix: string;
  suffix: string;
} {
  if (typeof value === "number" && Number.isFinite(value)) {
    const str = String(value);
    const dot = str.indexOf(".");
    return {
      isNumeric: true,
      target: value,
      decimals: dot >= 0 ? Math.min(2, str.length - dot - 1) : 0,
      prefix: "",
      suffix: "",
    };
  }
  const raw = String(value).trim();
  const match = raw.match(/^([^0-9-]*)(-?\d[\d,]*(?:\.\d+)?)(.*)$/);
  if (!match) {
    return { isNumeric: false, target: 0, decimals: 0, prefix: "", suffix: "" };
  }
  const numStr = match[2].replace(/,/g, "");
  const parsed = Number(numStr);
  if (!Number.isFinite(parsed)) {
    return { isNumeric: false, target: 0, decimals: 0, prefix: "", suffix: "" };
  }
  const dot = numStr.indexOf(".");
  return {
    isNumeric: true,
    target: parsed,
    decimals: dot >= 0 ? Math.min(2, numStr.length - dot - 1) : 0,
    prefix: match[1] ?? "",
    suffix: match[3] ?? "",
  };
}

function formatAnimatedNumber(val: number, decimals: number): string {
  if (decimals > 0) {
    return val.toLocaleString("en-US", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  }
  return Math.round(val).toLocaleString("en-US");
}

/**
 * Animated EXIF Trip Wrapped statistics dashboard card.
 * Smoothly counts up verified EXIF/GPS metrics (days, km traveled, peak altitude,
 * golden hour shots, cameras) using Remotion springs and active Style Pack tokens.
 */
export const TripWrappedCard: React.FC<TripWrappedCardProps> = ({
  tripTitle,
  eyebrow = "TRIP WRAPPED",
  dateRange,
  stats,
  backdropSrc,
  accentColor,
  stylePack,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const tokens = resolveStylePack(stylePack, accentColor);

  // Enforce verified-metrics guardrail: drop any stat explicitly flagged with <30% coverage
  const visibleStats = (stats ?? [])
    .filter((s) => s.coverage_pct === undefined || s.coverage_pct >= 30)
    .slice(0, 6);

  const progress = durationInFrames > 1 ? frame / (durationInFrames - 1) : 1;
  const qFrame = quantizeFrame(frame, fps, tokens.overlayFpsStep);

  const headerOpacity = interpolate(qFrame, [0, 16], [0, 1], { extrapolateRight: "clamp" });
  const headerSlide = interpolate(qFrame, [0, 18], [18, 0], { extrapolateRight: "clamp" });
  const fadeOutStart = Math.max(0, durationInFrames - 16);
  const fadeOut =
    durationInFrames > fadeOutStart
      ? interpolate(frame, [fadeOutStart, durationInFrames], [1, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        })
      : 1;

  const hasBackdrop = Boolean(backdropSrc && backdropSrc.trim().length > 0);
  const isLightMode = tokens.isLightCanvas && !hasBackdrop;
  const textPrimary = hasBackdrop ? "#F7F4EE" : tokens.textPrimary;
  const textSecondary = hasBackdrop ? "rgba(240, 234, 222, 0.80)" : tokens.textSecondary;
  const cardBg = hasBackdrop
    ? "rgba(14, 16, 22, 0.60)"
    : isLightMode
    ? hexWithAlpha(tokens.surface, 0.88)
    : hexWithAlpha(tokens.surface, 0.85);
  const cardBorder = hasBackdrop
    ? hexWithAlpha(tokens.accent, 0.30)
    : hexWithAlpha(tokens.border, 0.88);

  const cols = visibleStats.length <= 2 ? Math.max(1, visibleStats.length) : visibleStats.length === 4 ? 2 : 3;
  const backdropScale = 1.03 + tokens.backdropZoomPct * progress;
  const fallbackBg = isLightMode
    ? `radial-gradient(ellipse at 50% 35%, ${tokens.surface} 0%, ${tokens.bg} 100%)`
    : `radial-gradient(ellipse at 50% 30%, ${hexWithAlpha(tokens.accent, 0.16)} 0%, ${tokens.surface} 50%, ${tokens.bg} 100%)`;

  return (
    <AbsoluteFill
      style={{
        background: fallbackBg,
        overflow: "hidden",
        justifyContent: "space-between",
        padding: "86px 116px 82px",
        boxSizing: "border-box",
      }}
    >
      {/* Optional blurred slow-zooming backdrop */}
      {hasBackdrop && backdropSrc ? (
        <AbsoluteFill style={{ overflow: "hidden" }}>
          <Img
            src={backdropSrc}
            style={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
              transform: `scale(${backdropScale.toFixed(4)})`,
              filter: `blur(${Math.max(16, tokens.backdropBlurPx)}px) saturate(1.04)`,
            }}
          />
          <AbsoluteFill
            style={{
              backgroundColor: hexWithAlpha("#0B0C10", Math.max(0.56, tokens.backdropDimAlpha + 0.12)),
              backgroundImage:
                "radial-gradient(ellipse at 50% 50%, rgba(12,12,16,0.12) 20%, rgba(6,6,10,0.74) 100%)",
            }}
          />
        </AbsoluteFill>
      ) : null}

      {/* Inset editorial frame */}
      <div
        style={{
          position: "absolute",
          inset: 44,
          borderRadius: tokens.cornerRadiusPx,
          border: `1.5px solid ${hexWithAlpha(hasBackdrop ? tokens.accent : tokens.border, hasBackdrop ? 0.32 : 0.72)}`,
          pointerEvents: "none",
        }}
      />

      {/* Top Header */}
      <div
        style={{
          position: "relative",
          zIndex: 2,
          opacity: headerOpacity * fadeOut,
          transform: `translateY(${headerSlide.toFixed(2)}px)`,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-end",
          borderBottom: `1.5px solid ${hexWithAlpha(tokens.accent, 0.32)}`,
          paddingBottom: 24,
        }}
      >
        <div>
          <div
            style={{
              fontFamily: tokens.metaCss,
              fontSize: 18,
              fontWeight: 700,
              letterSpacing: `${tokens.metaLetterSpacingEm}em`,
              textTransform: "uppercase",
              color: tokens.accent,
              marginBottom: 10,
            }}
          >
            {eyebrow}
          </div>
          <div
            style={{
              fontFamily: tokens.displayCss,
              fontSize: 58,
              fontWeight: 700,
              color: textPrimary,
              textTransform: tokens.displayUppercase ? "uppercase" : "none",
              letterSpacing: `${tokens.displayLetterSpacingEm}em`,
              lineHeight: 1.08,
              textShadow: hasBackdrop ? "0 4px 20px rgba(0,0,0,0.55)" : "none",
            }}
          >
            {tripTitle}
          </div>
        </div>
        {dateRange ? (
          <div
            style={{
              fontFamily: "Menlo, 'Courier New', monospace",
              fontSize: 20,
              letterSpacing: "0.08em",
              color: textSecondary,
              paddingBottom: 8,
            }}
          >
            {dateRange}
          </div>
        ) : null}
      </div>

      {/* Animated EXIF Stat Grid */}
      <div
        style={{
          position: "relative",
          zIndex: 2,
          opacity: fadeOut,
          display: "grid",
          gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
          gap: 28,
          margin: "32px 0",
          flex: 1,
          alignContent: "center",
        }}
      >
        {visibleStats.map((stat, idx) => {
          const delayFrames = 10 + idx * 6;
          const cardPop = spring({
            frame: Math.max(0, qFrame - delayFrames),
            fps,
            config: { damping: 16, stiffness: 120 },
          });

          // Counter finishes cleanly before 72% of clip duration so viewers can savor final numbers
          const countDuration = Math.max(24, Math.round(durationInFrames * 0.42));
          const rawCountT = interpolate(frame, [delayFrames + 4, delayFrames + 4 + countDuration], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });
          const countEase = easeOutCubic(rawCountT);

          const parsed = parseNumericStat(stat.value);
          const displayNum = parsed.isNumeric
            ? `${stat.prefix ?? parsed.prefix}${formatAnimatedNumber(parsed.target * countEase, parsed.decimals)}${stat.suffix ?? parsed.suffix}`
            : `${stat.prefix ?? ""}${String(stat.value)}${stat.suffix ?? ""}`;

          const accentTint = idx % 2 === 0 ? tokens.accent : tokens.accentSecondary;

          return (
            <div
              key={`${stat.label}-${idx}`}
              style={{
                opacity: cardPop,
                transform: `translateY(${((1 - cardPop) * 24).toFixed(2)}px) scale(${(0.95 + 0.05 * cardPop).toFixed(4)})`,
                backgroundColor: cardBg,
                border: `1.5px solid ${cardBorder}`,
                borderTop: `4px solid ${accentTint}`,
                borderRadius: Math.max(12, tokens.cornerRadiusPx),
                padding: "30px 34px",
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                minHeight: visibleStats.length <= 3 ? 250 : 210,
                backdropFilter: hasBackdrop ? "blur(10px)" : undefined,
                boxShadow: hasBackdrop ? "0 14px 36px rgba(0,0,0,0.35)" : "0 8px 24px rgba(0,0,0,0.08)",
              }}
            >
              <div
                style={{
                  fontFamily: tokens.metaCss,
                  fontSize: 16,
                  fontWeight: 700,
                  letterSpacing: "0.18em",
                  textTransform: "uppercase",
                  color: textSecondary,
                }}
              >
                {stat.label}
              </div>

              <div
                style={{
                  display: "flex",
                  alignItems: "baseline",
                  gap: 12,
                  marginTop: 14,
                  marginBottom: 10,
                }}
              >
                <span
                  style={{
                    fontFamily: tokens.displayCss,
                    fontSize: visibleStats.length <= 3 ? 76 : 64,
                    fontWeight: 700,
                    color: textPrimary,
                    lineHeight: 1.0,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {displayNum}
                </span>
                {stat.unit ? (
                  <span
                    style={{
                      fontFamily: "Menlo, 'Courier New', monospace",
                      fontSize: 22,
                      fontWeight: 600,
                      color: accentTint,
                      letterSpacing: "0.06em",
                    }}
                  >
                    {stat.unit}
                  </span>
                ) : null}
              </div>

              {stat.detail ? (
                <div
                  style={{
                    fontFamily: tokens.metaCss,
                    fontSize: 17,
                    color: textSecondary,
                    lineHeight: 1.35,
                  }}
                >
                  {stat.detail}
                </div>
              ) : (
                <div
                  style={{
                    width: `${Math.round(56 * countEase)}px`,
                    height: 3,
                    backgroundColor: hexWithAlpha(accentTint, 0.7),
                    borderRadius: 2,
                  }}
                />
              )}
            </div>
          );
        })}
      </div>

      {/* Footer with style palette swatches */}
      <div
        style={{
          position: "relative",
          zIndex: 2,
          opacity: headerOpacity * fadeOut * 0.85,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <div
          style={{
            fontFamily: "Menlo, 'Courier New', monospace",
            fontSize: 15,
            letterSpacing: "0.14em",
            textTransform: "uppercase",
            color: textSecondary,
          }}
        >
          VERIFIED EXIF & GPS TELEMETRY
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          {tokens.swatches.slice(0, 5).map((hex, i) => (
            <div
              key={`${hex}-${i}`}
              style={{
                width: 18,
                height: 18,
                borderRadius: 999,
                backgroundColor: hex,
                border: `1.5px solid ${hexWithAlpha(textPrimary, 0.35)}`,
              }}
            />
          ))}
        </div>
      </div>

      <FilmGrainOverlay
        grainAmount={tokens.grainAmount}
        frame={frame}
        fps={fps}
        overlayFpsStep={tokens.overlayFpsStep}
      />
    </AbsoluteFill>
  );
};
