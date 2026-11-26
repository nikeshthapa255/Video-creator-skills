import React from "react";
import { Composition } from "remotion";
import { TripRouteMap, TripRouteMapProps } from "./TripRouteMap";
import { ChapterTitle, ChapterTitleProps } from "./ChapterTitle";
import { TripWrappedCard, TripWrappedCardProps } from "./TripWrappedCard";

export const RemotionRoot: React.FC = () => {
  const defaultMapProps: TripRouteMapProps = {
    tripTitle: "Himalayan Ridge Journey",
    eyebrow: "Route & Elevation",
    accentColor: "#D4AF37",
    elevationProfile: true,
    waypoints: [
      { label: "Day 1 · Valley Trailhead", lat: 28.2096, lon: 83.9856, elevation_m: 822 },
      { label: "Day 2 · Cloud Forest Camp", lat: 28.3967, lon: 83.8208, elevation_m: 2310 },
      { label: "Day 3 · Alpine Ridge", lat: 28.4871, lon: 83.8529, elevation_m: 3230 },
      { label: "Day 4 · Sanctuary Overlook", lat: 28.5304, lon: 83.878, elevation_m: 4130 },
    ],
  };

  const defaultTitleProps: ChapterTitleProps = {
    chapterNumber: "01",
    eyebrow: "DAY 01 · COASTAL RIDGE",
    title: "Arrival Above the Clouds",
    dateLabel: "2026-10-14",
    coordinatesLabel: "28.2096° N, 83.9856° E",
    accentColor: "#D4AF37",
  };

  const defaultWrappedProps: TripWrappedCardProps = {
    tripTitle: "Himalayan Ridge Journey",
    eyebrow: "TRIP WRAPPED",
    dateRange: "OCT 14 – OCT 18, 2026",
    accentColor: "#D4AF37",
    stats: [
      { label: "Days on the Trail", value: 5, unit: "days", detail: "4 high-altitude chapters" },
      { label: "Distance Covered", value: 68.4, unit: "km", detail: "Verified GPS waypoints" },
      { label: "Peak Elevation", value: 4130, unit: "m", detail: "Sanctuary Overlook" },
      { label: "Clips & Frames", value: 142, unit: "shots", detail: "80% live motion capture" },
      { label: "Golden Hour Magic", value: 38, unit: "captures", detail: "Sunrise & ridge dusk" },
      { label: "Slow-Mo Ready", value: 29, unit: "clips", detail: "60fps cinematic footage" },
    ],
  };

  return (
    <>
      <Composition
        id="TripRouteMap"
        component={TripRouteMap}
        durationInFrames={150}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={defaultMapProps}
      />
      <Composition
        id="ChapterTitle"
        component={ChapterTitle}
        durationInFrames={90}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={defaultTitleProps}
      />
      <Composition
        id="TripWrappedCard"
        component={TripWrappedCard}
        durationInFrames={150}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={defaultWrappedProps}
      />
    </>
  );
};
