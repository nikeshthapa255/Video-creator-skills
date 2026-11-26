/**
 * Trip Cinema Studio v0.2.0 — Dynamic Single & Multi-Source Layouts (macOS Native JXA).
 *
 * Supports both 16:9 (1920x1080) and 9:16 (1080x1920) canvases across all 7 layouts:
 *   - single (full-bleed crop for video, Ken Burns / static for still)
 *   - blurred_bg_pillarbox (edge-clamped blurred backdrop dimmed ~25% + drop shadow behind subject)
 *   - split_diptych_pair / split_2up_video (2-up side-by-side live videos or photos with style gutter)
 *   - triptych_3up_video (3 concurrent live vertical videos side-by-side in 16:9)
 *   - bento_living_grid (asymmetric 2/3/4-tile rounded-rect grid with beat-staggered pop-ins)
 *   - polaroid_stack (kraft/paper canvas + 1-3 tilted Polaroid/Instax cards + tape + spring drop + EXIF captions)
 *   - super8_frame (rounded Super 8 gate matte + sprocket edge + optional retro corner date stamp)
 */

(function (root) {
  "use strict";

  function clamp(v, lo, hi) {
    return Math.min(hi, Math.max(lo, v));
  }

  function easeOutCubic(t) {
    const c = clamp(t, 0.0, 1.0);
    return 1.0 - Math.pow(1.0 - c, 3.0);
  }

  function dampedSpring(t) {
    const c = clamp(t, 0.0, 1.0);
    if (c <= 0.0) return 0.0;
    if (c >= 1.0) return 1.0;
    // Snappy organic Polaroid drop-in spring (settles cleanly by t=1.0)
    return 1.0 - Math.exp(-6.5 * c) * Math.cos(8.5 * c);
  }

  function normalizeOrigin(ciImg) {
    if (!ciImg) return ciImg;
    const ext = ciImg.extent;
    if (ext.origin.x !== 0 || ext.origin.y !== 0) {
      return ciImg.imageByApplyingTransform($.CGAffineTransformMakeTranslation(-ext.origin.x, -ext.origin.y));
    }
    return ciImg;
  }

  function normalizeAnchor(anchor) {
    if (Array.isArray(anchor) && anchor.length >= 2) {
      return [clamp(Number(anchor[0]) || 0.5, 0.05, 0.95), clamp(Number(anchor[1]) || 0.5, 0.05, 0.95)];
    }
    if (anchor && typeof anchor === "object") {
      const ax = anchor.x !== undefined ? Number(anchor.x) : 0.5;
      const ay = anchor.y !== undefined ? Number(anchor.y) : 0.5;
      return [clamp(ax, 0.05, 0.95), clamp(ay, 0.05, 0.95)];
    }
    return [0.5, 0.5];
  }

  function hexToCIColor(hex, alphaOverride) {
    if (root.TripStyleEngine && root.TripStyleEngine.hexToCIColor) {
      return root.TripStyleEngine.hexToCIColor(hex, alphaOverride);
    }
    return $.CIColor.colorWithRedGreenBlueAlpha(0.06, 0.06, 0.08, alphaOverride !== undefined ? alphaOverride : 1.0);
  }

  function hexToNSColor(hex, alphaOverride) {
    if (root.TripStyleEngine && root.TripStyleEngine.hexToNSColor) {
      return root.TripStyleEngine.hexToNSColor(hex, alphaOverride);
    }
    return $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.06, 0.06, 0.08, alphaOverride !== undefined ? alphaOverride : 1.0);
  }

  function resolveFont(spec, size, weight) {
    if (root.TripStyleEngine && root.TripStyleEngine.resolveFont) {
      return root.TripStyleEngine.resolveFont(spec, size, weight);
    }
    return $.NSFont.monospacedSystemFontOfSizeWeight(size, weight || $.NSFontWeightRegular);
  }

  function makeSolidCI(hex, width, height, alpha) {
    return $.CIImage.imageWithColor(hexToCIColor(hex, alpha !== undefined ? alpha : 1.0))
      .imageByCroppingToRect($.CGRectMake(0, 0, width, height));
  }

  function aspectFillCrop(ciImg, targetW, targetH, anchor) {
    const img = normalizeOrigin(ciImg);
    const ext = img.extent;
    const srcW = Math.max(1, ext.size.width);
    const srcH = Math.max(1, ext.size.height);
    const a = normalizeAnchor(anchor);
    const scale = Math.max(targetW / srcW, targetH / srcH);
    let out = img.imageByApplyingTransform($.CGAffineTransformMakeScale(scale, scale));
    const scaledW = srcW * scale;
    const scaledH = srcH * scale;
    const cropX = Math.max(0, Math.min(scaledW - targetW, (scaledW - targetW) * a[0]));
    // EDL anchor y=0 is top, CoreImage y=0 is bottom
    const cropY = Math.max(0, Math.min(scaledH - targetH, (scaledH - targetH) * (1.0 - a[1])));
    out = out.imageByCroppingToRect($.CGRectMake(cropX, cropY, targetW, targetH));
    return out.imageByApplyingTransform($.CGAffineTransformMakeTranslation(-cropX, -cropY));
  }

  function aspectFitMetrics(srcW, srcH, targetW, targetH, zoomFactor) {
    const z = zoomFactor || 1.0;
    const scale = Math.min(targetW / Math.max(1, srcW), targetH / Math.max(1, srcH)) * z;
    const drawW = srcW * scale;
    const drawH = srcH * scale;
    const dx = (targetW - drawW) * 0.5;
    const dy = (targetH - drawH) * 0.5;
    return { scale: scale, w: drawW, h: drawH, x: dx, y: dy };
  }

  function aspectFitCentered(ciImg, targetW, targetH, zoomFactor) {
    const img = normalizeOrigin(ciImg);
    const ext = img.extent;
    const m = aspectFitMetrics(ext.size.width, ext.size.height, targetW, targetH, zoomFactor);
    let out = img.imageByApplyingTransform($.CGAffineTransformMakeScale(m.scale, m.scale));
    out = out.imageByApplyingTransform($.CGAffineTransformMakeTranslation(m.x, m.y));
    return { ci: out.imageByCroppingToRect($.CGRectMake(0, 0, targetW, targetH)), metrics: m };
  }

  function clipRoundedCI(ciImg, w, h, radius) {
    const r = Number(radius || 0);
    const rect = $.CGRectMake(0, 0, w, h);
    if (r <= 0.5) return ciImg.imageByCroppingToRect(rect);

    const rr = $.CIFilter.filterWithName("CIRoundedRectangleGenerator");
    const bm = $.CIFilter.filterWithName("CIBlendWithAlphaMask");
    if (!rr || !bm) return ciImg.imageByCroppingToRect(rect);

    rr.setValueForKey($.CIVector.vectorWithXYZW(0, 0, w, h), "inputExtent");
    rr.setValueForKey(r, "inputRadius");
    rr.setValueForKey($.CIColor.colorWithRedGreenBlueAlpha(1, 1, 1, 1), "inputColor");
    const mask = rr.outputImage.imageByCroppingToRect(rect);

    const clearBg = $.CIImage.imageWithColor($.CIColor.colorWithRedGreenBlueAlpha(0, 0, 0, 0))
      .imageByCroppingToRect(rect);
    bm.setValueForKey(ciImg.imageByCroppingToRect(rect), "inputImage");
    bm.setValueForKey(clearBg, "inputBackgroundImage");
    bm.setValueForKey(mask, "inputMaskImage");
    return bm.outputImage.imageByCroppingToRect(rect);
  }

  function makeDropShadowCI(rectX, rectY, rectW, rectH, radius, blurSigma, offsetX, offsetY, alpha, canvasW, canvasH) {
    const canvasRect = $.CGRectMake(0, 0, canvasW, canvasH);
    const rr = $.CIFilter.filterWithName("CIRoundedRectangleGenerator");
    const blur = $.CIFilter.filterWithName("CIGaussianBlur");
    if (!rr || !blur) return null;

    rr.setValueForKey($.CIVector.vectorWithXYZW(rectX + offsetX, rectY + offsetY, rectW, rectH), "inputExtent");
    rr.setValueForKey(Math.max(0, radius || 0), "inputRadius");
    rr.setValueForKey($.CIColor.colorWithRedGreenBlueAlpha(0, 0, 0, clamp(alpha || 0.48, 0, 0.9)), "inputColor");

    blur.setValueForKey(rr.outputImage, "inputImage");
    blur.setValueForKey(blurSigma || 24.0, "inputRadius");
    return blur.outputImage.imageByCroppingToRect(canvasRect);
  }

  function applyImageAlpha(ciImg, alpha) {
    const a = clamp(Number(alpha), 0.0, 1.0);
    if (a >= 0.995) return ciImg;
    const mat = $.CIFilter.filterWithName("CIColorMatrix");
    if (!mat) return ciImg;
    mat.setValueForKey(ciImg, "inputImage");
    mat.setValueForKey($.CIVector.vectorWithXYZW(1, 0, 0, 0), "inputRVector");
    mat.setValueForKey($.CIVector.vectorWithXYZW(0, 1, 0, 0), "inputGVector");
    mat.setValueForKey($.CIVector.vectorWithXYZW(0, 0, 1, 0), "inputBVector");
    mat.setValueForKey($.CIVector.vectorWithXYZW(0, 0, 0, a), "inputAVector");
    mat.setValueForKey($.CIVector.vectorWithXYZW(0, 0, 0, 0), "inputBiasVector");
    return mat.outputImage;
  }

  function nsImageToCI(nsImg, width, height) {
    const rep = $.NSBitmapImageRep.imageRepWithData(nsImg.TIFFRepresentation);
    let ci = $.CIImage.imageWithCGImage(rep.CGImage);
    const ext = ci.extent;
    if (ext.size.width !== width || ext.size.height !== height) {
      ci = ci.imageByApplyingTransform($.CGAffineTransformMakeScale(width / ext.size.width, height / ext.size.height));
    }
    return ci.imageByCroppingToRect($.CGRectMake(0, 0, width, height));
  }

  function makeTextAttrs(font, color, shadow) {
    const d = $.NSMutableDictionary.dictionary;
    if (font) d.setObjectForKey(font, $.NSFontAttributeName);
    if (color) d.setObjectForKey(color, $.NSForegroundColorAttributeName);
    if (shadow) d.setObjectForKey(shadow, $.NSShadowAttributeName);
    return d;
  }

  // Exported so the video decoder can hardware-downscale each live video tile to its exact pixel box
  function computeTileBoxes(layoutName, tileCount, width, height, style) {
    const lay = (style && style.layouts) ? style.layouts : {};
    const gutter = Number(lay.gutter_px !== undefined ? lay.gutter_px : 12);
    const border = Number(lay.border_px || 0);
    const isPortraitCanvas = height > width;

    if (layoutName === "split_diptych_pair" || layoutName === "split_2up_video") {
      const pad = border > 0 && style && style.textures && style.textures.frame_style === "postcard_matte" ? border : 0;
      const innerW = width - 2 * pad;
      const innerH = height - 2 * pad;
      if (isPortraitCanvas) {
        const halfH = Math.floor((innerH - gutter) / 2);
        return [
          { x: pad, y: pad + halfH + gutter, w: innerW, h: halfH },
          { x: pad, y: pad, w: innerW, h: halfH },
        ];
      }
      const halfW = Math.floor((innerW - gutter) / 2);
      return [
        { x: pad, y: pad, w: halfW, h: innerH },
        { x: pad + halfW + gutter, y: pad, w: innerW - halfW - gutter, h: innerH },
      ];
    }

    if (layoutName === "triptych_3up_video") {
      const pad = 0;
      if (isPortraitCanvas) {
        const tileH = Math.floor((height - 2 * gutter) / 3);
        return [
          { x: 0, y: 2 * (tileH + gutter), w: width, h: height - 2 * (tileH + gutter) },
          { x: 0, y: tileH + gutter, w: width, h: tileH },
          { x: 0, y: 0, w: width, h: tileH },
        ];
      }
      const tileW = Math.floor((width - 2 * gutter) / 3);
      return [
        { x: pad, y: 0, w: tileW, h: height },
        { x: pad + tileW + gutter, y: 0, w: tileW, h: height },
        { x: pad + 2 * (tileW + gutter), y: 0, w: width - 2 * (tileW + gutter), h: height },
      ];
    }

    if (layoutName === "bento_living_grid") {
      const pad = Math.round(Math.min(width, height) * 0.028); // ~30px
      const g = Math.max(12, gutter);
      const innerW = width - 2 * pad;
      const innerH = height - 2 * pad;
      const count = Math.max(2, Math.min(4, tileCount || 3));

      if (isPortraitCanvas) {
        if (count === 2) {
          const topH = Math.round((innerH - g) * 0.58);
          const botH = innerH - g - topH;
          return [
            { x: pad, y: pad + botH + g, w: innerW, h: topH },
            { x: pad, y: pad, w: innerW, h: botH },
          ];
        }
        const topH = Math.round((innerH - g) * 0.56);
        const botH = innerH - g - topH;
        const halfW = Math.floor((innerW - g) / 2);
        return [
          { x: pad, y: pad + botH + g, w: innerW, h: topH },
          { x: pad, y: pad, w: halfW, h: botH },
          { x: pad + halfW + g, y: pad, w: innerW - halfW - g, h: botH },
        ];
      }

      if (count === 2) {
        const leftW = Math.round((innerW - g) * 0.60);
        const rightW = innerW - g - leftW;
        return [
          { x: pad, y: pad, w: leftW, h: innerH },
          { x: pad + leftW + g, y: pad, w: rightW, h: innerH },
        ];
      }
      if (count === 4) {
        const leftW = Math.round((innerW - g) * 0.54);
        const rightW = innerW - g - leftW;
        const halfH = Math.floor((innerH - g) / 2);
        const halfRightW = Math.floor((rightW - g) / 2);
        return [
          { x: pad, y: pad, w: leftW, h: innerH },
          { x: pad + leftW + g, y: pad + halfH + g, w: rightW, h: innerH - halfH - g },
          { x: pad + leftW + g, y: pad, w: halfRightW, h: halfH },
          { x: pad + leftW + g + halfRightW + g, y: pad, w: rightW - halfRightW - g, h: halfH },
        ];
      }
      // 3-tile hero bento (62% hero left + 2 stacked tiles on right)
      const heroW = Math.round((innerW - g) * 0.62);
      const rightW = innerW - g - heroW;
      const halfH = Math.floor((innerH - g) / 2);
      return [
        { x: pad, y: pad, w: heroW, h: innerH },
        { x: pad + heroW + g, y: pad + halfH + g, w: rightW, h: innerH - halfH - g },
        { x: pad + heroW + g, y: pad, w: rightW, h: halfH },
      ];
    }

    if (layoutName === "polaroid_stack") {
      const count = Math.max(1, Math.min(3, tileCount || 1));
      const boxW = Math.round(width * (count === 1 ? 0.52 : count === 2 ? 0.38 : 0.30));
      const boxH = Math.round(height * (count === 1 ? 0.72 : 0.64));
      const boxes = [];
      for (let i = 0; i < count; i++) boxes.push({ x: 0, y: 0, w: boxW, h: boxH });
      return boxes;
    }

    if (layoutName === "super8_frame") {
      const gateH = Math.round(height * 0.88);
      const gateW = Math.min(Math.round(width * 0.86), Math.round(gateH * (4.0 / 3.0)));
      return [{ x: Math.round((width - gateW) / 2), y: Math.round((height - gateH) / 2), w: gateW, h: gateH }];
    }

    return [{ x: 0, y: 0, w: width, h: height }];
  }

  // 1. SINGLE (Full-bleed video / Ken Burns still / optional Wes postcard matte)
  function renderKenBurnsFrame(ciImg, progress, anchor, dir, width, height, style) {
    const a = normalizeAnchor(anchor);
    const p = clamp(Number(progress) || 0.0, 0.0, 1.0);
    const preferStatic = Boolean(style && style.pacing_rules && style.pacing_rules.prefer_static_frames);
    const mode = String(dir || "in").toLowerCase();

    let zoom = 1.0;
    let fx = a[0];
    let fy = a[1];

    if (mode === "static" || mode === "static_locked") {
      zoom = 1.0;
    } else if (preferStatic && mode !== "snap_zoom") {
      // Subtle locked-off postcard feel for wes_postcard (barely perceptible breathing)
      zoom = 1.0 + 0.025 * p;
    } else if (mode === "out" || mode === "ken_burns_out") {
      zoom = 1.13 - 0.13 * p;
    } else if (mode === "pan_left") {
      zoom = 1.12;
      fx = clamp(0.68 - 0.36 * p, 0.05, 0.95);
    } else if (mode === "pan_right") {
      zoom = 1.12;
      fx = clamp(0.32 + 0.36 * p, 0.05, 0.95);
    } else if (mode === "snap_zoom") {
      zoom = p < 0.35 ? 1.0 : (p < 0.70 ? 1.08 : 1.14);
    } else {
      zoom = 1.0 + 0.13 * p;
    }

    const img = normalizeOrigin(ciImg);
    const ext = img.extent;
    const baseScale = Math.max(width / Math.max(1, ext.size.width), height / Math.max(1, ext.size.height)) * zoom;
    let scaled = img.imageByApplyingTransform($.CGAffineTransformMakeScale(baseScale, baseScale));
    const sW = ext.size.width * baseScale;
    const sH = ext.size.height * baseScale;
    const cropX = Math.max(0, Math.min(sW - width, (sW - width) * fx));
    const cropY = Math.max(0, Math.min(sH - height, (sH - height) * (1.0 - fy)));
    scaled = scaled.imageByCroppingToRect($.CGRectMake(cropX, cropY, width, height));
    return scaled.imageByApplyingTransform($.CGAffineTransformMakeTranslation(-cropX, -cropY));
  }

  function renderSingle(sources, opts) {
    const src = sources[0];
    const width = opts.width;
    const height = opts.height;
    const style = opts.style || {};
    const shot = opts.shot || {};
    const dir = shot.ken_burns_dir || (shot.motion_type === "ken_burns_out" ? "out" : shot.motion_type === "static_locked" ? "static" : "in");

    const frameStyle = style.textures && style.textures.frame_style;
    const borderPx = Number((style.layouts && style.layouts.border_px) || 0);
    const hasPostcardMatte = frameStyle === "postcard_matte" && borderPx > 4;

    if (hasPostcardMatte) {
      const innerW = width - 2 * borderPx;
      const innerH = height - 2 * borderPx;
      const inner = src.isVideo
        ? aspectFillCrop(src.ciImg, innerW, innerH, src.anchor)
        : renderKenBurnsFrame(src.ciImg, opts.progress, src.anchor, dir, innerW, innerH, style);
      const matte = makeSolidCI((style.palette && style.palette.border) || "#FAF6EE", width, height, 1.0);
      const placed = inner.imageByApplyingTransform($.CGAffineTransformMakeTranslation(borderPx, borderPx));
      return placed.imageByCompositingOverImage(matte).imageByCroppingToRect($.CGRectMake(0, 0, width, height));
    }

    if (src.isVideo) {
      return aspectFillCrop(src.ciImg, width, height, src.anchor);
    }
    return renderKenBurnsFrame(src.ciImg, opts.progress, src.anchor, dir, width, height, style);
  }

  // 2. BLURRED_BG_PILLARBOX (edge-clamped blurred backdrop dimmed ~25% + drop shadow behind centered 9:16 subject)
  function renderBlurredBgPillarbox(sources, opts) {
    const src = sources[0];
    const width = opts.width;
    const height = opts.height;
    const rect = $.CGRectMake(0, 0, width, height);
    const lay = (opts.style && opts.style.layouts) ? opts.style.layouts : {};
    const blurSigma = Number(lay.pillarbox_blur_sigma || 28.0);
    const dimAlpha = clamp(Number(lay.pillarbox_dim_alpha !== undefined ? lay.pillarbox_dim_alpha : 0.26), 0.10, 0.65);

    // Edge-clamped full-bleed blurred backdrop
    const bgFill = aspectFillCrop(src.ciImg, width, height, src.anchor);
    const blur = $.CIFilter.filterWithName("CIGaussianBlur");
    blur.setValueForKey(bgFill.imageByClampingToExtent, "inputImage");
    blur.setValueForKey(blurSigma, "inputRadius");
    let bg = blur.outputImage.imageByCroppingToRect(rect);

    // Slightly desaturate & dim backdrop by ~25% so focus stays strictly locked on subject
    const bgCtrl = $.CIFilter.filterWithName("CIColorControls");
    if (bgCtrl) {
      bgCtrl.setValueForKey(bg, "inputImage");
      bgCtrl.setValueForKey(0.84, "inputSaturation");
      bgCtrl.setValueForKey(-dimAlpha * 0.45, "inputBrightness");
      bgCtrl.setValueForKey(0.95, "inputContrast");
      bg = bgCtrl.outputImage.imageByCroppingToRect(rect);
    }
    const scrim = $.CIImage.imageWithColor($.CIColor.colorWithRedGreenBlueAlpha(0.03, 0.03, 0.04, dimAlpha))
      .imageByCroppingToRect(rect);
    bg = scrim.imageByCompositingOverImage(bg).imageByCroppingToRect(rect);

    // Centered foreground (gentle 3% drift on still, crisp 1.0x on live video)
    const gentleZoom = src.isVideo ? 1.0 : (1.0 + 0.035 * clamp(opts.progress || 0, 0, 1));
    const fgRes = aspectFitCentered(src.ciImg, width, height, gentleZoom);
    const m = fgRes.metrics;

    // Soft drop shadow behind centered portrait/landscape pillarboxed frame
    const shadowCI = makeDropShadowCI(m.x, m.y, m.w, m.h, 4, 28.0, 0, -4, 0.55, width, height);
    if (shadowCI) {
      bg = shadowCI.imageByCompositingOverImage(bg).imageByCroppingToRect(rect);
    }

    return fgRes.ci.imageByCompositingOverImage(bg).imageByCroppingToRect(rect);
  }

  // 3. SPLIT_DIPTYCH_PAIR & SPLIT_2UP_VIDEO
  function renderSplit2Up(sources, opts) {
    const width = opts.width;
    const height = opts.height;
    const rect = $.CGRectMake(0, 0, width, height);
    const style = opts.style || {};
    const pal = style.palette || {};
    const lay = style.layouts || {};
    const radius = Number(lay.corner_radius_px || 0);

    const srcA = sources[0];
    const srcB = sources[1] || sources[0];
    const boxes = computeTileBoxes("split_2up_video", 2, width, height, style);
    const bgHex = (style.textures && style.textures.frame_style === "postcard_matte") ? (pal.border || "#FAF6EE") : (pal.bg || "#0F1115");
    let canvas = makeSolidCI(bgHex, width, height, 1.0);

    const pair = [srcA, srcB];
    for (let i = 0; i < 2; i++) {
      const b = boxes[i];
      const s = pair[i];
      let tile = s.isVideo
        ? aspectFillCrop(s.ciImg, b.w, b.h, s.anchor)
        : renderKenBurnsFrame(s.ciImg, (opts.progress || 0) * 0.45, s.anchor, i === 0 ? "in" : "out", b.w, b.h, style);
      if (radius > 0) tile = clipRoundedCI(tile, b.w, b.h, radius);
      tile = tile.imageByApplyingTransform($.CGAffineTransformMakeTranslation(b.x, b.y));
      canvas = tile.imageByCompositingOverImage(canvas);
    }

    return canvas.imageByCroppingToRect(rect);
  }

  // 4. TRIPTYCH_3UP_VIDEO (3 concurrent live vertical videos side-by-side in 16:9)
  function renderTriptych3Up(sources, opts) {
    const width = opts.width;
    const height = opts.height;
    const rect = $.CGRectMake(0, 0, width, height);
    const style = opts.style || {};
    const pal = style.palette || {};
    const lay = style.layouts || {};
    const radius = Number(lay.corner_radius_px || 0);
    const prog = clamp(opts.progress || 0, 0, 1);

    const boxes = computeTileBoxes("triptych_3up_video", 3, width, height, style);
    let canvas = makeSolidCI(pal.bg || "#0B0D10", width, height, 1.0);

    for (let i = 0; i < 3; i++) {
      const s = sources[i] || sources[sources.length - 1];
      const b = boxes[i];
      let tile = s.isVideo
        ? aspectFillCrop(s.ciImg, b.w, b.h, s.anchor)
        : renderKenBurnsFrame(s.ciImg, prog * 0.5, s.anchor, i % 2 === 0 ? "in" : "out", b.w, b.h, style);
      if (radius > 0) tile = clipRoundedCI(tile, b.w, b.h, radius);

      // Subtle snappy staggered pop-in on first ~14% of shot (optional, defaults on unless disabled)
      const stagger = (opts.shot && opts.shot.stagger_tiles === false) ? 1.0 : easeOutCubic((prog - i * 0.045) / 0.12);
      if (stagger < 0.995) {
        const slideY = (1.0 - stagger) * -36.0;
        tile = applyImageAlpha(tile, stagger);
        tile = tile.imageByApplyingTransform($.CGAffineTransformMakeTranslation(b.x, b.y + slideY));
      } else {
        tile = tile.imageByApplyingTransform($.CGAffineTransformMakeTranslation(b.x, b.y));
      }
      canvas = tile.imageByCompositingOverImage(canvas);
    }
    return canvas.imageByCroppingToRect(rect);
  }

  // 5. BENTO_LIVING_GRID (asymmetric 2/3/4-tile rounded-rect 16px grid with beat-staggered tile pop-ins)
  function renderBentoLivingGrid(sources, opts) {
    const width = opts.width;
    const height = opts.height;
    const rect = $.CGRectMake(0, 0, width, height);
    const style = opts.style || {};
    const pal = style.palette || {};
    const lay = style.layouts || {};
    const radius = Math.max(12, Number(lay.corner_radius_px || 16));
    const prog = clamp(opts.progress || 0, 0, 1);

    const count = Math.max(2, Math.min(4, sources.length));
    const boxes = computeTileBoxes("bento_living_grid", count, width, height, style);
    let canvas = makeSolidCI(pal.bg || "#F4F1EA", width, height, 1.0);

    // Draw subtle 1px hairline tile borders & micro EXIF captions onto an AppKit overlay
    const borderPx = Number(lay.border_px !== undefined ? lay.border_px : 1);
    const hasCaptions = sources.some((s) => Boolean(s.caption || s.dateLabel));

    for (let i = 0; i < count; i++) {
      const s = sources[i] || sources[sources.length - 1];
      const b = boxes[i];
      const enterStart = i * 0.10;
      const pop = easeOutCubic((prog - enterStart) / 0.18);
      if (pop <= 0.01) continue;

      let tile = s.isVideo
        ? aspectFillCrop(s.ciImg, b.w, b.h, s.anchor)
        : renderKenBurnsFrame(s.ciImg, prog * 0.55, s.anchor, i === 0 ? "in" : "out", b.w, b.h, style);
      tile = clipRoundedCI(tile, b.w, b.h, radius);

      // Beat-staggered scale & subtle rise into final box
      const sc = 0.93 + 0.07 * pop;
      const cx = b.x + b.w * 0.5;
      const cy = b.y + b.h * 0.5;
      const yShift = (1.0 - pop) * -22.0;

      // Subtle shadow behind Bento card
      const sh = makeDropShadowCI(b.x, b.y + yShift, b.w, b.h, radius, 14.0, 0, -4, 0.16 * pop, width, height);
      if (sh) canvas = sh.imageByCompositingOverImage(canvas);

      const tMat = $.CGAffineTransformMake(sc, 0, 0, sc, cx - b.w * 0.5 * sc, cy - b.h * 0.5 * sc + yShift);
      tile = applyImageAlpha(tile, pop).imageByApplyingTransform(tMat);
      canvas = tile.imageByCompositingOverImage(canvas);
    }

    if (borderPx > 0 || hasCaptions) {
      const ov = $.NSImage.alloc.initWithSize($.NSMakeSize(width, height));
      ov.lockFocus;
      for (let i = 0; i < count; i++) {
        const s = sources[i] || sources[sources.length - 1];
        const b = boxes[i];
        const pop = easeOutCubic((prog - i * 0.10) / 0.18);
        if (pop <= 0.05) continue;

        if (borderPx > 0) {
          const bp = $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius(
            $.NSMakeRect(b.x, b.y, b.w, b.h),
            radius,
            radius
          );
          bp.lineWidth = Math.max(1.0, Math.min(2.5, borderPx));
          hexToNSColor(pal.border || "#D2CBBF", 0.75 * pop).setStroke;
          bp.stroke;
        }

        const capText = s.caption || s.dateLabel || "";
        if (capText) {
          const metaFont = resolveFont((style.typography && style.typography.accent_font) || "Menlo-Regular", 15, $.NSFontWeightMedium);
          const attrs = makeTextAttrs(metaFont, $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.98, 0.97, 0.94, pop), null);
          const sz = $(capText).sizeWithAttributes(attrs);
          const pillW = sz.width + 22;
          const pillH = 28;
          const pill = $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius(
            $.NSMakeRect(b.x + 16, b.y + 16, pillW, pillH),
            6,
            6
          );
          $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.08, 0.08, 0.09, 0.72 * pop).setFill;
          pill.fill;
          $(capText).drawAtPointWithAttributes($.NSMakePoint(b.x + 27, b.y + 21), attrs);
        }
      }
      ov.unlockFocus;
      canvas = nsImageToCI(ov, width, height).imageByCompositingOverImage(canvas);
    }

    return canvas.imageByCroppingToRect(rect);
  }

  // 6. POLAROID_STACK (kraft paper backdrop + 1-3 tilted Polaroids + shadows + masking tape + spring drop + EXIF captions)
  function renderPolaroidStack(sources, opts) {
    const width = opts.width;
    const height = opts.height;
    const rect = $.CGRectMake(0, 0, width, height);
    const style = opts.style || {};
    const pal = style.palette || {};
    const lay = style.layouts || {};
    const shot = opts.shot || {};
    const prog = clamp(opts.progress || 0, 0, 1);
    const maxTilt = Number(lay.polaroid_tilt_deg || 5.2);

    // Step 1: Warm kraft / paper backdrop with subtle tactile paper texture
    const paperHex = (pal.bg && root.TripStyleEngine && root.TripStyleEngine.isLightColor(pal.bg))
      ? pal.bg
      : "#F2E9DC";
    let canvas = makeSolidCI(paperHex, width, height, 1.0);

    // Subtle paper fiber grain & warm corner vignette on backdrop
    const rng = $.CIFilter.filterWithName("CIRandomGenerator");
    const mono = $.CIFilter.filterWithName("CIColorControls");
    const mat = $.CIFilter.filterWithName("CIColorMatrix");
    const mult = $.CIFilter.filterWithName("CIMultiplyBlendMode");
    if (rng && mono && mat && mult) {
      let fiber = rng.outputImage.imageByCroppingToRect(rect);
      mono.setValueForKey(fiber, "inputImage");
      mono.setValueForKey(0.0, "inputSaturation");
      mono.setValueForKey(0.35, "inputContrast");
      mono.setValueForKey(0.95, "inputBrightness");
      fiber = mono.outputImage.imageByCroppingToRect(rect);
      mult.setValueForKey(fiber, "inputImage");
      mult.setValueForKey(canvas, "inputBackgroundImage");
      canvas = mult.outputImage.imageByCroppingToRect(rect);
    }

    const count = Math.max(1, Math.min(3, sources.length));
    const isPortraitCanvas = height > width;
    const cardConfigs = [];

    if (count === 1) {
      const cw = Math.round(Math.min(width * 0.46, height * 0.66));
      const ch = Math.round(cw * 1.18);
      cardConfigs.push({ cx: width * 0.50, cy: height * 0.50, w: cw, h: ch, tiltDeg: -maxTilt * 0.55, delay: 0.0 });
    } else if (count === 2) {
      if (isPortraitCanvas) {
        const cw = Math.round(width * 0.68);
        const ch = Math.round(cw * 1.16);
        cardConfigs.push({ cx: width * 0.46, cy: height * 0.68, w: cw, h: ch, tiltDeg: -maxTilt * 0.85, delay: 0.0 });
        cardConfigs.push({ cx: width * 0.55, cy: height * 0.30, w: cw, h: ch, tiltDeg: maxTilt * 0.90, delay: 0.20 });
      } else {
        const cw = Math.round(height * 0.60);
        const ch = Math.round(cw * 1.18);
        cardConfigs.push({ cx: width * 0.33, cy: height * 0.51, w: cw, h: ch, tiltDeg: -maxTilt * 0.85, delay: 0.0 });
        cardConfigs.push({ cx: width * 0.67, cy: height * 0.49, w: cw, h: ch, tiltDeg: maxTilt * 0.92, delay: 0.20 });
      }
    } else {
      if (isPortraitCanvas) {
        const cw = Math.round(width * 0.56);
        const ch = Math.round(cw * 1.15);
        cardConfigs.push({ cx: width * 0.44, cy: height * 0.76, w: cw, h: ch, tiltDeg: -maxTilt, delay: 0.0 });
        cardConfigs.push({ cx: width * 0.58, cy: height * 0.50, w: cw, h: ch, tiltDeg: maxTilt * 0.8, delay: 0.18 });
        cardConfigs.push({ cx: width * 0.46, cy: height * 0.24, w: cw, h: ch, tiltDeg: -maxTilt * 0.5, delay: 0.36 });
      } else {
        const cw = Math.round(height * 0.54);
        const ch = Math.round(cw * 1.18);
        cardConfigs.push({ cx: width * 0.24, cy: height * 0.52, w: cw, h: ch, tiltDeg: -maxTilt, delay: 0.0 });
        cardConfigs.push({ cx: width * 0.50, cy: height * 0.48, w: cw, h: ch, tiltDeg: maxTilt * 0.45, delay: 0.18 });
        cardConfigs.push({ cx: width * 0.76, cy: height * 0.52, w: cw, h: ch, tiltDeg: maxTilt * 0.95, delay: 0.36 });
      }
    }

    // Subtle 12fps stop-motion card wobble when overlay_fps_step > 0 (inner video remains smooth!)
    const stepFps = Number((style.textures && style.textures.overlay_fps_step) || 0);
    const wobbleStep = stepFps > 0 ? Math.floor((opts.frameIdx || 0) / Math.max(1, Math.round((opts.fps || 30) / stepFps))) : 0;

    const captionFont = resolveFont((style.typography && style.typography.meta_font) || "Menlo-Regular", 18, $.NSFontWeightMedium);

    for (let i = 0; i < count; i++) {
      const cfg = cardConfigs[i];
      const s = sources[i] || sources[sources.length - 1];
      const localP = clamp((prog - cfg.delay) / 0.32, 0.0, 1.0);
      if (localP <= 0.005) continue;

      const sp = dampedSpring(localP);
      const alpha = easeOutCubic(clamp(localP / 0.25, 0.0, 1.0));
      const scale = 1.18 - 0.18 * sp;
      const dropY = (1.0 - sp) * 140.0;
      const wobbleDeg = stepFps > 0 ? Math.sin(wobbleStep * 1.7 + i * 2.1) * 0.65 : 0.0;
      const rad = ((cfg.tiltDeg + wobbleDeg) * Math.PI) / 180.0;

      const padSide = Math.round(cfg.w * 0.055);
      const padTop = Math.round(cfg.w * 0.055);
      const padBot = Math.round(cfg.h * 0.175); // Classic wide Polaroid caption chin
      const photoW = cfg.w - 2 * padSide;
      const photoH = cfg.h - padTop - padBot;

      // 1) Draw Polaroid matte card + drop shadow + masking tape + EXIF caption in local AppKit canvas (padded for shadow & tape)
      const margin = 56;
      const boxW = cfg.w + margin * 2;
      const boxH = cfg.h + margin * 2;

      const cardImg = $.NSImage.alloc.initWithSize($.NSMakeSize(boxW, boxH));
      cardImg.lockFocus;

      // Card shadow + warm white matte body
      $.NSGraphicsContext.saveGraphicsState;
      const sh = $.NSShadow.alloc.init;
      sh.shadowOffset = $.NSMakeSize(4, -10);
      sh.shadowBlurRadius = 26.0;
      sh.shadowColor = $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.10, 0.08, 0.06, 0.40);
      sh.set;
      const cardRect = $.NSMakeRect(margin, margin, cfg.w, cfg.h);
      const cardPath = $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius(cardRect, 4, 4);
      $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.985, 0.976, 0.955, 1.0).setFill;
      cardPath.fill;
      $.NSGraphicsContext.restoreGraphicsState;

      // Subtle hairline border around matte
      $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.84, 0.80, 0.74, 0.8).setStroke;
      cardPath.lineWidth = 1.2;
      cardPath.stroke;

      // Subtle inner aperture recess
      $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.12, 0.11, 0.10, 1.0).setFill;
      $.NSBezierPath.fillRect($.NSMakeRect(margin + padSide, margin + padBot, photoW, photoH));

      // Monospace EXIF caption on bottom Polaroid chin
      const capStr = s.caption || s.dateLabel || shot.caption || shot.date_stamp || shot.date_label || "";
      if (capStr) {
        const capAttrs = makeTextAttrs(
          captionFont,
          hexToNSColor(pal.text_primary || "#23201D", 0.88),
          null
        );
        const capSize = $(capStr).sizeWithAttributes(capAttrs);
        const cxText = margin + (cfg.w - capSize.width) * 0.5;
        const cyText = margin + (padBot - capSize.height) * 0.5;
        $(capStr).drawAtPointWithAttributes($.NSMakePoint(cxText, cyText), capAttrs);
      }
      cardImg.unlockFocus;

      // 2) Draw translucent masking tape strips in a separate top overlay (so tape sits OVER the photo corners!)
      const tapeImg = $.NSImage.alloc.initWithSize($.NSMakeSize(boxW, boxH));
      tapeImg.lockFocus;
      const tapeAngles = i % 2 === 0 ? [-28, 22] : [-18, 30];
      const tapeAnchors = [
        { x: margin + 22, y: margin + cfg.h - 8 },
        { x: margin + cfg.w - 22, y: margin + cfg.h - 10 },
      ];
      for (let tIdx = 0; tIdx < 2; tIdx++) {
        $.NSGraphicsContext.saveGraphicsState;
        const xf = $.NSAffineTransform.transform;
        xf.translateXByYBy(tapeAnchors[tIdx].x, tapeAnchors[tIdx].y);
        xf.rotateByDegrees(tapeAngles[tIdx]);
        xf.concat;
        const tapeRect = $.NSMakeRect(-44, -14, 88, 28);
        $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.94, 0.90, 0.77, 0.72).setFill;
        $.NSBezierPath.fillRect(tapeRect);
        $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.84, 0.78, 0.64, 0.45).setStroke;
        const tp = $.NSBezierPath.bezierPathWithRect(tapeRect);
        tp.lineWidth = 1.0;
        tp.stroke;
        $.NSGraphicsContext.restoreGraphicsState;
      }
      tapeImg.unlockFocus;

      // 3) Composite live video or photo into the card's photo aperture, then tape on top
      const cardCI = nsImageToCI(cardImg, boxW, boxH);
      const tapeCI = nsImageToCI(tapeImg, boxW, boxH);
      const mediaCI = aspectFillCrop(s.ciImg, photoW, photoH, s.anchor)
        .imageByApplyingTransform($.CGAffineTransformMakeTranslation(margin + padSide, margin + padBot));

      let fullCard = mediaCI.imageByCompositingOverImage(cardCI);
      fullCard = tapeCI.imageByCompositingOverImage(fullCard).imageByCroppingToRect($.CGRectMake(0, 0, boxW, boxH));

      // 4) Center, scale, rotate, and translate onto canvas
      const cosR = Math.cos(rad) * scale;
      const sinR = Math.sin(rad) * scale;
      const halfBW = boxW * 0.5;
      const halfBH = boxH * 0.5;
      const dstX = cfg.cx;
      const dstY = cfg.cy + dropY;
      // Rotate around (halfBW, halfBH) then place at (dstX, dstY)
      const tx = dstX - (cosR * halfBW - sinR * halfBH);
      const ty = dstY - (sinR * halfBW + cosR * halfBH);
      const xform = $.CGAffineTransformMake(cosR, sinR, -sinR, cosR, tx, ty);

      fullCard = applyImageAlpha(fullCard, alpha).imageByApplyingTransform(xform);
      canvas = fullCard.imageByCompositingOverImage(canvas);
    }

    return canvas.imageByCroppingToRect(rect);
  }

  // 7. SUPER8_FRAME (rounded-corner Super 8 matte + subtle sprocket edge + optional corner date stamp)
  function renderSuper8Frame(sources, opts) {
    const src = sources[0];
    const width = opts.width;
    const height = opts.height;
    const rect = $.CGRectMake(0, 0, width, height);
    const style = opts.style || {};
    const shot = opts.shot || {};
    const lay = style.layouts || {};

    // Super 8 4:3 gate aperture inside deep projector black matte (#090807)
    const boxes = computeTileBoxes("super8_frame", 1, width, height, style);
    const gate = boxes[0];
    const cornerR = Math.max(22, Number(lay.corner_radius_px || 32));

    let media = src.isVideo
      ? aspectFillCrop(src.ciImg, gate.w, gate.h, src.anchor)
      : renderKenBurnsFrame(src.ciImg, opts.progress, src.anchor, shot.ken_burns_dir || "in", gate.w, gate.h, style);

    media = clipRoundedCI(media, gate.w, gate.h, cornerR);
    media = media.imageByApplyingTransform($.CGAffineTransformMakeTranslation(gate.x, gate.y));

    let canvas = makeSolidCI("#090807", width, height, 1.0);
    canvas = media.imageByCompositingOverImage(canvas).imageByCroppingToRect(rect);

    // Draw subtle Super 8 sprocket peek on left edge + soft projector gate inner shadow + optional retro date stamp
    const ov = $.NSImage.alloc.initWithSize($.NSMakeSize(width, height));
    ov.lockFocus;

    // Subtle film gate rim
    const gatePath = $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius(
      $.NSMakeRect(gate.x, gate.y, gate.w, gate.h),
      cornerR,
      cornerR
    );
    gatePath.lineWidth = 4.0;
    $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.03, 0.025, 0.02, 0.85).setStroke;
    gatePath.stroke;

    // Authentic Super 8 mid-left sprocket perforation peek (with gentle 18fps projector vertical micro-drift)
    if (gate.x > 40) {
      const stepIdx = Math.floor((opts.frameIdx || 0) / 2);
      const driftY = Math.sin(stepIdx * 2.1) * 3.0;
      const spW = Math.min(44, Math.round(gate.x * 0.42));
      const spH = Math.round(height * 0.13);
      const spX = Math.max(12, Math.round(gate.x * 0.25));
      const spY = Math.round(height * 0.5 - spH * 0.5 + driftY);
      const spPath = $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius($.NSMakeRect(spX, spY, spW, spH), 8, 8);
      $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.88, 0.82, 0.72, 0.14).setFill;
      spPath.fill;
      $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.95, 0.88, 0.76, 0.22).setStroke;
      spPath.lineWidth = 1.2;
      spPath.stroke;
    }

    // Optional retro glowing quartz corner date stamp (e.g. '26 07 04)
    const showDate = (style.cards && style.cards.show_date_stamp) || Boolean(shot.date_stamp || shot.date_label);
    const dateStr = shot.date_stamp || shot.date_label || "";
    if (showDate && dateStr) {
      const dFont = resolveFont("Menlo-Bold", 24, $.NSFontWeightBold);
      const glow = $.NSShadow.alloc.init;
      glow.shadowOffset = $.NSMakeSize(0, 0);
      glow.shadowBlurRadius = 10.0;
      glow.shadowColor = $.NSColor.colorWithCalibratedRedGreenBlueAlpha(1.0, 0.42, 0.10, 0.85);
      const dAttrs = makeTextAttrs(dFont, $.NSColor.colorWithCalibratedRedGreenBlueAlpha(1.0, 0.62, 0.22, 0.92), glow);
      const dSz = $(dateStr).sizeWithAttributes(dAttrs);
      $(dateStr).drawAtPointWithAttributes(
        $.NSMakePoint(gate.x + gate.w - dSz.width - 34, gate.y + 28),
        dAttrs
      );
    }

    ov.unlockFocus;
    canvas = nsImageToCI(ov, width, height).imageByCompositingOverImage(canvas);
    return canvas.imageByCroppingToRect(rect);
  }

  function renderLayoutFrame(layoutName, sources, opts) {
    const name = String(layoutName || "single").toLowerCase().trim();
    if (!sources || sources.length === 0) {
      return makeSolidCI("#0F1115", opts.width, opts.height, 1.0);
    }
    switch (name) {
      case "blurred_bg_pillarbox":
        return renderBlurredBgPillarbox(sources, opts);
      case "split_diptych_pair":
      case "split_2up_video":
        return renderSplit2Up(sources, opts);
      case "triptych_3up_video":
        return renderTriptych3Up(sources, opts);
      case "bento_living_grid":
      case "bento_3up":
      case "bento_4up":
        return renderBentoLivingGrid(sources, opts);
      case "polaroid_stack":
      case "polaroid_single":
        return renderPolaroidStack(sources, opts);
      case "super8_frame":
        return renderSuper8Frame(sources, opts);
      case "single":
      case "full_bleed":
      default:
        return renderSingle(sources, opts);
    }
  }

  const api = {
    normalizeOrigin: normalizeOrigin,
    normalizeAnchor: normalizeAnchor,
    aspectFillCrop: aspectFillCrop,
    aspectFitCentered: aspectFitCentered,
    clipRoundedCI: clipRoundedCI,
    renderKenBurnsFrame: renderKenBurnsFrame,
    computeTileBoxes: computeTileBoxes,
    renderLayoutFrame: renderLayoutFrame,
  };

  if (typeof exports !== "undefined") {
    const ks = Object.keys(api);
    for (let i = 0; i < ks.length; i++) exports[ks[i]] = api[ks[i]];
  }
  root.TripLayouts = api;
})(this);
