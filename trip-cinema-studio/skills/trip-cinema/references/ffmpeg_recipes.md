# FFmpeg Recipes for Trip Cinema Studio

All recipes target **16:9 horizontal Rec.709 MP4** (`yuv420p`, `1920x1080` or `3840x2160`) with `-14 LUFS`, `LRA=9` audio mastering.

## 1. HDR-to-SDR Rec.709 Tone-Mapping (iPhone HLG / Dolby Vision / 10-bit Log)
Prevents blown-out highlights and washed-out colors when mixing HDR phone clips with SDR stills:
```bash
zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=mobius:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p
```

## 2. Two-Pass Video Stabilization (`vidstab`)
Use on handheld walking/panning clips (`stabilize: true`):
```bash
# Pass 1: Detect motion vectors
ffmpeg -i input.mov -vf "vidstabdetect=shakiness=5:accuracy=15:result=./work/transforms.trf" -f null -
# Pass 2: Smooth + crop
ffmpeg -i input.mov -vf "vidstabtransform=input=./work/transforms.trf:smoothing=15:optzoom=1:interpol=bicubic,unsharp=5:5:0.8:3:3:0.4" -c:v libx264 -crf 18 out_stab.mp4
```

## 3. Supersampled Ken Burns (`zoompan`) Anchored to `focal_anchor`
Always upscale `2x`–`4x` before `zoompan` to eliminate sub-pixel shimmer on stills:
```bash
# Example: 1080p target at 30fps, 4.0s (120 frames), 3840x2160 supersampled input
ffmpeg -loop 1 -i photo.jpg -vf "scale=3840:-2:flags=lanczos,zoompan=z='min(zoom+0.0012,1.15)':x='iw*0.5-(iw/zoom/2)':y='ih*0.35-(ih/zoom/2)':d=120:s=1920x1080:fps=30,format=yuv420p" -t 4 -c:v libx264 -crf 18 shot_kb.mp4
```
Replace `0.5` and `0.35` with `focal_anchor.x` and `focal_anchor.y` so faces or landmarks stay centered.

## 4. Vertical `9:16` Assets in `16:9`
- **`split_diptych_pair`**: Place two related vertical photos side-by-side (`960x1080` each) with a thin dark divider (`hstack`).
- **`blurred_bg_pillarbox`**:
  ```bash
  [0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,boxblur=25:5,eq=brightness=-0.12[bg];
  [0:v]scale=-2:1080:force_original_aspect_ratio=decrease[fg];
  [bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p
  ```

## 5. Subtle Temporal Film Grain
Prevents banding on gradients and unifies mixed phone/camera footage:
```bash
noise=c0s=8:c0f=t+u
```

## 6. Transitions & J/L Audio Overlaps
- Prefer clean cuts on beat boundaries (`beat_map.json`).
- Use `xfade=transition=fade:duration=0.5:offset=<t>` sparingly between chapters.
- Use `acrossfade=d=0.4:c1=tri:c2=tri` for natural ambience J/L overlaps under BGM.

## 7. Two-Pass `-14 LUFS`, `LRA=9` Mastering
```bash
# Pass 1: Measure
ffmpeg -i mixed.mp4 -af "loudnorm=I=-14:TP=-1.5:LRA=9:print_format=json" -f null -
# Pass 2: Apply linear normalization using measured_I, measured_TP, measured_LRA, measured_thresh, target_offset
ffmpeg -i mixed.mp4 -c:v copy -af "loudnorm=I=-14:TP=-1.5:LRA=9:measured_I=<I>:measured_TP=<TP>:measured_LRA=<LRA>:measured_thresh=<TH>:offset=<OFF>:linear=true" -c:a aac -b:a 192k final_trip.mp4
```

## 8. QA Extraction (`--extract-qa`)
```bash
# End-frame check (-sseof -0.05) + <=60s review chunks
ffmpeg -sseof -0.05 -i final_trip.mp4 -frames:v 1 ./work/qa/end_frame.jpg
ffmpeg -i final_trip.mp4 -c copy -map 0 -segment_time 55 -f segment -reset_timestamps 1 ./work/qa/chunk_%03d.mp4
```
