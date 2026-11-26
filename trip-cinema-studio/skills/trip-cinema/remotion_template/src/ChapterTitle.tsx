import React from "react";
import { AbsoluteFill, Img, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import {
  FilmGrainOverlay,
  hexWithAlpha,
  quantizeFrame,
  resolveStylePack,
  StylePackTokens,
  toRoman,
} from "./stylePack";

export type ChapterTitleProps = {
  chapterNumber: string;
  title: string;
  dateLabel: string;
  coordinatesLabel?: string;
  eyebrow?: string;
  subtitle?: string;
  /** Chapter hero photo URL, staticFile() path, or data URI for blurred slow-zooming backdrop. */
  backdropSrc?: string;
  /** Override backdrop blur radius in pixels (defaults to stylePack.cards.backdrop_blur_sigma). */
  backdropBlurPx?: number;
  accentColor?: string;
  /** Full or partial Style Pack JSON matching `styles/<style_id>.json`. */
  stylePack?: StylePackTokens;
};

function easeOutCubic(t: number): number {
  const c = Math.max(0, Math.min(1, t));
  return 1 - Math.pow(1 - c, 3);
}

/**
 * Style-aware editorial chapter title card with blurred slow-zooming photo backdrops,
 * vignette scrims, and full `styles/<style_id>.json` token support.
 */
export const ChapterTitle: React.FC<ChapterTitleProps> = ({
  chapterNumber,
  title,
  dateLabel,
  coordinatesLabel,
  eyebrow,
  subtitle,
  backdropSrc,
  backdropBlurPx,
  accentColor,
  stylePack,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, height } = useVideoConfig();
  const tokens = resolveStylePack(stylePack, accentColor);

  const progress = durationInFrames > 1 ? frame / (durationInFrames - 1) : 1;
  const qFrame = quantizeFrame(frame, fps, tokens.overlayFpsStep);
  const qProgress = durationInFrames > 1 ? qFrame / (durationInFrames - 1) : 1;

  const fadeIn = interpolate(qFrame, [0, 16], [0, 1], { extrapolateRight: "clamp" });
  const fadeOutStart = Math.max(0, durationInFrames - 16);
  const fadeOut =
    durationInFrames > fadeOutStart
      ? interpolate(frame, [fadeOutStart, durationInFrames], [1, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        })
      : 1;
  const opacity = Math.min(fadeIn, fadeOut);

  const eyeEase = easeOutCubic(Math.min(1, qProgress / 0.22));
  const titleEase = easeOutCubic(Math.min(1, qProgress / 0.30));
  const dividerEase = easeOutCubic(Math.max(0, (qProgress - 0.08) / 0.28));
  const metaEase = easeOutCubic(Math.max(0, (qProgress - 0.14) / 0.28));

  // Smooth slow-zoom on photo backdrop (always full fps for cinematic fluidity)
  const backdropScale = 1.04 + tokens.backdropZoomPct * progress;
  const blurPx = backdropBlurPx ?? tokens.backdropBlurPx;

  // Optional subtle analog gate weave (e.g. Super 8 / Analog Scrapbook)
  const weaveX = tokens.gateWeavePx > 0 ? Math.sin(qFrame * 1.7) * tokens.gateWeavePx : 0;
  const weaveY = tokens.gateWeavePx > 0 ? Math.cos(qFrame * 2.3) * tokens.gateWeavePx * 0.6 : 0;

  const hasBackdrop = Boolean(backdropSrc && backdropSrc.trim().length > 0);
  const isLightMode = tokens.isLightCanvas && !hasBackdrop;

  // Ensure crisp readable typography over both dark scrim photo backdrops and light paper styles
  const primaryTextColor = hasBackdrop ? "#F7F4EE" : tokens.textPrimary;
  const secondaryTextColor = hasBackdrop ? "rgba(242, 236, 226, 0.86)" : tokens.textSecondary;
  const eyebrowColor = tokens.accent;

  const chapterDisplay = tokens.useRomanNumerals ? toRoman(chapterNumber) : chapterNumber;
  const resolvedEyebrow = eyebrow ?? `Chapter ${chapterDisplay}`;
  const metaParts = [subtitle, dateLabel, coordinatesLabel].filter(Boolean);
  const metaText = metaParts.join("  ·  ");

  const isPostcard = tokens.chapterCardVariant === "pastel_futura_roman";
  const isKraft = tokens.chapterCardVariant === "kraft_stamp_typewriter";
  const isSuper8 = tokens.chapterCardVariant === "vintage_super8_date";
  const isKinetic = tokens.chapterCardVariant === "bold_condensed_kinetic";
  const isSitcom = tokens.chapterCardVariant === "bright_pop_sitcom";
  const isSwiss = tokens.chapterCardVariant === "swiss_linen_minimal";

  // Optional 2.39:1 letterbox bars when requested by style (e.g. kinetic_montage)
  const letterboxBarPx =
    tokens.letterboxRatio > 1.85 ? Math.max(0, Math.round((height - 1920 / tokens.letterboxRatio) / 2)) : 0;

  // Rich atmospheric gradient when no photo backdrop is passed (never flat #0D0F13)
  const fallbackBackground = isLightMode
    ? `radial-gradient(ellipse at 50% 42%, ${tokens.surface} 0%, ${tokens.bg} 72%, ${hexWithAlpha(tokens.border, 0.92)} 100%)`
    : `radial-gradient(ellipse at 50% 38%, ${hexWithAlpha(tokens.accent, 0.16)} 0%, ${tokens.surface} 46%, ${tokens.bg} 100%)`;

  const scrimAlpha = Math.max(0.38, Math.min(0.76, tokens.backdropDimAlpha + 0.10));
  const vignetteEdgeAlpha = Math.min(0.82, 0.28 + tokens.vignetteIntensity * 0.55);

  return (
    <AbsoluteFill
      style={{
        background: fallbackBackground,
        justifyContent: "center",
        alignItems: "center",
        overflow: "hidden",
      }}
    >
      {/* Blurred slow-zooming chapter hero backdrop */}
      {hasBackdrop && backdropSrc ? (
        <AbsoluteFill style={{ overflow: "hidden" }}>
          <Img
            src={backdropSrc}
            style={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
              transform: `scale(${backdropScale.toFixed(4)})`,
              filter: `blur(${blurPx}px) saturate(1.06)`,
            }}
          />
          {/* Editorial readability scrim + vignette */}
          <AbsoluteFill
            style={{
              backgroundColor: hexWithAlpha("#0C0B0E", scrimAlpha),
              backgroundImage: `radial-gradient(ellipse at 50% 50%, rgba(12,11,14,0.08) 22%, rgba(8,8,10,${vignetteEdgeAlpha.toFixed(3)}) 100%)`,
            }}
          />
        </AbsoluteFill>
      ) : (
        <AbsoluteFill
          style={{
            backgroundImage: isLightMode
              ? `radial-gradient(ellipse at 50% 50%, rgba(255,255,255,0) 45%, ${hexWithAlpha(tokens.textPrimary, 0.10)} 100%)`
              : `radial-gradient(ellipse at 50% 50%, rgba(0,0,0,0) 35%, rgba(0,0,0,${(vignetteEdgeAlpha * 0.7).toFixed(3)}) 100%)`,
          }}
        />
      )}

      {/* Style-aware inset border / matte framing */}
      {isSuper8 ? (
        <div
          style={{
            position: "absolute",
            inset: 38,
            borderRadius: 42,
            border: `2.5px solid ${hexWithAlpha(tokens.accent, 0.36)}`,
            boxShadow: `inset 0 0 80px ${hexWithAlpha("#000000", 0.55)}`,
            pointerEvents: "none",
          }}
        />
      ) : isPostcard ? (
        <>
          <div
            style={{
              position: "absolute",
              inset: 42,
              border: `2px solid ${hexWithAlpha(tokens.accent, 0.55)}`,
              pointerEvents: "none",
            }}
          />
          <div
            style={{
              position: "absolute",
              inset: 52,
              border: `1px solid ${hexWithAlpha(hasBackdrop ? "#FFFFFF" : tokens.textPrimary, 0.22)}`,
              pointerEvents: "none",
            }}
          />
        </>
      ) : (
        <div
          style={{
            position: "absolute",
            inset: isSwiss ? 56 : 48,
            borderRadius: tokens.cornerRadiusPx,
            border: `${isKraft ? 2 : 1.5}px ${isKraft ? "dashed" : "solid"} ${hexWithAlpha(
              hasBackdrop ? tokens.accent : tokens.border,
              hasBackdrop ? 0.34 : 0.72
            )}`,
            pointerEvents: "none",
          }}
        />
      )}

      {/* Main typography container */}
      <div
        style={{
          opacity,
          transform: `translate(${weaveX.toFixed(2)}px, ${(weaveY + (1 - titleEase) * 18).toFixed(2)}px)`,
          textAlign: "center",
          maxWidth: 1360,
          padding: isKraft || isPostcard ? "52px 84px" : "0 76px",
          borderRadius: isKraft ? 6 : isSitcom ? 20 : 0,
          backgroundColor:
            isKraft && hasBackdrop
              ? hexWithAlpha(tokens.bg, 0.14)
              : isPostcard && !hasBackdrop
              ? hexWithAlpha(tokens.surface, 0.42)
              : "transparent",
          backdropFilter: isKraft && hasBackdrop ? "blur(4px)" : undefined,
        }}
      >
        {/* Eyebrow */}
        <div
          style={{
            opacity: eyeEase,
            transform: `translateY(${((1 - eyeEase) * 12).toFixed(2)}px)`,
            fontFamily: tokens.metaCss,
            fontSize: isSitcom ? 22 : 20,
            fontWeight: isSitcom || isKinetic ? 700 : 600,
            letterSpacing: `${tokens.metaLetterSpacingEm}em`,
            textTransform: tokens.metaUppercase ? "uppercase" : "none",
            color: isSitcom ? "#16181D" : eyebrowColor,
            backgroundColor: isSitcom ? tokens.accent : "transparent",
            display: isSitcom ? "inline-block" : "block",
            padding: isSitcom ? "8px 24px" : 0,
            borderRadius: isSitcom ? 999 : 0,
            marginBottom: 22,
            textShadow: hasBackdrop && !isSitcom ? "0 2px 14px rgba(0,0,0,0.65)" : "none",
          }}
        >
          {resolvedEyebrow}
        </div>

        {/* Display Title */}
        <div
          style={{
            fontFamily: tokens.displayCss,
            fontSize: isKinetic ? 92 : isPostcard ? 76 : 78,
            fontWeight: isKinetic || isPostcard || isSitcom ? 800 : 600,
            color: primaryTextColor,
            textTransform: tokens.displayUppercase ? "uppercase" : "none",
            letterSpacing: `${tokens.displayLetterSpacingEm}em`,
            lineHeight: 1.08,
            textShadow: hasBackdrop ? "0 6px 28px rgba(0,0,0,0.65)" : "none",
          }}
        >
          {title}
        </div>

        {/* Symmetrically expanding accent divider hairline */}
        {tokens.showDividerRule ? (
          <div
            style={{
              width: `${Math.round(220 * dividerEase)}px`,
              height: isKinetic || isSitcom ? 4 : 2,
              backgroundColor: tokens.accent,
              margin: "30px auto",
              opacity: 0.9 * dividerEase,
              borderRadius: 2,
            }}
          />
        ) : (
          <div style={{ height: 26 }} />
        )}

        {/* Date / Subtitle / Coordinates */}
        {metaText ? (
          <div
            style={{
              opacity: metaEase,
              fontFamily: tokens.metaCss,
              fontSize: 23,
              fontWeight: 500,
              color: secondaryTextColor,
              letterSpacing: "0.09em",
              textShadow: hasBackdrop ? "0 2px 14px rgba(0,0,0,0.65)" : "none",
            }}
          >
            {metaText}
          </div>
        ) : null}
      </div>

      {/* Optional retro corner date stamp (Super 8 / Analog Scrapbook / Wes Postcard) */}
      {tokens.showDateStamp && dateLabel ? (
        <div
          style={{
            position: "absolute",
            bottom: letterboxBarPx + 66,
            right: 84,
            opacity: opacity * 0.88,
            fontFamily: "Menlo, 'Courier New', monospace",
            fontSize: 20,
            letterSpacing: "0.14em",
            color: isSuper8 ? "#FF9F43" : hasBackdrop ? tokens.accent : tokens.textSecondary,
            textShadow: isSuper8 ? "0 0 10px rgba(255, 130, 30, 0.75)" : "none",
          }}
        >
          {dateLabel}
        </div>
      ) : null}

      {/* Optional 2.39:1 cinema letterbox bars */}
      {letterboxBarPx > 0 ? (
        <>
          <div
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              right: 0,
              height: letterboxBarPx,
              backgroundColor: "#050608",
            }}
          />
          <div
            style={{
              position: "absolute",
              bottom: 0,
              left: 0,
              right: 0,
              height: letterboxBarPx,
              backgroundColor: "#050608",
            }}
          />
        </>
      ) : null}

      <FilmGrainOverlay
        grainAmount={tokens.grainAmount}
        frame={frame}
        fps={fps}
        overlayFpsStep={tokens.overlayFpsStep}
      />
    </AbsoluteFill>
  );
};
