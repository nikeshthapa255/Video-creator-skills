import React from "react";
import { Composition } from "remotion";
import { TripRouteMap, TripRouteMapProps } from "./TripRouteMap";
import { ChapterTitle, ChapterTitleProps } from "./ChapterTitle";

export const RemotionRoot: React.FC = () => {
  const defaultMapProps: TripRouteMapProps = {
    tripTitle: "Coastal Journey",
    accentColor: "#E8A87C",
    waypoints: [
      { label: "Day 1 · Arrival", lat: 36.6002, lon: -121.8947 },
      { label: "Day 2 · Coastline", lat: 36.2704, lon: -121.8081 },
      { label: "Day 3 · Ridge Overlook", lat: 35.6369, lon: -121.1869 },
    ],
  };

  const defaultTitleProps: ChapterTitleProps = {
    chapterNumber: "01",
    title: "Arrival by the Coast",
    dateLabel: "2026-10-14",
    coordinatesLabel: "36.6002° N, 121.8947° W",
    accentColor: "#E8A87C",
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
    </>
  );
};
