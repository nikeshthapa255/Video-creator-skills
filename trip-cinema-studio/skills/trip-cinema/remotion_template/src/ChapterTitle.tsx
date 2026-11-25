import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from "remotion";

export interface ChapterTitleProps {
  chapterNumber: string;
  title: string;
  dateLabel: string;
  coordinatesLabel?: string;
  accentColor?: string;
}

/**
 * Minimalist EXIF date and coordinate chapter title card for 16:9 timelines.
 */
export const ChapterTitle: React.FC<ChapterTitleProps> = ({
  chapterNumber,
  title,
  dateLabel,
  coordinatesLabel,
  accentColor = "#E8A87C",
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  const fadeIn = interpolate(frame, [0, 18], [0, 1], { extrapolateRight: "clamp" });
  const fadeOut = interpolate(frame, [durationInFrames - 18, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const opacity = Math.min(fadeIn, fadeOut);
  const translateY = interpolate(frame, [0, 20], [18, 0], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: "#0D0F13",
        justifyContent: "center",
        alignItems: "center",
        fontFamily: "Inter, -apple-system, BlinkMacSystemFont, sans-serif",
      }}
    >
      <div
        style={{
          opacity,
          transform: `translateY(${translateY}px)`,
          textAlign: "center",
          maxWidth: 1200,
          padding: "0 64px",
        }}
      >
        <div
          style={{
            fontSize: 20,
            letterSpacing: "0.28em",
            textTransform: "uppercase",
            color: accentColor,
            marginBottom: 18,
          }}
        >
          Chapter {chapterNumber}
        </div>
        <div
          style={{
            fontSize: 68,
            fontWeight: 600,
            color: "#F5F7FA",
            letterSpacing: "-0.02em",
            lineHeight: 1.12,
          }}
        >
          {title}
        </div>
        <div
          style={{
            width: 64,
            height: 2,
            backgroundColor: accentColor,
            margin: "28px auto",
            opacity: 0.8,
          }}
        />
        <div
          style={{
            fontSize: 22,
            color: "rgba(245, 247, 250, 0.72)",
            letterSpacing: "0.08em",
          }}
        >
          {dateLabel}
          {coordinatesLabel ? `  ·  ${coordinatesLabel}` : ""}
        </div>
      </div>
    </AbsoluteFill>
  );
};
