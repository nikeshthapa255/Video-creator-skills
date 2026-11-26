import React from "react";
import { AbsoluteFill, Img, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import {
  FilmGrainOverlay,
  hexWithAlpha,
  resolveStylePack,
  StylePackTokens,
} from "./stylePack";

export interface Waypoint {
  label: string;
  lat: number;
  lon: number;
  /** Optional altitude in meters; when present on waypoints, renders a synced Elevation Profile sparkline. */
  elevation_m?: number;
  subtitle?: string;
}

export interface ElevationProfileConfig {
  enabled?: boolean;
  unit?: "m" | "ft";
  min_m?: number;
  max_m?: number;
  points?: Array<{ label: string; elevation_m: number }>;
}

export type TripRouteMapProps = {
  tripTitle: string;
  eyebrow?: string;
  waypoints: Waypoint[];
  accentColor?: string;
  /** Optional blurred slow-zooming hero photo backdrop. */
  backdropSrc?: string;
  /** Enable/configure synced Elevation Profile sparkline (auto-enabled when waypoints contain elevation_m). */
  elevationProfile?: boolean | ElevationProfileConfig;
  elevationUnit?: "m" | "ft";
  /** Full or partial Style Pack JSON matching `styles/<style_id>.json`. */
  stylePack?: StylePackTokens;
};

interface Pt2D {
  x: number;
  y: number;
}

function easeInOutCubic(t: number): number {
  const c = Math.max(0, Math.min(1, t));
  return c < 0.5 ? 4 * c * c * c : 1 - Math.pow(-2 * c + 2, 3) / 2;
}

function quadBezier(p0: Pt2D, ctrl: Pt2D, p1: Pt2D, t: number): Pt2D {
  const u = 1 - t;
  return {
    x: u * u * p0.x + 2 * u * t * ctrl.x + t * t * p1.x,
    y: u * u * p0.y + 2 * u * t * ctrl.y + t * t * p1.y,
  };
}

function formatElevation(meters: number, unit: "m" | "ft"): string {
  const val = unit === "ft" ? Math.round(meters * 3.28084) : Math.round(meters);
  return `${val.toLocaleString("en-US")} ${unit}`;
}

/**
 * Pure-SVG animated GPS route map (zero paid map API keys required) with stylePack
 * skinning, curved Bezier arcs, optional blurred photo backdrop, and a synced
 * Elevation Profile sparkline beneath the route whenever `elevation_m` is present.
 */
export const TripRouteMap: React.FC<TripRouteMapProps> = ({
  tripTitle,
  eyebrow = "Route Overview",
  waypoints,
  accentColor,
  backdropSrc,
  elevationProfile,
  elevationUnit,
  stylePack,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();
  const tokens = resolveStylePack(stylePack, accentColor);

  const safeWaypoints: Waypoint[] =
    waypoints && waypoints.length > 0
      ? waypoints
      : [{ label: "Start", lat: 0, lon: 0 }];

  const epObj: ElevationProfileConfig | null =
    typeof elevationProfile === "object" && elevationProfile !== null ? elevationProfile : null;
  const epExplicitlyDisabled = elevationProfile === false || epObj?.enabled === false;
  const unit: "m" | "ft" = elevationUnit ?? epObj?.unit ?? "m";

  // Build elevation series either from waypoints[].elevation_m or elevationProfile.points
  const elevationSeries: Array<{ label: string; elevation_m: number; tNorm: number }> = [];
  if (!epExplicitlyDisabled) {
    if (epObj?.points && epObj.points.length >= 2) {
      const n = epObj.points.length;
      epObj.points.forEach((pt, idx) => {
        elevationSeries.push({
          label: pt.label,
          elevation_m: pt.elevation_m,
          tNorm: n > 1 ? idx / (n - 1) : 0,
        });
      });
    } else {
      const wpWithElev = safeWaypoints
        .map((wp, idx) => ({
          label: wp.label,
          elevation_m: wp.elevation_m,
          tNorm: safeWaypoints.length > 1 ? idx / (safeWaypoints.length - 1) : 0,
        }))
        .filter((item): item is { label: string; elevation_m: number; tNorm: number } =>
          typeof item.elevation_m === "number" && Number.isFinite(item.elevation_m)
        );
      if (wpWithElev.length >= 2) {
        elevationSeries.push(...wpWithElev);
      }
    }
  }
  const hasElevation = elevationSeries.length >= 2;

  const progress = durationInFrames > 1 ? frame / (durationInFrames - 1) : 1;
  const titleOpacity = interpolate(frame, [0, 16], [0, 1], { extrapolateRight: "clamp" });
  const fadeOutStart = Math.max(0, durationInFrames - 14);
  const fadeOut =
    durationInFrames > fadeOutStart
      ? interpolate(frame, [fadeOutStart, durationInFrames], [1, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        })
      : 1;

  // Route completes cleanly by progress = 0.82 so all stop pills settle before 0.85
  const routeEndFrame = Math.max(11, Math.round(durationInFrames * 0.82));
  const rawDraw = interpolate(frame, [10, routeEndFrame], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const drawProgress = easeInOutCubic(rawDraw);

  const hasBackdrop = Boolean(backdropSrc && backdropSrc.trim().length > 0);
  const isLightMode = tokens.isLightCanvas && !hasBackdrop;
  const textPrimary = hasBackdrop ? "#F7F4EE" : tokens.textPrimary;
  const textSecondary = hasBackdrop ? "rgba(240, 234, 222, 0.82)" : tokens.textSecondary;
  const gridStroke = isLightMode
    ? hexWithAlpha(tokens.textPrimary, 0.07)
    : hexWithAlpha(tokens.accent, 0.08);
  const surfaceBg = hasBackdrop
    ? "rgba(12, 13, 18, 0.62)"
    : isLightMode
    ? hexWithAlpha(tokens.surface, 0.86)
    : hexWithAlpha(tokens.surface, 0.78);

  // Reserve bottom panel when elevation sparkline is active
  const mapTop = 195;
  const mapBottom = hasElevation ? height - 305 : height - 150;
  const mapPadX = 280;
  const mapSpanW = Math.max(200, width - mapPadX * 2);
  const mapSpanH = Math.max(180, mapBottom - mapTop);

  const lats = safeWaypoints.map((w) => w.lat);
  const lons = safeWaypoints.map((w) => w.lon);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const minLon = Math.min(...lons);
  const maxLon = Math.max(...lons);
  const latSpan = Math.max(0.01, maxLat - minLat);
  const lonSpan = Math.max(0.01, maxLon - minLon);

  const projected = safeWaypoints.map((wp, idx) => {
    const xNorm = safeWaypoints.length === 1 ? 0.5 : (wp.lon - minLon) / lonSpan;
    const yNorm = safeWaypoints.length === 1 ? 0.5 : (wp.lat - minLat) / latSpan;
    return {
      ...wp,
      idx,
      x: mapPadX + xNorm * mapSpanW,
      y: mapBottom - yNorm * mapSpanH,
    };
  });

  // Build curved quadratic Bezier segments for smooth handcrafted look
  const numSegs = Math.max(0, projected.length - 1);
  const fullPathParts: string[] = [`M ${projected[0].x.toFixed(1)} ${projected[0].y.toFixed(1)}`];
  const activePathParts: string[] = [`M ${projected[0].x.toFixed(1)} ${projected[0].y.toFixed(1)}`];
  let headPt: Pt2D = { x: projected[0].x, y: projected[0].y };
  const totalPos = drawProgress * Math.max(1, numSegs);

  for (let i = 0; i < numSegs; i++) {
    const p0 = projected[i];
    const p1 = projected[i + 1];
    const dx = p1.x - p0.x;
    const dy = p1.y - p0.y;
    const sign = i % 2 === 0 ? 1 : -1;
    const ctrl: Pt2D = {
      x: (p0.x + p1.x) * 0.5 - dy * 0.16 * sign,
      y: (p0.y + p1.y) * 0.5 + dx * 0.16 * sign,
    };
    fullPathParts.push(`Q ${ctrl.x.toFixed(1)} ${ctrl.y.toFixed(1)} ${p1.x.toFixed(1)} ${p1.y.toFixed(1)}`);

    const segT = Math.max(0, Math.min(1, totalPos - i));
    if (segT > 0) {
      const steps = 28;
      const maxStep = Math.max(1, Math.round(steps * segT));
      for (let s = 1; s <= maxStep; s++) {
        const t = (s / maxStep) * segT;
        headPt = quadBezier(p0, ctrl, p1, t);
        activePathParts.push(`L ${headPt.x.toFixed(1)} ${headPt.y.toFixed(1)}`);
      }
    }
  }
  const ghostPathD = fullPathParts.join(" ");
  const activePathD = activePathParts.join(" ");

  // Elevation profile geometry (synced to drawProgress)
  const elevBox = {
    x: 112,
    y: height - 252,
    w: width - 224,
    h: 184,
    padLeft: 108,
    padRight: 42,
    padTop: 46,
    padBottom: 42,
  };
  const plotLeft = elevBox.x + elevBox.padLeft;
  const plotRight = elevBox.x + elevBox.w - elevBox.padRight;
  const plotTop = elevBox.y + elevBox.padTop;
  const plotBottom = elevBox.y + elevBox.h - elevBox.padBottom;
  const plotW = Math.max(100, plotRight - plotLeft);
  const plotH = Math.max(40, plotBottom - plotTop);

  let elevMin = 0;
  let elevMax = 100;
  let elevPeak = 0;
  let elevLineD = "";
  let elevAreaD = "";
  let elevPts: Array<{ x: number; y: number; label: string; elevation_m: number; tNorm: number }> = [];
  let headElevPt = { x: plotLeft, y: plotBottom, elevation_m: 0 };

  if (hasElevation) {
    const rawElevs = elevationSeries.map((e) => e.elevation_m);
    const dataMin = Math.min(...rawElevs);
    const dataMax = Math.max(...rawElevs);
    elevPeak = dataMax;
    const pad = Math.max(50, (dataMax - dataMin) * 0.14);
    elevMin = epObj?.min_m ?? Math.max(0, Math.floor((dataMin - pad) / 50) * 50);
    elevMax = epObj?.max_m ?? Math.ceil((dataMax + pad) / 50) * 50;
    const elevSpan = Math.max(1, elevMax - elevMin);

    elevPts = elevationSeries.map((pt) => ({
      ...pt,
      x: plotLeft + pt.tNorm * plotW,
      y: plotBottom - ((pt.elevation_m - elevMin) / elevSpan) * plotH,
    }));

    // Smooth cubic / midpoint quadratic path for sparkline
    const lineParts: string[] = [`M ${elevPts[0].x.toFixed(1)} ${elevPts[0].y.toFixed(1)}`];
    for (let i = 0; i < elevPts.length - 1; i++) {
      const a = elevPts[i];
      const b = elevPts[i + 1];
      const midX = (a.x + b.x) * 0.5;
      lineParts.push(
        `C ${midX.toFixed(1)} ${a.y.toFixed(1)}, ${midX.toFixed(1)} ${b.y.toFixed(1)}, ${b.x.toFixed(1)} ${b.y.toFixed(1)}`
      );
    }
    elevLineD = lineParts.join(" ");
    elevAreaD = `${elevLineD} L ${elevPts[elevPts.length - 1].x.toFixed(1)} ${plotBottom.toFixed(1)} L ${elevPts[0].x.toFixed(1)} ${plotBottom.toFixed(1)} Z`;

    // Interpolate live elevation head along drawProgress
    let segIdx = 0;
    while (segIdx < elevPts.length - 2 && drawProgress > elevPts[segIdx + 1].tNorm) {
      segIdx++;
    }
    const a = elevPts[segIdx];
    const b = elevPts[Math.min(elevPts.length - 1, segIdx + 1)];
    const segSpan = Math.max(0.0001, b.tNorm - a.tNorm);
    const localU = Math.max(0, Math.min(1, (drawProgress - a.tNorm) / segSpan));
    const smoothU = localU * localU * (3 - 2 * localU);
    const curElev = a.elevation_m + (b.elevation_m - a.elevation_m) * smoothU;
    headElevPt = {
      x: plotLeft + drawProgress * plotW,
      y: plotBottom - ((curElev - elevMin) / elevSpan) * plotH,
      elevation_m: curElev,
    };
  }

  const backdropScale = 1.03 + 0.05 * progress;
  const fallbackBg = isLightMode
    ? `radial-gradient(ellipse at 50% 42%, ${tokens.surface} 0%, ${tokens.bg} 100%)`
    : `radial-gradient(ellipse at 50% 35%, ${hexWithAlpha(tokens.accent, 0.12)} 0%, ${tokens.surface} 48%, ${tokens.bg} 100%)`;

  return (
    <AbsoluteFill
      style={{
        background: fallbackBg,
        fontFamily: tokens.metaCss,
        overflow: "hidden",
      }}
    >
      {/* Optional blurred slow-zooming hero backdrop */}
      {hasBackdrop && backdropSrc ? (
        <AbsoluteFill style={{ overflow: "hidden" }}>
          <Img
            src={backdropSrc}
            style={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
              transform: `scale(${backdropScale.toFixed(4)})`,
              filter: `blur(${Math.max(14, tokens.backdropBlurPx)}px) saturate(0.95)`,
            }}
          />
          <AbsoluteFill
            style={{
              backgroundColor: hexWithAlpha("#0C0D12", Math.max(0.58, tokens.backdropDimAlpha + 0.14)),
              backgroundImage:
                "radial-gradient(ellipse at 50% 50%, rgba(12,13,18,0.12) 25%, rgba(8,9,13,0.72) 100%)",
            }}
          />
        </AbsoluteFill>
      ) : null}

      <svg
        width={width}
        height={height}
        style={{ position: "absolute", inset: 0, opacity: fadeOut }}
      >
        <defs>
          <pattern id="trip-topo-grid" width="80" height="80" patternUnits="userSpaceOnUse">
            <path d="M 80 0 L 0 0 0 80" fill="none" stroke={gridStroke} strokeWidth="1" />
          </pattern>
          <linearGradient id="elev-area-grad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={tokens.accent} stopOpacity={0.34} />
            <stop offset="100%" stopColor={tokens.accent} stopOpacity={0.02} />
          </linearGradient>
          <clipPath id="elev-reveal-clip">
            <rect
              x={plotLeft - 6}
              y={plotTop - 24}
              width={Math.max(0, drawProgress * plotW + 12)}
              height={plotH + 36}
            />
          </clipPath>
        </defs>

        {/* Subtle topographic grid */}
        <rect width={width} height={height} fill="url(#trip-topo-grid)" />

        {/* Inset editorial frame */}
        <rect
          x={44}
          y={44}
          width={width - 88}
          height={height - 88}
          rx={tokens.cornerRadiusPx}
          fill="none"
          stroke={hexWithAlpha(hasBackdrop ? tokens.accent : tokens.border, hasBackdrop ? 0.3 : 0.65)}
          strokeWidth="1.5"
        />

        {/* Subtle concentric radar rings around stops */}
        {projected.map((pt) => (
          <g key={`rings-${pt.idx}`}>
            {[24, 44, 64].map((r, rIdx) => (
              <circle
                key={r}
                cx={pt.x}
                cy={pt.y}
                r={r}
                fill="none"
                stroke={hexWithAlpha(tokens.accent, 0.14 - rIdx * 0.035)}
                strokeWidth="1"
              />
            ))}
          </g>
        ))}

        {/* Ghost base route */}
        <path
          d={ghostPathD}
          fill="none"
          stroke={hexWithAlpha(textPrimary, 0.16)}
          strokeWidth="3.5"
          strokeDasharray="8 10"
          strokeLinecap="round"
        />

        {/* Glow halo + animated active route */}
        <path
          d={activePathD}
          fill="none"
          stroke={hexWithAlpha(tokens.accent, 0.28)}
          strokeWidth="12"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path
          d={activePathD}
          fill="none"
          stroke={tokens.accent}
          strokeWidth="4.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        {/* Moving travel marker head */}
        <circle cx={headPt.x} cy={headPt.y} r={14} fill={hexWithAlpha(tokens.accent, 0.34)} />
        <circle cx={headPt.x} cy={headPt.y} r={6.5} fill={textPrimary} stroke={tokens.accent} strokeWidth="2.5" />

        {/* Stop markers & crisp labels (all reveal cleanly before progress = 0.85) */}
        {projected.map((pt, idx) => {
          const threshold = projected.length > 1 ? idx / (projected.length - 1) : 0;
          const triggerFrame = Math.round(10 + threshold * (routeEndFrame - 14));
          const appear = spring({
            frame: Math.max(0, frame - triggerFrame),
            fps,
            config: { damping: 14, stiffness: 135 },
          });
          const labelRight = pt.x < width - 380;
          const labelX = labelRight ? pt.x + 20 : pt.x - 20;
          const anchor = labelRight ? "start" : "end";
          const elevTag =
            typeof pt.elevation_m === "number" ? formatElevation(pt.elevation_m, unit) : pt.subtitle;

          return (
            <g key={`${pt.label}-${idx}`} opacity={appear}>
              <circle cx={pt.x} cy={pt.y} r={13 * appear} fill={hexWithAlpha(tokens.accent, 0.22)} />
              <circle
                cx={pt.x}
                cy={pt.y}
                r={6 * appear}
                fill={textPrimary}
                stroke={tokens.accent}
                strokeWidth="3"
              />
              <text
                x={labelX}
                y={pt.y + 4}
                textAnchor={anchor}
                fill={textPrimary}
                fontFamily={tokens.metaCss}
                fontSize="23"
                fontWeight="600"
                letterSpacing="0.02em"
              >
                {pt.label}
              </text>
              {elevTag ? (
                <text
                  x={labelX}
                  y={pt.y + 28}
                  textAnchor={anchor}
                  fill={tokens.accent}
                  fontFamily="Menlo, 'Courier New', monospace"
                  fontSize="16"
                  fontWeight="500"
                >
                  {elevTag}
                </text>
              ) : null}
            </g>
          );
        })}

        {/* Synced Elevation Profile Sparkline beneath the route */}
        {hasElevation ? (
          <g opacity={titleOpacity}>
            {/* Panel backdrop */}
            <rect
              x={elevBox.x}
              y={elevBox.y}
              width={elevBox.w}
              height={elevBox.h}
              rx={Math.max(10, tokens.cornerRadiusPx)}
              fill={surfaceBg}
              stroke={hexWithAlpha(tokens.accent, 0.28)}
              strokeWidth="1.25"
            />

            {/* Panel title & peak readout */}
            <text
              x={elevBox.x + 26}
              y={elevBox.y + 28}
              fill={tokens.accent}
              fontFamily={tokens.metaCss}
              fontSize="14"
              fontWeight="700"
              letterSpacing="0.2em"
            >
              ELEVATION PROFILE
            </text>
            <text
              x={elevBox.x + elevBox.w - 26}
              y={elevBox.y + 28}
              textAnchor="end"
              fill={textSecondary}
              fontFamily="Menlo, 'Courier New', monospace"
              fontSize="15"
              fontWeight="600"
            >
              {`PEAK ${formatElevation(elevPeak, unit)}  ·  NOW ${formatElevation(headElevPt.elevation_m, unit)}`}
            </text>

            {/* Horizontal dashed altitude gridlines & Y-axis ticks */}
            {[0, 0.5, 1].map((frac) => {
              const gy = plotBottom - frac * plotH;
              const tickElev = elevMin + frac * (elevMax - elevMin);
              return (
                <g key={`elev-grid-${frac}`}>
                  <line
                    x1={plotLeft}
                    y1={gy}
                    x2={plotRight}
                    y2={gy}
                    stroke={hexWithAlpha(textPrimary, 0.10)}
                    strokeDasharray="4 6"
                    strokeWidth="1"
                  />
                  <text
                    x={plotLeft - 14}
                    y={gy + 4}
                    textAnchor="end"
                    fill={textSecondary}
                    fontFamily="Menlo, 'Courier New', monospace"
                    fontSize="13"
                  >
                    {formatElevation(tickElev, unit)}
                  </text>
                </g>
              );
            })}

            {/* Ghost full elevation curve */}
            <path
              d={elevLineD}
              fill="none"
              stroke={hexWithAlpha(textPrimary, 0.16)}
              strokeWidth="2"
              strokeDasharray="5 6"
            />

            {/* Progress-synced revealed area + stroke */}
            <g clipPath="url(#elev-reveal-clip)">
              <path d={elevAreaD} fill="url(#elev-area-grad)" />
              <path
                d={elevLineD}
                fill="none"
                stroke={tokens.accent}
                strokeWidth="3.5"
                strokeLinecap="round"
              />
            </g>

            {/* Waypoint ticks & labels along elevation X-axis */}
            {elevPts.map((pt, idx) => {
              const reached = drawProgress + 0.02 >= pt.tNorm;
              const anchor = idx === 0 ? "start" : idx === elevPts.length - 1 ? "end" : "middle";
              return (
                <g key={`ep-${idx}`} opacity={reached ? 0.95 : 0.35}>
                  <line
                    x1={pt.x}
                    y1={pt.y}
                    x2={pt.x}
                    y2={plotBottom}
                    stroke={hexWithAlpha(tokens.accent, 0.26)}
                    strokeWidth="1"
                  />
                  <circle
                    cx={pt.x}
                    cy={pt.y}
                    r={reached ? 4.5 : 3}
                    fill={reached ? textPrimary : hexWithAlpha(textPrimary, 0.4)}
                    stroke={tokens.accent}
                    strokeWidth="2"
                  />
                  <text
                    x={pt.x}
                    y={plotBottom + 24}
                    textAnchor={anchor}
                    fill={textSecondary}
                    fontFamily={tokens.metaCss}
                    fontSize="14"
                    fontWeight="500"
                  >
                    {pt.label}
                  </text>
                </g>
              );
            })}

            {/* Synced live elevation head dot */}
            <circle
              cx={headElevPt.x}
              cy={headElevPt.y}
              r={10}
              fill={hexWithAlpha(tokens.accent, 0.32)}
            />
            <circle
              cx={headElevPt.x}
              cy={headElevPt.y}
              r={5}
              fill={textPrimary}
              stroke={tokens.accent}
              strokeWidth="2.5"
            />
          </g>
        ) : null}
      </svg>

      {/* Header */}
      <div
        style={{
          position: "absolute",
          top: 66,
          left: 88,
          opacity: titleOpacity * fadeOut,
          color: textPrimary,
        }}
      >
        <div
          style={{
            fontFamily: tokens.metaCss,
            fontSize: 17,
            fontWeight: 600,
            letterSpacing: `${tokens.metaLetterSpacingEm}em`,
            textTransform: "uppercase",
            color: tokens.accent,
          }}
        >
          {eyebrow}
        </div>
        <div
          style={{
            fontFamily: tokens.displayCss,
            fontSize: 46,
            fontWeight: 600,
            marginTop: 6,
            letterSpacing: `${tokens.displayLetterSpacingEm}em`,
            textShadow: hasBackdrop ? "0 4px 18px rgba(0,0,0,0.6)" : "none",
          }}
        >
          {tripTitle}
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
