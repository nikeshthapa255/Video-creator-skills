/**
 * Trip Cinema Studio v0.2.0 — Live Moving-Buffer Transitions (macOS Native JXA CoreImage).
 *
 * Blends live advancing tail frames of Shot A (`frameA`) with live advancing head frames
 * of Shot B (`frameB`) over `progress` in [0..1] without ever freezing Frame A.
 *
 * Supported transition types:
 *   cut, dissolve, whip_pan_left, whip_pan_right, whip_pan_up, whip_pan_down,
 *   light_leak, film_burn, flash_cut, zoom_punch, paper_wipe
 */

(function (root) {
  "use strict";

  function clamp(v, lo, hi) {
    return Math.min(hi, Math.max(lo, v));
  }

  function smoothstep(t) {
    const c = clamp(t, 0.0, 1.0);
    return c * c * (3.0 - 2.0 * c);
  }

  function easeInOutCubic(t) {
    const c = clamp(t, 0.0, 1.0);
    return c < 0.5 ? 4.0 * c * c * c : 1.0 - Math.pow(-2.0 * c + 2.0, 3.0) / 2.0;
  }

  function easeInCubic(t) {
    const c = clamp(t, 0.0, 1.0);
    return c * c * c;
  }

  function easeOutCubic(t) {
    const c = clamp(t, 0.0, 1.0);
    return 1.0 - Math.pow(1.0 - c, 3.0);
  }

  function normalizeTransitionType(spec) {
    if (!spec) return "cut";
    let raw = typeof spec === "string" ? spec : (spec.type || "cut");
    raw = String(raw).toLowerCase().trim();
    if (raw === "xfade_dissolve" || raw === "cross_dissolve" || raw === "fade") return "dissolve";
    if (raw === "whip_pan") return "whip_pan_left";
    if (raw === "j_cut" || raw === "l_cut" || raw === "none") return "cut";
    return raw;
  }

  function normalizeAnchor(anchor) {
    if (Array.isArray(anchor) && anchor.length >= 2) {
      return [clamp(Number(anchor[0]) || 0.5, 0.05, 0.95), clamp(Number(anchor[1]) || 0.5, 0.05, 0.95)];
    }
    if (anchor && typeof anchor === "object") {
      return [clamp(Number(anchor.x) || 0.5, 0.05, 0.95), clamp(Number(anchor.y) || 0.5, 0.05, 0.95)];
    }
    return [0.5, 0.5];
  }

  function dissolveFrames(frameA, frameB, t, rect) {
    const f = $.CIFilter.filterWithName("CIDissolveTransition");
    if (!f) return t < 0.5 ? frameA : frameB;
    f.setValueForKey(frameA.imageByCroppingToRect(rect), "inputImage");
    f.setValueForKey(frameB.imageByCroppingToRect(rect), "inputTargetImage");
    f.setValueForKey(clamp(t, 0.0, 1.0), "inputTime");
    return f.outputImage.imageByCroppingToRect(rect);
  }

  function whipPanTransition(frameA, frameB, p, direction, width, height) {
    const rect = $.CGRectMake(0, 0, width, height);
    const e = easeInOutCubic(p);
    const blurRadius = 48.0 * Math.sin(Math.PI * clamp(p, 0.0, 1.0));

    const aCrop = frameA.imageByCroppingToRect(rect);
    const bCrop = frameB.imageByCroppingToRect(rect);

    let dxA = 0, dyA = 0, dxB = 0, dyB = 0, angle = 0.0;
    if (direction === "whip_pan_right") {
      dxA = e * width;
      dxB = -(1.0 - e) * width;
      angle = 0.0;
    } else if (direction === "whip_pan_up") {
      dyA = e * height;
      dyB = -(1.0 - e) * height;
      angle = Math.PI * 0.5;
    } else if (direction === "whip_pan_down") {
      dyA = -e * height;
      dyB = (1.0 - e) * height;
      angle = Math.PI * 0.5;
    } else {
      // whip_pan_left (default): B slides in from right, A exits left
      dxA = -e * width;
      dxB = (1.0 - e) * width;
      angle = 0.0;
    }

    const aMoved = aCrop.imageByApplyingTransform($.CGAffineTransformMakeTranslation(dxA, dyA));
    const bMoved = bCrop.imageByApplyingTransform($.CGAffineTransformMakeTranslation(dxB, dyB));
    const combined = bMoved.imageByCompositingOverImage(aMoved).imageByCroppingToRect(rect);

    if (blurRadius < 0.5) return combined;

    // imageByClampingToExtent wraps CIAffineClamp cleanly to prevent black border pull during CIMotionBlur
    const clamped = combined.imageByClampingToExtent;

    const mb = $.CIFilter.filterWithName("CIMotionBlur");
    if (!mb) return combined;
    mb.setValueForKey(clamped, "inputImage");
    mb.setValueForKey(blurRadius, "inputRadius");
    mb.setValueForKey(angle, "inputAngle");
    return mb.outputImage.imageByCroppingToRect(rect);
  }

  function makeWarmLeakGradient(cx, cy, r0, r1, r, g, b, intensity, rect) {
    const grad = $.CIFilter.filterWithName("CIRadialGradient");
    if (!grad) return null;
    grad.setValueForKey($.CIVector.vectorWithXY(cx, cy), "inputCenter");
    grad.setValueForKey(r0, "inputRadius0");
    grad.setValueForKey(r1, "inputRadius1");
    grad.setValueForKey($.CIColor.colorWithRedGreenBlueAlpha(r * intensity, g * intensity, b * intensity, intensity), "inputColor0");
    grad.setValueForKey($.CIColor.colorWithRedGreenBlueAlpha(0.0, 0.0, 0.0, 0.0), "inputColor1");
    return grad.outputImage.imageByCroppingToRect(rect);
  }

  function lightLeakTransition(frameA, frameB, p, isFilmBurn, width, height) {
    const rect = $.CGRectMake(0, 0, width, height);
    const cp = clamp(p, 0.0, 1.0);
    let base = dissolveFrames(frameA, frameB, smoothstep(cp), rect);

    const env = Math.pow(Math.sin(Math.PI * cp), isFilmBurn ? 1.35 : 1.75);
    if (env < 0.01) return base;

    // Primary drifting warm amber/coral lobe
    const cx1 = width * (0.12 + 0.78 * cp);
    const cy1 = height * (0.78 - 0.52 * cp);
    const leak1 = makeWarmLeakGradient(
      cx1,
      cy1,
      Math.min(width, height) * 0.08,
      Math.max(width, height) * (isFilmBurn ? 0.82 : 0.68),
      1.0,
      isFilmBurn ? 0.56 : 0.66,
      isFilmBurn ? 0.16 : 0.24,
      env * (isFilmBurn ? 0.96 : 0.80),
      rect
    );

    if (leak1) {
      const screen = $.CIFilter.filterWithName("CIScreenBlendMode");
      if (screen) {
        screen.setValueForKey(leak1, "inputImage");
        screen.setValueForKey(base, "inputBackgroundImage");
        base = screen.outputImage.imageByCroppingToRect(rect);
      }
    }

    // Secondary hot splice core for film_burn (and peak flare for light_leak)
    const peakEnv = Math.pow(Math.sin(Math.PI * cp), 2.4);
    if (peakEnv > 0.03) {
      const cx2 = width * (0.88 - 0.65 * cp);
      const cy2 = height * (0.22 + 0.55 * cp);
      const hotCore = makeWarmLeakGradient(
        cx2,
        cy2,
        Math.min(width, height) * 0.04,
        Math.max(width, height) * (isFilmBurn ? 0.55 : 0.42),
        1.0,
        isFilmBurn ? 0.34 : 0.48,
        0.10,
        peakEnv * (isFilmBurn ? 0.85 : 0.45),
        rect
      );
      if (hotCore) {
        const addF = $.CIFilter.filterWithName("CIAdditionCompositing");
        if (addF) {
          addF.setValueForKey(hotCore, "inputImage");
          addF.setValueForKey(base, "inputBackgroundImage");
          base = addF.outputImage.imageByCroppingToRect(rect);
        }
      }
    }

    if (isFilmBurn && peakEnv > 0.05) {
      const expF = $.CIFilter.filterWithName("CIExposureAdjust");
      if (expF) {
        expF.setValueForKey(base, "inputImage");
        expF.setValueForKey(0.65 * peakEnv, "inputEV");
        base = expF.outputImage.imageByCroppingToRect(rect);
      }
    }

    return base.imageByCroppingToRect(rect);
  }

  function flashCutTransition(frameA, frameB, p, width, height) {
    const rect = $.CGRectMake(0, 0, width, height);
    const cp = clamp(p, 0.0, 1.0);
    let base = (cp < 0.5 ? frameA : frameB).imageByCroppingToRect(rect);

    // Sharp ~4-frame camera shutter flash envelope peaking at midpoint
    const flash = Math.pow(Math.sin(Math.PI * cp), 2.2);
    if (flash > 0.02) {
      const expF = $.CIFilter.filterWithName("CIExposureAdjust");
      if (expF) {
        expF.setValueForKey(base, "inputImage");
        expF.setValueForKey(3.0 * flash, "inputEV");
        base = expF.outputImage.imageByCroppingToRect(rect);
      }
      const whiteVeil = $.CIImage.imageWithColor(
        $.CIColor.colorWithRedGreenBlueAlpha(1.0, 0.99, 0.96, clamp(flash * 0.85, 0.0, 0.92))
      ).imageByCroppingToRect(rect);
      base = whiteVeil.imageByCompositingOverImage(base).imageByCroppingToRect(rect);
    }
    return base;
  }

  function scaleAroundAnchor(ciImg, scale, anchor, width, height) {
    const rect = $.CGRectMake(0, 0, width, height);
    const ax = anchor[0] * width;
    const ay = (1.0 - anchor[1]) * height; // CoreImage Y-up
    const clamped = ciImg.imageByCroppingToRect(rect).imageByClampingToExtent;
    const tx = ax * (1.0 - scale);
    const ty = ay * (1.0 - scale);
    const t = $.CGAffineTransformMake(scale, 0, 0, scale, tx, ty);
    return clamped.imageByApplyingTransform(t).imageByCroppingToRect(rect);
  }

  function zoomPunchTransition(frameA, frameB, p, spec, width, height) {
    const rect = $.CGRectMake(0, 0, width, height);
    const cp = clamp(p, 0.0, 1.0);
    const anchor = normalizeAnchor(spec && spec.focal_anchor);

    // Outgoing zooms IN (1.0 -> 1.24), Incoming zooms IN smoothly (0.88 -> 1.0)
    const uA = clamp(cp / 0.58, 0.0, 1.0);
    const scaleA = 1.0 + 0.24 * easeInCubic(uA);
    let imgA = scaleAroundAnchor(frameA, scaleA, anchor, width, height);

    const uB = clamp((cp - 0.42) / 0.58, 0.0, 1.0);
    const scaleB = 0.88 + 0.12 * easeOutCubic(uB);
    let imgB = scaleAroundAnchor(frameB, scaleB, anchor, width, height);

    // Subtle radial zoom blur peaking at the splice midpoint
    const blurAmt = 18.0 * Math.pow(Math.sin(Math.PI * cp), 1.6);
    if (blurAmt > 0.8) {
      const zbA = $.CIFilter.filterWithName("CIZoomBlur");
      const zbB = $.CIFilter.filterWithName("CIZoomBlur");
      const centerVec = $.CIVector.vectorWithXY(anchor[0] * width, (1.0 - anchor[1]) * height);
      if (zbA && cp < 0.65) {
        zbA.setValueForKey(imgA.imageByClampingToExtent, "inputImage");
        zbA.setValueForKey(centerVec, "inputCenter");
        zbA.setValueForKey(blurAmt, "inputAmount");
        imgA = zbA.outputImage.imageByCroppingToRect(rect);
      }
      if (zbB && cp > 0.35) {
        zbB.setValueForKey(imgB.imageByClampingToExtent, "inputImage");
        zbB.setValueForKey(centerVec, "inputCenter");
        zbB.setValueForKey(blurAmt, "inputAmount");
        imgB = zbB.outputImage.imageByCroppingToRect(rect);
      }
    }

    // Splice smoothly across midpoint [0.42 .. 0.58]
    if (cp <= 0.42) return imgA;
    if (cp >= 0.58) return imgB;
    const spliceT = smoothstep((cp - 0.42) / 0.16);
    return dissolveFrames(imgA, imgB, spliceT, rect);
  }

  function paperWipeTransition(frameA, frameB, p, width, height) {
    const rect = $.CGRectMake(0, 0, width, height);
    const cp = clamp(p, 0.0, 1.0);
    // 8fps-feel quantized stop-motion progression (6 tactile steps across a ~0.4-0.5s wipe)
    const steps = 6;
    const qStep = Math.min(steps, Math.floor(cp * steps) + 1);
    const q = qStep / (steps + 0.35);

    const tearX = width * (1.0 - q);
    const numTeeth = 26;
    const stepY = height / numTeeth;

    // Build deterministic jagged mask + crisp torn-paper white deckle edge in AppKit
    const maskCanvas = $.NSImage.alloc.initWithSize($.NSMakeSize(width, height));
    maskCanvas.lockFocus;
    $.NSColor.blackColor.setFill;
    $.NSBezierPath.fillRect($.NSMakeRect(0, 0, width, height));

    const tearPath = $.NSBezierPath.bezierPath;
    tearPath.moveToPoint($.NSMakePoint(width, 0));
    tearPath.lineToPoint($.NSMakePoint(width, height));
    const edgePts = [];
    for (let i = numTeeth; i >= 0; i--) {
      const y = i * stepY;
      // Deterministic jagged organic paper tear offset
      const jag = Math.sin(i * 2.7 + qStep * 1.9) * 18.0 + Math.cos(i * 5.3 + qStep * 0.7) * 9.0;
      const x = clamp(tearX + jag, -30, width + 30);
      edgePts.push({ x: x, y: y });
      tearPath.lineToPoint($.NSMakePoint(x, y));
    }
    tearPath.closePath;
    $.NSColor.whiteColor.setFill;
    tearPath.fill;
    maskCanvas.unlockFocus;

    const maskRep = $.NSBitmapImageRep.imageRepWithData(maskCanvas.TIFFRepresentation);
    let maskCI = $.CIImage.imageWithCGImage(maskRep.CGImage);
    const mExt = maskCI.extent;
    if (mExt.size.width !== width || mExt.size.height !== height) {
      maskCI = maskCI.imageByApplyingTransform($.CGAffineTransformMakeScale(width / mExt.size.width, height / mExt.size.height));
    }

    const blend = $.CIFilter.filterWithName("CIBlendWithMask");
    let out = frameB;
    if (blend) {
      blend.setValueForKey(frameB.imageByCroppingToRect(rect), "inputImage");
      blend.setValueForKey(frameA.imageByCroppingToRect(rect), "inputBackgroundImage");
      blend.setValueForKey(maskCI.imageByCroppingToRect(rect), "inputMaskImage");
      out = blend.outputImage.imageByCroppingToRect(rect);
    }

    // Draw crisp white torn-paper deckle border & subtle shadow along tear seam
    const rimCanvas = $.NSImage.alloc.initWithSize($.NSMakeSize(width, height));
    rimCanvas.lockFocus;
    const rimPath = $.NSBezierPath.bezierPath;
    for (let i = 0; i < edgePts.length; i++) {
      const pt = $.NSMakePoint(edgePts[i].x, edgePts[i].y);
      if (i === 0) rimPath.moveToPoint(pt);
      else rimPath.lineToPoint(pt);
    }
    const sh = $.NSShadow.alloc.init;
    sh.shadowOffset = $.NSMakeSize(-6, 0);
    sh.shadowBlurRadius = 14.0;
    sh.shadowColor = $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.0, 0.0, 0.0, 0.55);
    sh.set;
    rimPath.lineWidth = 14.0;
    rimPath.lineJoinStyle = $.NSLineJoinStyleBevel;
    $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.96, 0.94, 0.89, 0.96).setStroke;
    rimPath.stroke;
    rimCanvas.unlockFocus;

    const rimRep = $.NSBitmapImageRep.imageRepWithData(rimCanvas.TIFFRepresentation);
    let rimCI = $.CIImage.imageWithCGImage(rimRep.CGImage);
    const rExt = rimCI.extent;
    if (rExt.size.width !== width || rExt.size.height !== height) {
      rimCI = rimCI.imageByApplyingTransform($.CGAffineTransformMakeScale(width / rExt.size.width, height / rExt.size.height));
    }

    return rimCI.imageByCompositingOverImage(out).imageByCroppingToRect(rect);
  }

  function applyTransition(frameA, frameB, progress, transitionSpec, width, height, style) {
    const rect = $.CGRectMake(0, 0, width, height);
    const tType = normalizeTransitionType(transitionSpec);
    const p = clamp(Number(progress) || 0.0, 0.0, 1.0);

    if (!frameA) return frameB ? frameB.imageByCroppingToRect(rect) : null;
    if (!frameB) return frameA.imageByCroppingToRect(rect);

    switch (tType) {
      case "cut":
        return (p < 0.5 ? frameA : frameB).imageByCroppingToRect(rect);
      case "dissolve":
        return dissolveFrames(frameA, frameB, smoothstep(p), rect);
      case "whip_pan_left":
      case "whip_pan_right":
      case "whip_pan_up":
      case "whip_pan_down":
        return whipPanTransition(frameA, frameB, p, tType, width, height);
      case "light_leak":
        return lightLeakTransition(frameA, frameB, p, false, width, height);
      case "film_burn":
        return lightLeakTransition(frameA, frameB, p, true, width, height);
      case "flash_cut":
        return flashCutTransition(frameA, frameB, p, width, height);
      case "zoom_punch":
        return zoomPunchTransition(frameA, frameB, p, transitionSpec, width, height);
      case "paper_wipe":
        return paperWipeTransition(frameA, frameB, p, width, height);
      default:
        return dissolveFrames(frameA, frameB, smoothstep(p), rect);
    }
  }

  const api = {
    normalizeTransitionType: normalizeTransitionType,
    applyTransition: applyTransition,
  };

  if (typeof exports !== "undefined") {
    const ks = Object.keys(api);
    for (let i = 0; i < ks.length; i++) exports[ks[i]] = api[ks[i]];
  }
  root.TripTransitions = api;
})(this);
