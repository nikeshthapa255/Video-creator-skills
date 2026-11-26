#!/usr/bin/env ruby
# frozen_string_literal: true

# Zero-third-party Chrome WebCodecs VP9/AV1 Frame Extractor & Hardware H.264 Proxy Transcoder.
# Uses only Apple built-in /usr/bin/ruby, WEBrick, /usr/bin/osascript, and an installed
# Chromium/Chrome browser (Google Chrome / Chromium / Edge / Brave).
#
# Modes:
#   1. Extract tone-mapped, upright JPEG frames (single file or batched JSON):
#      macos_chrome_vp9.rb --mode frames --input clip.mp4 --times 0.5,2.0 --out-dir ./frames [--max-dim 450]
#      macos_chrome_vp9.rb --mode frames --batch-json spec.json
#
#   2. Transcode VP9/AV1 MP4 to hardware H.264 Rec.709 MP4 preserving native FPS & AAC audio:
#      macos_chrome_vp9.rb --mode transcode --input clip.mp4 --output clip_h264.mp4 [--max-dim 1920] [--bitrate 12000000]
#      macos_chrome_vp9.rb --mode transcode --batch-json spec.json

require "optparse"
require "json"
require "webrick"
require "fileutils"
require "tmpdir"
require "open3"
require "thread"
require "shellwords"

CHROME_CANDIDATES = [
  ENV["CHROME_BIN"],
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
  "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
].compact.freeze

def find_chrome_binary(override = nil)
  if override && !override.empty?
    return override if File.executable?(override)
    raise "Specified --chrome-bin is not executable: #{override}"
  end
  CHROME_CANDIDATES.each do |path|
    return path if File.executable?(path)
  end
  raise "No supported Chrome/Chromium/Edge browser found in /Applications. Pass --chrome-bin <path>."
end

def parse_cli(argv)
  opts = {
    mode: nil,
    input: nil,
    output: nil,
    times: nil,
    out_dir: nil,
    batch_json: nil,
    max_dim: nil,
    bitrate: 12_000_000,
    quality: 0.88,
    chrome_bin: nil,
    timeout: 180,
    dry_run: false,
    help: false,
  }

  parser = OptionParser.new do |o|
    o.banner = "Usage: macos_chrome_vp9.rb --mode frames|transcode [options]"
    o.on("--mode MODE", "frames or transcode") { |v| opts[:mode] = v }
    o.on("--input PATH", "Single input video path") { |v| opts[:input] = v }
    o.on("--output PATH", "Output H.264 MP4 path (transcode mode)") { |v| opts[:output] = v }
    o.on("--times CSV", "Comma-separated timestamps in seconds (frames mode)") { |v| opts[:times] = v }
    o.on("--out-dir DIR", "Output directory for extracted JPEG frames") { |v| opts[:out_dir] = v }
    o.on("--batch-json PATH", "JSON batch specification path") { |v| opts[:batch_json] = v }
    o.on("--max-dim N", Integer, "Max pixel dimension (default 450 frames, 1920 transcode)") { |v| opts[:max_dim] = v }
    o.on("--bitrate BPS", Integer, "Target video bitrate in bps (default 12000000)") { |v| opts[:bitrate] = v }
    o.on("--quality Q", Float, "JPEG quality 0.1..1.0 (default 0.88)") { |v| opts[:quality] = v }
    o.on("--chrome-bin PATH", "Explicit Chrome/Chromium executable path") { |v| opts[:chrome_bin] = v }
    o.on("--timeout SEC", Integer, "Inactivity watchdog timeout in seconds (default 180)") { |v| opts[:timeout] = v }
    o.on("--dry-run", "Validate inputs and print planned jobs without launching Chrome") { opts[:dry_run] = true }
    o.on("-h", "--help", "Print JSON usage") { opts[:help] = true }
  end
  parser.parse!(argv)
  opts
end

def normalize_jobs(opts)
  mode = opts[:mode]
  raise "Missing or invalid --mode (expected 'frames' or 'transcode')." unless %w[frames transcode].include?(mode)

  default_max_dim = opts[:max_dim] || (mode == "frames" ? 450 : 1920)
  default_bitrate = opts[:bitrate] || 12_000_000
  default_quality = opts[:quality] || 0.88

  raw_jobs = []
  if opts[:batch_json]
    spec = JSON.parse(File.read(opts[:batch_json]))
    if spec.is_a?(Array)
      raw_jobs = spec
    elsif spec.is_a?(Hash)
      default_max_dim = opts[:max_dim] || spec["max_dim"] || default_max_dim
      default_bitrate = spec["bitrate"] || default_bitrate
      default_quality = spec["quality"] || default_quality
      raw_jobs = spec["jobs"] || []
    else
      raise "Invalid --batch-json format."
    end
  else
    raise "Provide --input <path> or --batch-json <spec.json>." unless opts[:input]
    if mode == "frames"
      times = (opts[:times] || "0.5").split(",").map { |t| Float(t.strip) }
      out_dir = opts[:out_dir] || "./frames"
      raw_jobs = [{
        "id" => File.basename(opts[:input], ".*"),
        "input" => opts[:input],
        "times" => times,
        "out_dir" => out_dir,
        "max_dim" => default_max_dim,
        "quality" => default_quality,
      }]
    else
      raise "Transcode mode requires --output <out.mp4> (or --batch-json)." unless opts[:output]
      raw_jobs = [{
        "id" => File.basename(opts[:input], ".*"),
        "input" => opts[:input],
        "output" => opts[:output],
        "max_dim" => default_max_dim,
        "bitrate" => default_bitrate,
      }]
    end
  end

  jobs = raw_jobs.each_with_index.map do |j, idx|
    in_path = File.expand_path(j["input"].to_s)
    raise "Input video not found for job #{idx}: #{in_path}" unless File.file?(in_path)
    job = {
      "job_index" => idx,
      "id" => (j["id"] || "job_#{idx}").to_s,
      "input" => in_path,
      "max_dim" => (j["max_dim"] || default_max_dim).to_i,
    }
    if mode == "frames"
      times = (j["times"] || [0.5]).map(&:to_f)
      out_paths = j["out_paths"]
      if out_paths && !out_paths.empty?
        out_paths = out_paths.map { |p| File.expand_path(p.to_s) }
      else
        out_dir = File.expand_path((j["out_dir"] || opts[:out_dir] || "./frames").to_s)
        out_paths = times.each_with_index.map do |_t, ti|
          File.join(out_dir, format("frame_%03d.jpg", ti))
        end
      end
      raise "Job #{idx}: times.length must match out_paths.length" unless times.length == out_paths.length
      job["times"] = times
      job["out_paths"] = out_paths
      job["quality"] = (j["quality"] || default_quality).to_f
    else
      out_path = File.expand_path(j["output"].to_s)
      raise "Job #{idx}: missing output path" if out_path.empty?
      job["output"] = out_path
      job["bitrate"] = (j["bitrate"] || default_bitrate).to_i
    end
    job
  end

  jobs
end

# Mux transcoded video-only H.264 MP4 with the original input's AAC audio track using Apple AVFoundation passthrough.
def mux_original_audio_jxa(video_only_mp4, original_input_mp4, final_output_mp4)
  FileUtils.mkdir_p(File.dirname(final_output_mp4))
  jxa = <<~'JXA'
    ObjC.import("Foundation");
    ObjC.import("AVFoundation");
    ObjC.import("CoreMedia");

    function run(argv) {
      const vidPath = argv[0];
      const origPath = argv[1];
      const outPath = argv[2];
      const fm = $.NSFileManager.defaultManager;
      const AVURLAsset = $.NSClassFromString("AVURLAsset");
      const AVMutableComposition = $.NSClassFromString("AVMutableComposition");
      const AVAssetExportSession = $.NSClassFromString("AVAssetExportSession");

      const vAsset = AVURLAsset.URLAssetWithURLOptions($.NSURL.fileURLWithPath(vidPath), $({}));
      const oAsset = AVURLAsset.URLAssetWithURLOptions($.NSURL.fileURLWithPath(origPath), $({}));
      const vTracks = vAsset.tracksWithMediaType($.AVMediaTypeVideo);
      if (!vTracks || Number(vTracks.count) === 0) {
        throw new Error("Transcoded video-only MP4 has no video track: " + vidPath);
      }
      const aTracks = oAsset.tracksWithMediaType($.AVMediaTypeAudio);
      const hasAudio = !!(aTracks && Number(aTracks.count) > 0);

      if (!hasAudio) {
        if (fm.fileExistsAtPath(outPath)) fm.removeItemAtPathError(outPath, null);
        fm.moveItemAtPathToPathError(vidPath, outPath, null);
        return JSON.stringify({ status: "ok", audio_muxed: false, output: outPath });
      }

      const comp = AVMutableComposition.composition;
      const vt = vTracks.objectAtIndex(0);
      const at = aTracks.objectAtIndex(0);
      const vDur = vAsset.duration;
      const aDur = oAsset.duration;

      const cV = comp.addMutableTrackWithMediaTypePreferredTrackID($.AVMediaTypeVideo, 0);
      cV.insertTimeRangeOfTrackAtTimeError($.CMTimeRangeMake($.kCMTimeZero, vDur), vt, $.kCMTimeZero, null);
      cV.preferredTransform = $.CGAffineTransformIdentity;

      const vSec = $.CMTimeGetSeconds(vDur);
      const aSec = $.CMTimeGetSeconds(aDur);
      const muxAudioDur = (isFinite(aSec) && aSec > 0 && aSec < vSec) ? aDur : vDur;
      const cA = comp.addMutableTrackWithMediaTypePreferredTrackID($.AVMediaTypeAudio, 0);
      cA.insertTimeRangeOfTrackAtTimeError($.CMTimeRangeMake($.kCMTimeZero, muxAudioDur), at, $.kCMTimeZero, null);

      const tmpFinal = outPath + ".muxing.mp4";
      if (fm.fileExistsAtPath(tmpFinal)) fm.removeItemAtPathError(tmpFinal, null);
      const exp = AVAssetExportSession.exportSessionWithAssetPresetName(comp, $.AVAssetExportPresetPassthrough);
      if (!exp || exp.isNil()) throw new Error("Failed to create AVAssetExportSession passthrough.");
      exp.outputURL = $.NSURL.fileURLWithPath(tmpFinal);
      exp.outputFileType = $.AVFileTypeMPEG4;
      exp.shouldOptimizeForNetworkUse = true;
      exp.exportAsynchronouslyWithCompletionHandler(() => {});
      while (Number(exp.status) === 1 || Number(exp.status) === 2) {
        $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(0.01));
      }
      if (Number(exp.status) !== 3) {
        const errMsg = exp.error ? ObjC.unwrap(exp.error.localizedDescription) : "unknown";
        if (fm.fileExistsAtPath(tmpFinal)) fm.removeItemAtPathError(tmpFinal, null);
        throw new Error("Audio passthrough mux failed (status " + Number(exp.status) + "): " + errMsg);
      }
      if (fm.fileExistsAtPath(outPath)) fm.removeItemAtPathError(outPath, null);
      fm.moveItemAtPathToPathError(tmpFinal, outPath, null);
      if (fm.fileExistsAtPath(vidPath)) fm.removeItemAtPathError(vidPath, null);
      return JSON.stringify({ status: "ok", audio_muxed: true, output: outPath });
    }
  JXA

  stdout, stderr, status = Open3.capture3("/usr/bin/osascript", "-l", "JavaScript", "-e", jxa, video_only_mp4, original_input_mp4, final_output_mp4)
  unless status.success?
    raise "JXA audio mux failed (exit #{status.exitstatus}): #{stderr.strip} #{stdout.strip}"
  end
  JSON.parse(stdout.strip)
end

def runner_html
  <<~'HTML'
    <!doctype html>
    <html>
    <head><meta charset="utf-8"><title>Chrome WebCodecs VP9 Runner</title></head>
    <body>
    <script>
    "use strict";

    function p2(n) { return String(n).padStart(2, "0"); }

    async function postJson(url, payload) {
      const resp = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const txt = await resp.text();
        throw new Error("POST " + url + " failed (" + resp.status + "): " + txt);
      }
      return await resp.json();
    }

    async function heartbeat(msg) {
      try {
        await fetch("/heartbeat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ msg: msg, ts: Date.now() }),
        });
      } catch (_) {}
    }

    // =========================================================================
    // MODE 1: FRAMES EXTRACTOR (<video> + <canvas> upright Rec.709 JPEG frames)
    // =========================================================================
    function waitEvent(el, okEv, timeoutMs) {
      return new Promise((resolve, reject) => {
        let settled = false;
        const timer = setTimeout(() => {
          if (!settled) {
            settled = true;
            cleanup();
            reject(new Error("Timeout waiting for " + okEv));
          }
        }, timeoutMs);
        function onOk() {
          if (!settled) { settled = true; cleanup(); resolve(); }
        }
        function onErr() {
          if (!settled) {
            settled = true;
            cleanup();
            const code = el.error ? el.error.code : "unknown";
            reject(new Error("Media error code=" + code));
          }
        }
        function cleanup() {
          clearTimeout(timer);
          el.removeEventListener(okEv, onOk);
          el.removeEventListener("error", onErr);
        }
        el.addEventListener(okEv, onOk, { once: true });
        el.addEventListener("error", onErr, { once: true });
      });
    }

    async function runFramesMode(jobs) {
      const video = document.createElement("video");
      video.muted = true;
      video.playsInline = true;
      video.preload = "auto";
      video.crossOrigin = "anonymous";
      const canvas = document.createElement("canvas");
      const ctx = canvas.getContext("2d", { colorSpace: "srgb" });

      const results = [];
      for (let i = 0; i < jobs.length; i++) {
        const job = jobs[i];
        const jobOut = { id: job.id, job_index: i, frames: [], status: "ok" };
        try {
          await heartbeat("frames_load_" + job.id);
          const metaP = waitEvent(video, "loadeddata", 25000);
          video.src = "/media/" + i;
          video.load();
          await metaP;

          const vw = video.videoWidth;
          const vh = video.videoHeight;
          const dur = isFinite(video.duration) && video.duration > 0 ? video.duration : 1.0;
          if (!vw || !vh) throw new Error("Zero video dimensions after loadeddata");

          const scale = Math.min(1.0, (job.max_dim || 450) / Math.max(vw, vh));
          const outW = Math.max(2, Math.round(vw * scale));
          const outH = Math.max(2, Math.round(vh * scale));
          canvas.width = outW;
          canvas.height = outH;
          jobOut.display_width = vw;
          jobOut.display_height = vh;
          jobOut.frame_width = outW;
          jobOut.frame_height = outH;
          jobOut.duration_sec = Math.round(dur * 1000) / 1000;

          const times = job.times || [0.5];
          const quality = job.quality || 0.88;
          for (let f = 0; f < times.length; f++) {
            let target = Math.max(0.0, Math.min(Math.max(0.0, dur - 0.04), Number(times[f]) || 0.0));
            if (Math.abs(video.currentTime - target) > 0.002) {
              const seekP = waitEvent(video, "seeked", 15000);
              video.currentTime = target;
              await seekP;
            }
            // Wait one animation frame / microtask to ensure decoded frame is committed
            await new Promise((r) => setTimeout(r, 15));
            ctx.drawImage(video, 0, 0, outW, outH);
            const blob = await new Promise((resolve, reject) => {
              canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("canvas.toBlob failed"))), "image/jpeg", quality);
            });
            const resp = await fetch("/upload/frame/" + i + "/" + f, {
              method: "POST",
              headers: { "Content-Type": "image/jpeg" },
              body: blob,
            });
            if (!resp.ok) throw new Error("Upload frame failed: " + resp.status);
            const up = await resp.json();
            jobOut.frames.push({ time_sec: times[f], actual_time_sec: Math.round(video.currentTime * 1000) / 1000, path: up.path, bytes: blob.size });
          }
        } catch (err) {
          jobOut.status = "error";
          jobOut.error = String(err && err.message ? err.message : err);
        } finally {
          video.pause();
          video.removeAttribute("src");
          video.load();
        }
        results.push(jobOut);
      }
      return results;
    }

    // =========================================================================
    // MODE 2: PURE-JS ISO-BMFF DEMUXER -> WEBCODECS -> H.264 MP4 MUXER
    // =========================================================================
    function readFourCC(dv, off) {
      return String.fromCharCode(
        dv.getUint8(off), dv.getUint8(off + 1), dv.getUint8(off + 2), dv.getUint8(off + 3)
      );
    }

    function readU64(dv, off) {
      const hi = dv.getUint32(off);
      const lo = dv.getUint32(off + 4);
      return hi * 4294967296 + lo;
    }

    function iterBoxes(dv, start, end) {
      const boxes = [];
      let pos = start;
      while (pos + 8 <= end) {
        let size = dv.getUint32(pos);
        const type = readFourCC(dv, pos + 4);
        let hdrLen = 8;
        if (size === 1) {
          if (pos + 16 > end) break;
          size = readU64(dv, pos + 8);
          hdrLen = 16;
        } else if (size === 0) {
          size = end - pos;
        }
        if (size < hdrLen || pos + size > end) break;
        boxes.push({ type: type, start: pos, dataStart: pos + hdrLen, end: pos + size, size: size });
        pos += size;
      }
      return boxes;
    }

    function findBox(boxes, type) {
      for (let i = 0; i < boxes.length; i++) {
        if (boxes[i].type === type) return boxes[i];
      }
      return null;
    }

    function parseVpccCodec(dv, dataStart, dataEnd) {
      const len = dataEnd - dataStart;
      if (len < 8) return "vp09.00.40.08";
      const ver = dv.getUint8(dataStart);
      if (ver === 1 && len >= 10) {
        const profile = dv.getUint8(dataStart + 4);
        const level = dv.getUint8(dataStart + 5);
        const b6 = dv.getUint8(dataStart + 6);
        const bitDepth = (b6 >> 4) & 0x0f;
        const chroma = (b6 >> 1) & 0x07;
        const fullRange = b6 & 0x01;
        const cp = dv.getUint8(dataStart + 7);
        const tc = dv.getUint8(dataStart + 8);
        const mc = dv.getUint8(dataStart + 9);
        return (
          "vp09." + p2(profile) + "." + p2(level) + "." + p2(bitDepth) +
          "." + p2(chroma) + "." + p2(cp) + "." + p2(tc) + "." + p2(mc) + "." + p2(fullRange)
        );
      }
      // Legacy v0 vpcC fallback
      const profile = dv.getUint8(dataStart + 4);
      const level = dv.getUint8(dataStart + 5);
      const b6 = dv.getUint8(dataStart + 6);
      const bitDepth = (b6 >> 4) & 0x0f;
      const chroma = b6 & 0x0f;
      return "vp09." + p2(profile) + "." + p2(level) + "." + p2(bitDepth || 8) + "." + p2(chroma);
    }

    function parseAv1cCodec(dv, dataStart, dataEnd) {
      if (dataEnd - dataStart < 4) return "av01.0.08M.08";
      const b1 = dv.getUint8(dataStart + 1);
      const b2 = dv.getUint8(dataStart + 2);
      const profile = (b1 >> 5) & 0x07;
      const level = b1 & 0x1f;
      const tier = (b2 >> 7) & 0x01 ? "H" : "M";
      const highBit = (b2 >> 6) & 0x01;
      const twelve = (b2 >> 5) & 0x01;
      const bitDepth = highBit ? (twelve ? 12 : 10) : 8;
      return "av01." + profile + "." + p2(level) + tier + "." + p2(bitDepth);
    }

    function parseMp4VideoTrack(arrayBuf) {
      const dv = new DataView(arrayBuf);
      const top = iterBoxes(dv, 0, dv.byteLength);
      const moov = findBox(top, "moov");
      if (!moov) throw new Error("No moov atom found in MP4");

      const traks = iterBoxes(dv, moov.dataStart, moov.end).filter((b) => b.type === "trak");
      for (let tIdx = 0; tIdx < traks.length; tIdx++) {
        const trakBoxes = iterBoxes(dv, traks[tIdx].dataStart, traks[tIdx].end);
        const tkhd = findBox(trakBoxes, "tkhd");
        const mdia = findBox(trakBoxes, "mdia");
        if (!tkhd || !mdia) continue;

        const mdiaBoxes = iterBoxes(dv, mdia.dataStart, mdia.end);
        const hdlr = findBox(mdiaBoxes, "hdlr");
        if (!hdlr || readFourCC(dv, hdlr.dataStart + 8) !== "vide") continue;

        const mdhd = findBox(mdiaBoxes, "mdhd");
        const minf = findBox(mdiaBoxes, "minf");
        if (!mdhd || !minf) throw new Error("Malformed video track: missing mdhd/minf");
        const minfBoxes = iterBoxes(dv, minf.dataStart, minf.end);
        const stbl = findBox(minfBoxes, "stbl");
        if (!stbl) throw new Error("Malformed video track: missing stbl");
        const stblBoxes = iterBoxes(dv, stbl.dataStart, stbl.end);

        // Parse tkhd matrix -> rotation degrees
        const tkVer = dv.getUint8(tkhd.dataStart);
        const matOff = tkhd.dataStart + (tkVer === 1 ? 52 : 40);
        const a = dv.getInt32(matOff) / 65536.0;
        const b = dv.getInt32(matOff + 4) / 65536.0;
        const rotDeg = ((Math.round((Math.atan2(b, a) * 180.0) / Math.PI) % 360) + 360) % 360;

        // Parse mdhd timescale & duration
        const mdVer = dv.getUint8(mdhd.dataStart);
        const timescale = dv.getUint32(mdhd.dataStart + (mdVer === 1 ? 20 : 12));
        const mdDuration = mdVer === 1 ? readU64(dv, mdhd.dataStart + 24) : dv.getUint32(mdhd.dataStart + 16);

        // Parse stsd
        const stsd = findBox(stblBoxes, "stsd");
        if (!stsd) throw new Error("Missing stsd");
        const entryList = iterBoxes(dv, stsd.dataStart + 8, stsd.end);
        if (entryList.length === 0) throw new Error("Empty stsd");
        const entry = entryList[0];
        const codecFourCC = entry.type;
        const codedWidth = dv.getUint16(entry.start + 32);
        const codedHeight = dv.getUint16(entry.start + 34);

        const subBoxes = iterBoxes(dv, entry.start + 86, entry.end);
        let codecString = null;
        let description = null;
        if (codecFourCC === "vp09") {
          const vpcc = findBox(subBoxes, "vpcC");
          codecString = vpcc ? parseVpccCodec(dv, vpcc.dataStart, vpcc.end) : "vp09.00.40.08";
        } else if (codecFourCC === "av01") {
          const av1c = findBox(subBoxes, "av1C");
          codecString = av1c ? parseAv1cCodec(dv, av1c.dataStart, av1c.end) : "av01.0.08M.08";
          if (av1c) description = new Uint8Array(arrayBuf, av1c.dataStart, av1c.end - av1c.dataStart);
        } else if (codecFourCC === "avc1" || codecFourCC === "avc3") {
          const avcc = findBox(subBoxes, "avcC");
          if (avcc && avcc.end - avcc.dataStart >= 4) {
            const p = dv.getUint8(avcc.dataStart + 1);
            const c = dv.getUint8(avcc.dataStart + 2);
            const l = dv.getUint8(avcc.dataStart + 3);
            codecString = "avc1." + p.toString(16).padStart(2, "0") + c.toString(16).padStart(2, "0") + l.toString(16).padStart(2, "0");
            description = new Uint8Array(arrayBuf, avcc.dataStart, avcc.end - avcc.dataStart);
          }
        } else if (codecFourCC === "hvc1" || codecFourCC === "hev1") {
          const hvcc = findBox(subBoxes, "hvcC");
          codecString = "hvc1.1.6.L93.B0";
          if (hvcc) description = new Uint8Array(arrayBuf, hvcc.dataStart, hvcc.end - hvcc.dataStart);
        } else {
          throw new Error("Unsupported video sample entry: " + codecFourCC);
        }

        // Parse stsz (sample sizes)
        const stsz = findBox(stblBoxes, "stsz");
        const uniformSize = dv.getUint32(stsz.dataStart + 4);
        const sampleCount = dv.getUint32(stsz.dataStart + 8);
        const sizes = new Uint32Array(sampleCount);
        if (uniformSize > 0) {
          sizes.fill(uniformSize);
        } else {
          for (let i = 0; i < sampleCount; i++) {
            sizes[i] = dv.getUint32(stsz.dataStart + 12 + i * 4);
          }
        }

        // Parse stts (decode durations)
        const stts = findBox(stblBoxes, "stts");
        const sttsEntries = dv.getUint32(stts.dataStart + 4);
        const dtsArr = new Float64Array(sampleCount);
        const durArr = new Uint32Array(sampleCount);
        let sIdx = 0;
        let curDts = 0;
        for (let i = 0; i < sttsEntries && sIdx < sampleCount; i++) {
          const cnt = dv.getUint32(stts.dataStart + 8 + i * 8);
          const delta = dv.getUint32(stts.dataStart + 12 + i * 8);
          for (let k = 0; k < cnt && sIdx < sampleCount; k++) {
            dtsArr[sIdx] = curDts;
            durArr[sIdx] = delta;
            curDts += delta;
            sIdx++;
          }
        }
        const totalTicks = curDts > 0 ? curDts : mdDuration;

        // Parse optional ctts (composition offsets)
        const ctsArr = new Int32Array(sampleCount);
        const ctts = findBox(stblBoxes, "ctts");
        if (ctts) {
          const cttsVer = dv.getUint8(ctts.dataStart);
          const cttsCnt = dv.getUint32(ctts.dataStart + 4);
          let ci = 0;
          let minCts = 0;
          for (let i = 0; i < cttsCnt && ci < sampleCount; i++) {
            const cnt = dv.getUint32(ctts.dataStart + 8 + i * 8);
            const off = cttsVer === 1
              ? dv.getInt32(ctts.dataStart + 12 + i * 8)
              : dv.getUint32(ctts.dataStart + 12 + i * 8);
            if (off < minCts) minCts = off;
            for (let k = 0; k < cnt && ci < sampleCount; k++) {
              ctsArr[ci++] = off;
            }
          }
          if (minCts < 0) {
            for (let i = 0; i < sampleCount; i++) ctsArr[i] -= minCts;
          }
        }

        // Parse stco / co64 (chunk offsets)
        const stco = findBox(stblBoxes, "stco");
        const co64 = findBox(stblBoxes, "co64");
        let chunkOffsets;
        if (stco) {
          const nChunks = dv.getUint32(stco.dataStart + 4);
          chunkOffsets = new Float64Array(nChunks);
          for (let i = 0; i < nChunks; i++) chunkOffsets[i] = dv.getUint32(stco.dataStart + 8 + i * 4);
        } else if (co64) {
          const nChunks = dv.getUint32(co64.dataStart + 4);
          chunkOffsets = new Float64Array(nChunks);
          for (let i = 0; i < nChunks; i++) chunkOffsets[i] = readU64(dv, co64.dataStart + 8 + i * 8);
        } else {
          throw new Error("Missing stco/co64");
        }

        // Parse stsc (sample-to-chunk mapping) -> compute byte offset of each sample
        const stsc = findBox(stblBoxes, "stsc");
        const stscCnt = dv.getUint32(stsc.dataStart + 4);
        const stscRuns = [];
        for (let i = 0; i < stscCnt; i++) {
          stscRuns.push({
            firstChunk: dv.getUint32(stsc.dataStart + 8 + i * 12),
            samplesPerChunk: dv.getUint32(stsc.dataStart + 12 + i * 12),
          });
        }
        const offsets = new Float64Array(sampleCount);
        let samplePtr = 0;
        for (let c = 0; c < chunkOffsets.length && samplePtr < sampleCount; c++) {
          const chunkNum1 = c + 1;
          let spc = stscRuns[0].samplesPerChunk;
          for (let r = 0; r < stscRuns.length; r++) {
            if (stscRuns[r].firstChunk <= chunkNum1) spc = stscRuns[r].samplesPerChunk;
            else break;
          }
          let byteOff = chunkOffsets[c];
          for (let k = 0; k < spc && samplePtr < sampleCount; k++) {
            offsets[samplePtr] = byteOff;
            byteOff += sizes[samplePtr];
            samplePtr++;
          }
        }

        // Parse optional stss (keyframes)
        const stss = findBox(stblBoxes, "stss");
        let keyframes = null;
        if (stss) {
          const nKey = dv.getUint32(stss.dataStart + 4);
          keyframes = new Set();
          for (let i = 0; i < nKey; i++) keyframes.add(dv.getUint32(stss.dataStart + 8 + i * 4) - 1);
        }

        const durationSec = totalTicks / timescale;
        const fps = durationSec > 0 ? sampleCount / durationSec : 30.0;

        return {
          codecFourCC,
          codecString,
          description,
          codedWidth,
          codedHeight,
          rotationDeg: rotDeg,
          timescale,
          totalTicks,
          durationSec,
          fps,
          sampleCount,
          sizes,
          offsets,
          dtsArr,
          durArr,
          ctsArr,
          keyframes,
        };
      }
      throw new Error("No video track found in MP4");
    }

    // ---- Pure-JS ISO-BMFF H.264 (`avc1`) MP4 Muxer ----
    function concatU8(arrays) {
      let total = 0;
      for (let i = 0; i < arrays.length; i++) total += arrays[i].byteLength;
      const out = new Uint8Array(total);
      let off = 0;
      for (let i = 0; i < arrays.length; i++) {
        out.set(arrays[i], off);
        off += arrays[i].byteLength;
      }
      return out;
    }

    function u32Bytes(v) {
      const b = new Uint8Array(4);
      new DataView(b.buffer).setUint32(0, v >>> 0);
      return b;
    }

    function u16Bytes(v) {
      const b = new Uint8Array(2);
      new DataView(b.buffer).setUint16(0, v & 0xffff);
      return b;
    }

    function strBytes(s) {
      const b = new Uint8Array(s.length);
      for (let i = 0; i < s.length; i++) b[i] = s.charCodeAt(i);
      return b;
    }

    function makeBox(type, payloadParts) {
      const payload = Array.isArray(payloadParts) ? concatU8(payloadParts) : payloadParts;
      const hdr = new Uint8Array(8);
      const dv = new DataView(hdr.buffer);
      dv.setUint32(0, 8 + payload.byteLength);
      for (let i = 0; i < 4; i++) hdr[4 + i] = type.charCodeAt(i);
      return concatU8([hdr, payload]);
    }

    function makeFullBox(type, version, flags, payloadParts) {
      const vf = new Uint8Array(4);
      vf[0] = version & 0xff;
      vf[1] = (flags >> 16) & 0xff;
      vf[2] = (flags >> 8) & 0xff;
      vf[3] = flags & 0xff;
      const parts = Array.isArray(payloadParts) ? [vf].concat(payloadParts) : [vf, payloadParts];
      return makeBox(type, parts);
    }

    function buildUnityMatrix() {
      const b = new Uint8Array(36);
      const dv = new DataView(b.buffer);
      dv.setUint32(0, 0x00010000);
      dv.setUint32(16, 0x00010000);
      dv.setUint32(32, 0x40000000);
      return b;
    }

    function muxAvcMp4(samples, avcCBytes, width, height, targetDurationSec) {
      const TIMESCALE = 90000;
      const n = samples.length;
      if (n === 0) throw new Error("No encoded video chunks produced");

      // Compute exact stts sample durations in 90000 ticks from encoded timestamps/durations
      const durs = new Uint32Array(n);
      let hasOutOfOrder = false;
      for (let i = 1; i < n; i++) {
        if (samples[i].timestamp < samples[i - 1].timestamp) {
          hasOutOfOrder = true;
          break;
        }
      }

      let totalTicks = 0;
      const ctsOffsets = hasOutOfOrder ? new Int32Array(n) : null;

      if (!hasOutOfOrder) {
        const baseTs = samples[0].timestamp;
        let prevTick = 0;
        for (let i = 0; i < n; i++) {
          let nextTick;
          if (i + 1 < n) {
            nextTick = Math.max(prevTick + 1, Math.round(((samples[i + 1].timestamp - baseTs) / 1e6) * TIMESCALE));
          } else {
            const lastDurUs = samples[i].duration || (i > 0 ? Math.max(1000, samples[i].timestamp - samples[i - 1].timestamp) : 33333);
            const estEnd = Math.round(((samples[i].timestamp - baseTs + lastDurUs) / 1e6) * TIMESCALE);
            const targetEnd = targetDurationSec ? Math.round(targetDurationSec * TIMESCALE) : estEnd;
            nextTick = Math.max(prevTick + 1, Math.abs(targetEnd - estEnd) < TIMESCALE * 0.1 ? targetEnd : estEnd);
          }
          durs[i] = nextTick - prevTick;
          prevTick = nextTick;
        }
        totalTicks = prevTick;
      } else {
        // Handle B-frame reordering cleanly using sorted PTS deltas + ctts v1
        const sortedPts = samples.map((s) => s.timestamp).sort((a, b) => a - b);
        const basePts = sortedPts[0];
        const avgDeltaUs = n > 1 ? (sortedPts[n - 1] - basePts) / (n - 1) : 33333;
        const avgTick = Math.max(1, Math.round((avgDeltaUs / 1e6) * TIMESCALE));
        for (let i = 0; i < n; i++) {
          durs[i] = avgTick;
          const dtsTick = i * avgTick;
          const ptsTick = Math.round(((samples[i].timestamp - basePts) / 1e6) * TIMESCALE);
          ctsOffsets[i] = ptsTick - dtsTick;
        }
        totalTicks = n * avgTick;
      }

      // Build ftyp box
      const ftyp = makeBox("ftyp", [
        strBytes("isom"), u32Bytes(0x00000200),
        strBytes("isom"), strBytes("iso2"), strBytes("avc1"), strBytes("mp41"),
      ]);

      // Compute mdat header & byte layout
      let mdatPayloadBytes = 0;
      for (let i = 0; i < n; i++) mdatPayloadBytes += samples[i].data.byteLength;
      const useLargeMdat = (8 + mdatPayloadBytes) > 0xffffffff;
      let mdatHeader;
      if (!useLargeMdat) {
        mdatHeader = new Uint8Array(8);
        new DataView(mdatHeader.buffer).setUint32(0, 8 + mdatPayloadBytes);
        mdatHeader.set(strBytes("mdat"), 4);
      } else {
        mdatHeader = new Uint8Array(16);
        const hdv = new DataView(mdatHeader.buffer);
        hdv.setUint32(0, 1);
        mdatHeader.set(strBytes("mdat"), 4);
        const totalMdat = 16 + mdatPayloadBytes;
        hdv.setUint32(8, Math.floor(totalMdat / 4294967296));
        hdv.setUint32(12, totalMdat >>> 0);
      }
      const mdatDataStart = ftyp.byteLength + mdatHeader.byteLength;

      // Chunking: group every 30 samples into a chunk for clean seeking & compact stco
      const CHUNK_SIZE = 30;
      const chunkOffsets = [];
      const stscRuns = [];
      let curOffset = mdatDataStart;
      let chunkIdx = 0;
      let prevSpc = -1;
      for (let i = 0; i < n; i += CHUNK_SIZE) {
        chunkIdx++;
        const spc = Math.min(CHUNK_SIZE, n - i);
        chunkOffsets.push(curOffset);
        if (spc !== prevSpc) {
          stscRuns.push({ firstChunk: chunkIdx, spc: spc });
          prevSpc = spc;
        }
        for (let k = 0; k < spc; k++) curOffset += samples[i + k].data.byteLength;
      }

      // 1. mvhd
      const mvhdBody = new Uint8Array(96);
      const mvhdDv = new DataView(mvhdBody.buffer);
      mvhdDv.setUint32(8, TIMESCALE);
      mvhdDv.setUint32(12, totalTicks >>> 0);
      mvhdDv.setUint32(16, 0x00010000); // rate 1.0
      mvhdDv.setUint16(20, 0x0100);     // volume 1.0
      mvhdBody.set(buildUnityMatrix(), 32);
      mvhdDv.setUint32(92, 2); // next_track_ID
      const mvhd = makeFullBox("mvhd", 0, 0, mvhdBody);

      // 2. tkhd (flags = 3: enabled + in_movie)
      const tkhdBody = new Uint8Array(80);
      const tkhdDv = new DataView(tkhdBody.buffer);
      tkhdDv.setUint32(8, 1); // track_ID
      tkhdDv.setUint32(16, totalTicks >>> 0);
      tkhdBody.set(buildUnityMatrix(), 36);
      tkhdDv.setUint32(72, (width << 16) >>> 0);
      tkhdDv.setUint32(76, (height << 16) >>> 0);
      const tkhd = makeFullBox("tkhd", 0, 3, tkhdBody);

      // 3. mdhd
      const mdhdBody = new Uint8Array(20);
      const mdhdDv = new DataView(mdhdBody.buffer);
      mdhdDv.setUint32(8, TIMESCALE);
      mdhdDv.setUint32(12, totalTicks >>> 0);
      mdhdDv.setUint16(16, 0x55c4); // "und"
      const mdhd = makeFullBox("mdhd", 0, 0, mdhdBody);

      // 4. hdlr
      const hdlr = makeFullBox("hdlr", 0, 0, [
        u32Bytes(0), strBytes("vide"),
        new Uint8Array(12), strBytes("VideoHandler\0"),
      ]);

      // 5. vmhd + dinf
      const vmhd = makeFullBox("vmhd", 0, 1, new Uint8Array(8));
      const dref = makeFullBox("dref", 0, 0, [u32Bytes(1), makeFullBox("url ", 0, 1, new Uint8Array(0))]);
      const dinf = makeBox("dinf", dref);

      // 6. stsd -> avc1 -> avcC + colr (Rec.709 nclx)
      const avc1Header = new Uint8Array(78);
      const aDv = new DataView(avc1Header.buffer);
      aDv.setUint16(6, 1);           // data_reference_index
      aDv.setUint16(24, width);
      aDv.setUint16(26, height);
      aDv.setUint32(28, 0x00480000); // 72 dpi horiz
      aDv.setUint32(32, 0x00480000); // 72 dpi vert
      aDv.setUint16(40, 1);          // frame_count
      aDv.setUint16(74, 0x0018);     // depth 24
      aDv.setInt16(76, -1);          // pre_defined
      const avcCBox = makeBox("avcC", avcCBytes);
      const colrBox = makeBox("colr", [
        strBytes("nclx"),
        u16Bytes(1), // BT.709 primaries
        u16Bytes(1), // BT.709 transfer
        u16Bytes(1), // BT.709 matrix
        new Uint8Array([0x00]), // video/limited range
      ]);
      const avc1 = makeBox("avc1", [avc1Header, avcCBox, colrBox]);
      const stsd = makeFullBox("stsd", 0, 0, [u32Bytes(1), avc1]);

      // 7. stts (run-length compressed)
      const sttsRuns = [];
      for (let i = 0; i < n; i++) {
        const d = durs[i];
        if (sttsRuns.length > 0 && sttsRuns[sttsRuns.length - 1].delta === d) {
          sttsRuns[sttsRuns.length - 1].count++;
        } else {
          sttsRuns.push({ count: 1, delta: d });
        }
      }
      const sttsBuf = new Uint8Array(4 + sttsRuns.length * 8);
      const sttsDv = new DataView(sttsBuf.buffer);
      sttsDv.setUint32(0, sttsRuns.length);
      for (let i = 0; i < sttsRuns.length; i++) {
        sttsDv.setUint32(4 + i * 8, sttsRuns[i].count);
        sttsDv.setUint32(8 + i * 8, sttsRuns[i].delta);
      }
      const stts = makeFullBox("stts", 0, 0, sttsBuf);

      // 8. optional ctts (v1 signed)
      let ctts = null;
      if (ctsOffsets) {
        const cttsBuf = new Uint8Array(4 + n * 8);
        const cttsDv = new DataView(cttsBuf.buffer);
        cttsDv.setUint32(0, n);
        for (let i = 0; i < n; i++) {
          cttsDv.setUint32(4 + i * 8, 1);
          cttsDv.setInt32(8 + i * 8, ctsOffsets[i]);
        }
        ctts = makeFullBox("ctts", 1, 0, cttsBuf);
      }

      // 9. stss (sync/keyframe table)
      const keyIdx = [];
      for (let i = 0; i < n; i++) {
        if (samples[i].isKey) keyIdx.push(i + 1);
      }
      const stssBuf = new Uint8Array(4 + keyIdx.length * 4);
      const stssDv = new DataView(stssBuf.buffer);
      stssDv.setUint32(0, keyIdx.length);
      for (let i = 0; i < keyIdx.length; i++) stssDv.setUint32(4 + i * 4, keyIdx[i]);
      const stss = makeFullBox("stss", 0, 0, stssBuf);

      // 10. stsc
      const stscBuf = new Uint8Array(4 + stscRuns.length * 12);
      const stscDv = new DataView(stscBuf.buffer);
      stscDv.setUint32(0, stscRuns.length);
      for (let i = 0; i < stscRuns.length; i++) {
        stscDv.setUint32(4 + i * 12, stscRuns[i].firstChunk);
        stscDv.setUint32(8 + i * 12, stscRuns[i].spc);
        stscDv.setUint32(12 + i * 12, 1);
      }
      const stsc = makeFullBox("stsc", 0, 0, stscBuf);

      // 11. stsz
      const stszBuf = new Uint8Array(8 + n * 4);
      const stszDv = new DataView(stszBuf.buffer);
      stszDv.setUint32(0, 0);
      stszDv.setUint32(4, n);
      for (let i = 0; i < n; i++) stszDv.setUint32(8 + i * 4, samples[i].data.byteLength);
      const stsz = makeFullBox("stsz", 0, 0, stszBuf);

      // 12. stco / co64
      const needs64 = chunkOffsets.length > 0 && chunkOffsets[chunkOffsets.length - 1] > 0xffffffff;
      let chunkOffBox;
      if (!needs64) {
        const b = new Uint8Array(4 + chunkOffsets.length * 4);
        const dv = new DataView(b.buffer);
        dv.setUint32(0, chunkOffsets.length);
        for (let i = 0; i < chunkOffsets.length; i++) dv.setUint32(4 + i * 4, chunkOffsets[i] >>> 0);
        chunkOffBox = makeFullBox("stco", 0, 0, b);
      } else {
        const b = new Uint8Array(4 + chunkOffsets.length * 8);
        const dv = new DataView(b.buffer);
        dv.setUint32(0, chunkOffsets.length);
        for (let i = 0; i < chunkOffsets.length; i++) {
          dv.setUint32(4 + i * 8, Math.floor(chunkOffsets[i] / 4294967296));
          dv.setUint32(8 + i * 8, chunkOffsets[i] >>> 0);
        }
        chunkOffBox = makeFullBox("co64", 0, 0, b);
      }

      const stblChildren = [stsd, stts];
      if (ctts) stblChildren.push(ctts);
      stblChildren.push(stss, stsc, stsz, chunkOffBox);

      const stbl = makeBox("stbl", stblChildren);
      const minf = makeBox("minf", [vmhd, dinf, stbl]);
      const mdia = makeBox("mdia", [mdhd, hdlr, minf]);
      const trak = makeBox("trak", [tkhd, mdia]);
      const moov = makeBox("moov", [mvhd, trak]);

      const blobParts = [ftyp, mdatHeader];
      for (let i = 0; i < n; i++) blobParts.push(samples[i].data);
      blobParts.push(moov);
      return new Blob(blobParts, { type: "video/mp4" });
    }

    async function pickH264EncoderConfig(outW, outH, fps, bitrate) {
      const candidates =
        outW * outH * fps > 1920 * 1080 * 60
          ? ["avc1.640033", "avc1.64002a", "avc1.4d0033", "avc1.42e033"]
          : ["avc1.64002a", "avc1.640033", "avc1.4d002a", "avc1.42e02a"];
      for (let i = 0; i < candidates.length; i++) {
        const cfg = {
          codec: candidates[i],
          width: outW,
          height: outH,
          bitrate: bitrate,
          framerate: Math.max(1, Math.round(fps * 100) / 100),
          hardwareAcceleration: "prefer-hardware",
          latencyMode: "realtime",
          avc: { format: "avc" },
        };
        const sup = await VideoEncoder.isConfigSupported(cfg);
        if (sup && sup.supported) return sup.config;
      }
      throw new Error("No supported hardware H.264 VideoEncoder config found");
    }

    async function transcodeOneJob(job, jobIdx) {
      await heartbeat("transcode_fetch_" + job.id);
      const resp = await fetch("/media/" + jobIdx);
      if (!resp.ok) throw new Error("Failed to fetch /media/" + jobIdx + ": " + resp.status);
      let arrayBuf = await resp.arrayBuffer();
      const track = parseMp4VideoTrack(arrayBuf);

      const decCfg = {
        codec: track.codecString,
        codedWidth: track.codedWidth,
        codedHeight: track.codedHeight,
      };
      if (track.description) decCfg.description = track.description;
      const decSup = await VideoDecoder.isConfigSupported(decCfg);
      if (!decSup || !decSup.supported) {
        throw new Error("VideoDecoder does not support codec: " + track.codecString);
      }

      const rotated = track.rotationDeg === 90 || track.rotationDeg === 270;
      const dispW = rotated ? track.codedHeight : track.codedWidth;
      const dispH = rotated ? track.codedWidth : track.codedHeight;
      const maxDim = job.max_dim || 1920;
      const scale = Math.min(1.0, maxDim / Math.max(dispW, dispH));
      const outW = Math.max(2, Math.round((dispW * scale) / 2) * 2);
      const outH = Math.max(2, Math.round((dispH * scale) / 2) * 2);

      const encCfg = await pickH264EncoderConfig(outW, outH, track.fps, job.bitrate || 12000000);
      const keyInterval = Math.max(1, Math.round(track.fps));

      const canvas = new OffscreenCanvas(outW, outH);
      const ctx = canvas.getContext("2d", { colorSpace: "srgb" });

      const encodedSamples = [];
      let avcCBytes = null;
      let fatalErr = null;
      let decodedFrames = 0;

      const encoder = new VideoEncoder({
        output(chunk, meta) {
          if (!avcCBytes && meta && meta.decoderConfig && meta.decoderConfig.description) {
            const desc = meta.decoderConfig.description;
            avcCBytes = desc instanceof Uint8Array
              ? new Uint8Array(desc)
              : new Uint8Array(desc.buffer || desc, desc.byteOffset || 0, desc.byteLength);
          }
          const buf = new Uint8Array(chunk.byteLength);
          chunk.copyTo(buf);
          encodedSamples.push({
            data: buf,
            timestamp: chunk.timestamp,
            duration: chunk.duration || 0,
            isKey: chunk.type === "key",
          });
        },
        error(e) {
          fatalErr = e;
        },
      });
      encoder.configure(encCfg);

      const decoder = new VideoDecoder({
        output(frame) {
          try {
            if (track.rotationDeg === 0) {
              ctx.drawImage(frame, 0, 0, outW, outH);
            } else {
              ctx.save();
              ctx.translate(outW / 2.0, outH / 2.0);
              ctx.rotate((track.rotationDeg * Math.PI) / 180.0);
              if (rotated) {
                ctx.drawImage(frame, -outH / 2.0, -outW / 2.0, outH, outW);
              } else {
                ctx.drawImage(frame, -outW / 2.0, -outH / 2.0, outW, outH);
              }
              ctx.restore();
            }
            const encFrame = new VideoFrame(canvas, {
              timestamp: frame.timestamp,
              duration: frame.duration,
            });
            const isKey = decodedFrames % keyInterval === 0;
            decodedFrames++;
            encoder.encode(encFrame, { keyFrame: isKey });
            encFrame.close();
          } catch (e) {
            fatalErr = e;
          } finally {
            frame.close();
          }
        },
        error(e) {
          fatalErr = e;
        },
      });
      decoder.configure(decCfg);

      const total = track.sampleCount;
      for (let i = 0; i < total; i++) {
        if (fatalErr) throw fatalErr;
        // Backpressure so M5 Pro RAM stays low
        while (decoder.decodeQueueSize > 8 || encoder.encodeQueueSize > 8) {
          if (fatalErr) throw fatalErr;
          await new Promise((r) => setTimeout(r, 4));
        }
        const ptsTicks = track.dtsArr[i] + track.ctsArr[i];
        const tsUs = Math.round((ptsTicks / track.timescale) * 1e6);
        const durUs = Math.round((track.durArr[i] / track.timescale) * 1e6);
        const isKey = track.keyframes ? track.keyframes.has(i) : (i === 0);
        const chunkData = new Uint8Array(arrayBuf, track.offsets[i], track.sizes[i]);
        decoder.decode(new EncodedVideoChunk({
          type: isKey ? "key" : "delta",
          timestamp: tsUs,
          duration: durUs,
          data: chunkData,
        }));
        if (i > 0 && i % 60 === 0) {
          await heartbeat("transcode_" + job.id + "_" + i + "/" + total);
        }
      }

      await decoder.flush();
      await encoder.flush();
      decoder.close();
      encoder.close();
      arrayBuf = null; // Release input MP4 buffer immediately

      if (fatalErr) throw fatalErr;
      if (!avcCBytes) throw new Error("VideoEncoder did not emit avcC decoderConfig description");

      await heartbeat("transcode_mux_" + job.id);
      const mp4Blob = muxAvcMp4(encodedSamples, avcCBytes, outW, outH, track.durationSec);

      const upResp = await fetch("/upload/video/" + jobIdx, {
        method: "POST",
        headers: { "Content-Type": "video/mp4" },
        body: mp4Blob,
      });
      if (!upResp.ok) {
        const errTxt = await upResp.text();
        throw new Error("Video upload/audio-mux failed (" + upResp.status + "): " + errTxt);
      }
      const muxRes = await upResp.json();

      return {
        id: job.id,
        job_index: jobIdx,
        status: "ok",
        source_codec: track.codecString,
        output_codec: encCfg.codec,
        output: muxRes.output,
        audio_muxed: muxRes.audio_muxed,
        width: outW,
        height: outH,
        rotation_baked_deg: track.rotationDeg,
        source_frames: total,
        encoded_frames: encodedSamples.length,
        fps: Math.round(track.fps * 100) / 100,
        duration_sec: Math.round(track.durationSec * 1000) / 1000,
        video_bytes: mp4Blob.size,
      };
    }

    async function runTranscodeMode(jobs) {
      const results = [];
      for (let i = 0; i < jobs.length; i++) {
        try {
          const res = await transcodeOneJob(jobs[i], i);
          results.push(res);
        } catch (err) {
          results.push({
            id: jobs[i].id,
            job_index: i,
            status: "error",
            error: String(err && err.message ? err.message : err),
          });
        }
      }
      return results;
    }

    (async function main() {
      try {
        const specResp = await fetch("/jobs.json");
        const spec = await specResp.json();
        const results =
          spec.mode === "frames"
            ? await runFramesMode(spec.jobs || [])
            : await runTranscodeMode(spec.jobs || []);
        await postJson("/done", { status: "ok", mode: spec.mode, results: results });
      } catch (err) {
        try {
          await postJson("/done", {
            status: "error",
            error: String(err && err.stack ? err.stack : err),
            results: [],
          });
        } catch (_) {}
      }
    })();
    </script>
    </body>
    </html>
  HTML
end

def serve_range_file(req, res, file_path)
  size = File.size(file_path)
  res["Content-Type"] = "video/mp4"
  res["Accept-Ranges"] = "bytes"
  range = req["Range"]
  if range && range =~ /\Abytes=(\d*)-(\d*)\z/
    s_str = Regexp.last_match(1)
    e_str = Regexp.last_match(2)
    if s_str.empty? && !e_str.empty?
      suffix = e_str.to_i
      start_b = [0, size - suffix].max
      end_b = size - 1
    else
      start_b = s_str.to_i
      end_b = e_str.empty? ? (size - 1) : [e_str.to_i, size - 1].min
    end
    if start_b > end_b || start_b >= size
      res.status = 416
      res["Content-Range"] = "bytes */#{size}"
      res.body = ""
      return
    end
    length = end_b - start_b + 1
    res.status = 206
    res["Content-Range"] = "bytes #{start_b}-#{end_b}/#{size}"
    res["Content-Length"] = length.to_s
    File.open(file_path, "rb") do |f|
      f.seek(start_b)
      res.body = f.read(length)
    end
  else
    res.status = 200
    res["Content-Length"] = size.to_s
    res.body = File.open(file_path, "rb")
  end
end

def main(argv)
  opts = parse_cli(argv)
  if opts[:help]
    puts JSON.pretty_generate(
      script: "macos_chrome_vp9.rb",
      modes: {
        frames: "macos_chrome_vp9.rb --mode frames (--batch-json spec.json | --input clip.mp4 --times 0.5,2.0 --out-dir ./frames) [--max-dim 450] [--quality 0.88]",
        transcode: "macos_chrome_vp9.rb --mode transcode (--batch-json spec.json | --input clip.mp4 --output out_h264.mp4) [--max-dim 1920] [--bitrate 12000000]",
      },
      flags: ["--dry-run", "--chrome-bin <path>", "--timeout <sec>"],
    )
    return 0
  end

  chrome_bin = find_chrome_binary(opts[:chrome_bin])
  jobs = normalize_jobs(opts)

  if opts[:dry_run]
    puts JSON.pretty_generate(
      status: "ok",
      dry_run: true,
      mode: opts[:mode],
      chrome_bin: chrome_bin,
      job_count: jobs.length,
      jobs: jobs,
    )
    return 0
  end

  done_queue = Queue.new
  last_activity = Time.now
  activity_mutex = Mutex.new
  touch_activity = lambda { activity_mutex.synchronize { last_activity = Time.now } }

  tmp_profile = Dir.mktmpdir("chrome_vp9_")
  tmp_vid_files = []
  chrome_pid = nil
  server = nil

  cleanup = lambda do
    if chrome_pid
      begin
        Process.kill("TERM", chrome_pid)
        Timeout.timeout(3) { Process.wait(chrome_pid) } rescue nil
      rescue Errno::ESRCH, Errno::ECHILD
      end
      begin
        Process.kill("KILL", chrome_pid)
        Process.wait(chrome_pid) rescue nil
      rescue Errno::ESRCH, Errno::ECHILD
      end
      chrome_pid = nil
    end
    begin
      server&.shutdown
    rescue StandardError
    end
    tmp_vid_files.each { |f| FileUtils.rm_f(f) }
    FileUtils.rm_rf(tmp_profile) if tmp_profile && Dir.exist?(tmp_profile)
  end

  %w[INT TERM].each do |sig|
    Signal.trap(sig) do
      cleanup.call
      exit(143)
    end
  end

  begin
    require "timeout"
    server = WEBrick::HTTPServer.new(
      BindAddress: "127.0.0.1",
      Port: 0,
      Logger: WEBrick::Log.new(File::NULL),
      AccessLog: [],
    )
    port = server.config[:Port]

    server.mount_proc("/runner.html") do |_req, res|
      touch_activity.call
      res["Content-Type"] = "text/html; charset=utf-8"
      res.body = runner_html
    end

    server.mount_proc("/jobs.json") do |_req, res|
      touch_activity.call
      sanitized = jobs.map do |j|
        h = { "job_index" => j["job_index"], "id" => j["id"], "max_dim" => j["max_dim"] }
        if opts[:mode] == "frames"
          h["times"] = j["times"]
          h["quality"] = j["quality"]
        else
          h["bitrate"] = j["bitrate"]
        end
        h
      end
      res["Content-Type"] = "application/json"
      res.body = JSON.generate({ "mode" => opts[:mode], "jobs" => sanitized })
    end

    server.mount_proc("/media") do |req, res|
      touch_activity.call
      idx = req.path_info.sub(%r{\A/}, "").to_i
      job = jobs[idx]
      if job && File.file?(job["input"])
        serve_range_file(req, res, job["input"])
      else
        res.status = 404
        res.body = "Not found"
      end
    end

    server.mount_proc("/upload/frame") do |req, res|
      touch_activity.call
      parts = req.path_info.sub(%r{\A/}, "").split("/")
      job_idx = parts[0].to_i
      frame_idx = parts[1].to_i
      job = jobs[job_idx]
      out_path = job && job["out_paths"] && job["out_paths"][frame_idx]
      unless out_path
        res.status = 400
        res.body = "Invalid frame target"
        next
      end
      FileUtils.mkdir_p(File.dirname(out_path))
      File.binwrite(out_path, req.body || "")
      res["Content-Type"] = "application/json"
      res.body = JSON.generate({ "status" => "ok", "path" => out_path })
    end

    server.mount_proc("/upload/video") do |req, res|
      touch_activity.call
      job_idx = req.path_info.sub(%r{\A/}, "").to_i
      job = jobs[job_idx]
      unless job && job["output"]
        res.status = 400
        res.body = "Invalid video target"
        next
      end
      FileUtils.mkdir_p(File.dirname(job["output"]))
      tmp_vid = "#{job['output']}.chrome_vidonly.#{Process.pid}.mp4"
      tmp_vid_files << tmp_vid
      File.open(tmp_vid, "wb") do |fh|
        req.body { |chunk| fh.write(chunk) }
      end
      touch_activity.call
      begin
        mux_res = mux_original_audio_jxa(tmp_vid, job["input"], job["output"])
        res["Content-Type"] = "application/json"
        res.body = JSON.generate(mux_res)
      rescue StandardError => e
        res.status = 500
        res["Content-Type"] = "text/plain"
        res.body = e.message
      ensure
        FileUtils.rm_f(tmp_vid)
      end
    end

    server.mount_proc("/heartbeat") do |_req, res|
      touch_activity.call
      res["Content-Type"] = "application/json"
      res.body = '{"ok":true}'
    end

    server.mount_proc("/done") do |req, res|
      touch_activity.call
      payload = JSON.parse(req.body.to_s)
      res["Content-Type"] = "application/json"
      res.body = '{"ok":true}'
      done_queue << payload
    end

    server_thread = Thread.new { server.start }

    chrome_args = [
      chrome_bin,
      "--headless=new",
      "--user-data-dir=#{tmp_profile}",
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-extensions",
      "--disable-background-networking",
      "--disable-sync",
      "--autoplay-policy=no-user-gesture-required",
      "--mute-audio",
      "--disable-features=MediaRouter",
      "--hide-scrollbars",
      "http://127.0.0.1:#{port}/runner.html",
    ]
    chrome_pid = Process.spawn(*chrome_args, out: File::NULL, err: File::NULL)

    result_payload = nil
    loop do
      unless done_queue.empty?
        result_payload = done_queue.pop(true) rescue nil
        break if result_payload
      end

      # Check if Chrome exited prematurely before posting /done
      if chrome_pid
        waited = Process.waitpid(chrome_pid, Process::WNOHANG) rescue chrome_pid
        if waited
          chrome_pid = nil
          sleep(0.15)
          result_payload = done_queue.pop(true) rescue nil
          break if result_payload
          raise "Headless Chrome exited prematurely before completing #{opts[:mode]} batch."
        end
      end

      elapsed = activity_mutex.synchronize { Time.now - last_activity }
      if elapsed > opts[:timeout]
        raise "Timed out after #{opts[:timeout]}s waiting for Chrome #{opts[:mode]} worker."
      end
      sleep(0.1)
    end

    final_out = {
      "status" => result_payload["status"],
      "mode" => opts[:mode],
      "chrome_bin" => chrome_bin,
      "job_count" => jobs.length,
      "results" => result_payload["results"] || [],
    }
    final_out["error"] = result_payload["error"] if result_payload["error"]

    puts JSON.pretty_generate(final_out)
    has_err = final_out["status"] != "ok" || final_out["results"].any? { |r| r["status"] != "ok" }
    has_err ? 1 : 0
  ensure
    cleanup.call
    server_thread&.join(1) if defined?(server_thread) && server_thread
  end
end

begin
  exit(main(ARGV))
rescue StandardError => e
  warn JSON.pretty_generate(status: "error", error: e.message)
  exit(1)
end
