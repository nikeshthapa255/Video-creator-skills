import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";

export interface Waypoint {
  label: string;
  lat: number;
  lon: number;
}

export interface TripRouteMapProps {
  tripTitle: string;
  waypoints: Waypoint[];
  accentColor?: string;
}

/**
 * Pure-SVG animated GPS route map (zero paid map API keys required).
 * Projects lat/lon waypoints into a padded 1920x1080 canvas and animates
 * the route stroke and waypoint markers.
 */
export const TripRouteMap: React.FC<TripRouteMapProps> = ({
  tripTitle,
  waypoints,
  accentColor = "#E8A87C",
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const padX = 260;
  const padY = 210;
  const width = 1920;
  const height = 1080;

  const lats = waypoints.map((w) => w.lat);
  const lons = waypoints.map((w) => w.lon);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const minLon = Math.min(...lons);
  const maxLon = Math.max(...lons);
  const latSpan = Math.max(0.01, maxLat - minLat);
  const lonSpan = Math.max(0.01, maxLon - minLon);

  const projected = waypoints.map((wp) => {
    const x = padX + ((wp.lon - minLon) / lonSpan) * (width - padX * 2);
    const y = height - padY - ((wp.lat - minLat) / latSpan) * (height - padY * 2);
    return { ...wp, x, y };
  });

  const pathD = projected
    .map((pt, idx) => `${idx === 0 ? "M" : "L"} ${pt.x.toFixed(1)} ${pt.y.toFixed(1)}`)
    .join(" ");

  // Approximate total polyline length for stroke-dashoffset animation
  let totalLen = 0;
  for (let i = 1; i < projected.length; i++) {
    const dx = projected[i].x - projected[i - 1].x;
    const dy = projected[i].y - projected[i - 1].y;
    totalLen += Math.hypot(dx, dy);
  }
  const safeLen = Math.max(1, totalLen);

  const drawProgress = interpolate(frame, [15, durationInFrames - 25], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const dashOffset = safeLen * (1 - drawProgress);
  const titleOpacity = interpolate(frame, [0, 18], [0, 1], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: "#0F1115",
        fontFamily: "Inter, -apple-system, BlinkMacSystemFont, sans-serif",
      }}
    >
      {/* Subtle topographic grid */}
      <svg width={width} height={height} style={{ position: "absolute", inset: 0 }}>
        <defs>
          <pattern id="grid" width="80" height="80" patternUnits="userSpaceOnUse">
            <path d="M 80 0 L 0 0 0 80" fill="none" stroke="rgba(255,255,255,0.04)" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width={width} height={height} fill="url(#grid)" />

        {/* Ghost base path */}
        <path
          d={pathD}
          fill="none"
          stroke="rgba(255,255,255,0.14)"
          strokeWidth="4"
          strokeDasharray="8 10"
          strokeLinecap="round"
        />

        {/* Animated active route */}
        <path
          d={pathD}
          fill="none"
          stroke={accentColor}
          strokeWidth="6"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeDasharray={safeLen}
          strokeDashoffset={dashOffset}
        />

        {projected.map((pt, idx) => {
          const threshold = projected.length > 1 ? idx / (projected.length - 1) : 0;
          const appear = spring({
            frame: Math.max(0, frame - Math.round(15 + threshold * (durationInFrames - 45))),
            fps,
            config: { damping: 14, stiffness: 120 },
          });
          return (
            <g key={pt.label} opacity={appear}>
              <circle cx={pt.x} cy={pt.y} r={14 * appear} fill={accentColor} fillOpacity={0.22} />
              <circle cx={pt.x} cy={pt.y} r={6 * appear} fill="#F5F7FA" stroke={accentColor} strokeWidth="3" />
              <text
                x={pt.x + 18}
                y={pt.y + 6}
                fill="#F5F7FA"
                fontSize="24"
                fontWeight="500"
                letterSpacing="0.02em"
              >
                {pt.label}
              </text>
            </g>
          );
        })}
      </svg>

      <div
        style={{
          position: "absolute",
          top: 64,
          left: 88,
          opacity: titleOpacity,
          color: "#F5F7FA",
        }}
      >
        <div style={{ fontSize: 18, letterSpacing: "0.22em", textTransform: "uppercase", color: accentColor }}>
          Route Overview
        </div>
        <div style={{ fontSize: 44, fontWeight: 600, marginTop: 8 }}>{tripTitle}</div>
      </div>
    </AbsoluteFill>
  );
};
