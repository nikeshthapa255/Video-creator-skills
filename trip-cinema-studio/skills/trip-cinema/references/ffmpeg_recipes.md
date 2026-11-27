# FFmpeg recipes for Trip Cinema Studio

All recipes target 16:9 horizontal Rec.709 MP4 (`yuv420p`, `1920x1080` or `3840x2160`) with `-14 LUFS`, `LRA=9` audio mastering.

## 1. HDR-to-SDR Rec.709 conversion (HLG vs. PQ)

Probe `color_transfer` with `ffprobe` before choosing a filter. Smartphone 10-bit HDR clips (iPhone Dolby Vision Profile 8.4, Pixel, Samsung) usually use HLG (`arib-std-b67`), while HDR10 cameras and mastered HDR files use PQ (`smpte2084`).

### 1a. HLG (`arib-std-b67`) smartphone footage

Convert BT.2020 HLG directly to Rec.709 primaries and matrix with mild gamma/brightness shaping:

```bash
zscale=pin=bt2020:min=bt2020nc:p=bt709:m=bt709:r=tv,format=yuv420p,eq=gamma=0.95:brightness=-0.02
```

**Warning:** Never apply PQ Mobius tone-mapping (`t=linear:npl=100,tonemap=mobius`) to `arib-std-b67` HLG clips. Treating scene-referred HLG as 10,000-nit PQ lifts blacks above `YMIN=40` and whitewashes every clip against SDR photos.

### 1b. PQ (`smpte2084`) HDR10 footage

Use explicit `tin=smpte2084` Mobius tone-mapping only when `ffprobe` confirms `color_transfer=smpte2084`:

```bash
zscale=tin=smpte2084:pin=bt2020:min=bt2020nc:t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=mobius:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p
```

## 2. Two-pass video stabilization (`vidstab`)

Run stabilization on handheld walking or panning clips (`stabilize: true`) before speed ramps and layout framing:

```bash
# Pass 1: Detect motion vectors
ffmpeg -i input.mov -vf "vidstabdetect=shakiness=5:accuracy=15:result=./work/transforms.trf" -f null -
# Pass 2: Apply smoothing transform
ffmpeg -i input.mov -vf "vidstabtransform=input=./work/transforms.trf:smoothing=15:zoom=0:optzoom=1,unsharp=5:5:0.8:3:3:0.4" -an -c:v libx264 -crf 18 out_stab.mp4
```

## 3. Supersampled Ken Burns (`zoompan`) anchored to `focal_anchor`

Upscale `2x` before `zoompan` to stop sub-pixel shimmer on stills, and emit video-only intermediates (`-an -frames:v`) with exact frame counts:

```bash
# 1080p at 30fps, 4.0s (120 frames), 3840x2160 supersampled input
ffmpeg -i photo.jpg -vf "scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160:x='max(0,min(iw-ow,iw*0.500-ow/2))':y='max(0,min(ih-oh,ih*0.350-oh/2))',setsar=1,zoompan=z='min(1.15,1.0+0.15*on/120)':x='max(0,min(iw-iw/zoom,(iw-iw/zoom)*0.500))':y='max(0,min(ih-ih/zoom,(ih-ih/zoom)*0.350))':d=120:s=1920x1080:fps=30,format=yuv420p" -an -r 30 -frames:v 120 -c:v libx264 -crf 18 shot_kb.mp4
```

Replace `0.500` and `0.350` with `focal_anchor` (`[x, y]`) so faces and landmarks stay centered.

## 4. Vertical `9:16` assets in `16:9`

- `split_diptych_pair` / `split_2up_video`: place two portrait clips side-by-side (`955x1080` each with gutter) via `hstack`, cropping around each tile's `focal_anchor`.
- `blurred_bg_pillarbox`:
  ```bash
  [0:v]split=2[bg0][fg0];
  [bg0]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,gblur=sigma=28,eq=brightness=-0.16[bg];
  [fg0]scale=-2:1080:force_original_aspect_ratio=decrease[fg];
  [bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p
  ```

## 5. Subtle temporal film grain

Add temporal noise to prevent gradient banding and blend mixed cameras:

```bash
noise=c0s=8:c0f=t+u
```

## 6. Transitions and frame-locked timing

- Render every intermediate `shot_NNN.mp4` video-only (`-an -frames:v <N> -video_track_timescale 90000`) and probe actual clip durations before computing `xfade` offsets.
- Prefer hard cuts on beats (`beat_map.json`). Use `dissolve`, `smoothleft`/`smoothright`/`smoothup`/`smoothdown`, `wipeleft`, or `zoomin` for overlap transitions. Avoid `fadewhite` whiteouts and abrupt 1-frame `boxblur` gates.
- Split long timelines into cut-aligned segments (10 to 15 shots) and join them with `ffmpeg -f concat -safe 0 -i concat_list.txt -c copy`.

## 7. Two-pass `-14 LUFS`, `LRA=9` mastering

```bash
# Pass 1: Measure
ffmpeg -i mixed.mp4 -af "loudnorm=I=-14:TP=-1.0:LRA=9:print_format=json" -f null -
# Pass 2: Apply linear normalization using measured values
ffmpeg -i mixed.mp4 -c:v copy -af "loudnorm=I=-14:TP=-1.0:LRA=9:measured_I=<I>:measured_TP=<TP>:measured_LRA=<LRA>:measured_thresh=<TH>:offset=<OFF>:linear=true" -c:a aac -b:a 256k -movflags +faststart final_trip.mp4
```

## 8. EDL-aware QA extraction and luma telemetry

Run `python3 scripts/render_trip.py --extract-qa ./output/trip_cinema.mp4 --edl ./work/edl.json --work-dir ./work` to extract all QA sheets, transition 3-frame strips, `960x540` collage checks, `<=60s` chunks, and `qa_metrics.json`. Single-frame luma/saturation inspection uses:

```bash
ffmpeg -ss <sec> -i final_trip.mp4 -frames:v 1 -vf "signalstats,metadata=mode=print:file=-" -f null -
```
