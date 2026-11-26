/**
 * Trip Cinema Studio v0.2.0 — Style-Aware Dynamic Graphic Cards (macOS Native JXA).
 *
 * Strictly uses NSMutableDictionary + $.NSFontAttributeName / $.NSForegroundColorAttributeName
 * / $.NSShadowAttributeName (NEVER raw JS string keys!).
 *
 * Supported graphic_type cards:
 *   - chapter_title (all 7 Style Pack variants over blurred slow-zooming backdrop_path)
 *   - text_behind_subject (giant sliding display title occluded by Vision mask_path via CIBlendWithMask,
 *                          with graceful magazine-cover fallback when no mask_path)
 *   - route_map (curved Bezier arcs + rotated travel marker + synced Elevation Profile sparkline,
 *                guaranteed all stop pills revealed before progress = 0.85)
 *   - trip_wrapped (animated EXIF stat counters + palette accents)
 *   - cast_intro (freeze-frame cutout with optional mask_path outline or tilted Polaroid + nickname/role)
 *   - blooper_title (playful post-credits outtakes stinger card)
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

  function easeInOutCubic(t) {
    const c = clamp(t, 0.0, 1.0);
    return c < 0.5 ? 4.0 * c * c * c : 1.0 - Math.pow(-2.0 * c + 2.0, 3.0) / 2.0;
  }

  function dampedSpring(t) {
    const c = clamp(t, 0.0, 1.0);
    if (c <= 0) return 0;
    if (c >= 1) return 1;
    return 1.0 - Math.exp(-6.2 * c) * Math.cos(8.0 * c);
  }

  function hexToNSColor(hex, alphaOverride) {
    if (root.TripStyleEngine && root.TripStyleEngine.hexToNSColor) {
      return root.TripStyleEngine.hexToNSColor(hex, alphaOverride);
    }
    return $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.91, 0.77, 0.56, alphaOverride !== undefined ? alphaOverride : 1.0);
  }

  function hexToCIColor(hex, alphaOverride) {
    if (root.TripStyleEngine && root.TripStyleEngine.hexToCIColor) {
      return root.TripStyleEngine.hexToCIColor(hex, alphaOverride);
    }
    return $.CIColor.colorWithRedGreenBlueAlpha(0.06, 0.06, 0.08, alphaOverride !== undefined ? alphaOverride : 1.0);
  }

  function isLightColor(hex) {
    if (root.TripStyleEngine && root.TripStyleEngine.isLightColor) {
      return root.TripStyleEngine.isLightColor(hex);
    }
    return false;
  }

  function resolveFont(spec, size, weight) {
    if (root.TripStyleEngine && root.TripStyleEngine.resolveFont) {
      return root.TripStyleEngine.resolveFont(spec, size, weight);
    }
    return $.NSFont.systemFontOfSizeWeight(size, weight || $.NSFontWeightBold);
  }

  function makeTextShadow(alpha, blur, offsetY) {
    const s = $.NSShadow.alloc.init;
    s.shadowOffset = $.NSMakeSize(0, offsetY !== undefined ? offsetY : -3);
    s.shadowBlurRadius = blur !== undefined ? blur : 16.0;
    s.shadowColor = $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.0, 0.0, 0.0, alpha !== undefined ? alpha : 0.65);
    return s;
  }

  function makeTextAttrs(font, color, shadow) {
    const d = $.NSMutableDictionary.dictionary;
    if (font) d.setObjectForKey(font, $.NSFontAttributeName);
    if (color) d.setObjectForKey(color, $.NSForegroundColorAttributeName);
    if (shadow) d.setObjectForKey(shadow, $.NSShadowAttributeName);
    return d;
  }

  function fitFontSize(text, fontSpec, baseSize, maxWidth, minSize) {
    let sz = baseSize;
    const floorSz = minSize || Math.max(16, Math.round(baseSize * 0.45));
    const s = String(text || "");
    if (!s) return resolveFont(fontSpec, sz, $.NSFontWeightBold);
    while (sz > floorSz) {
      const f = resolveFont(fontSpec, sz, $.NSFontWeightBold);
      const attrs = makeTextAttrs(f, $.NSColor.whiteColor, null);
      const w = $(s).sizeWithAttributes(attrs).width;
      if (w <= maxWidth) return f;
      sz = Math.max(floorSz, Math.round(sz * 0.90));
    }
    return resolveFont(fontSpec, floorSz, $.NSFontWeightBold);
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

  function aspectFillZoomCI(ciImg, zoom, width, height) {
    const ext = ciImg.extent;
    const srcW = Math.max(1, ext.size.width);
    const srcH = Math.max(1, ext.size.height);
    let norm = ciImg;
    if (ext.origin.x !== 0 || ext.origin.y !== 0) {
      norm = ciImg.imageByApplyingTransform($.CGAffineTransformMakeTranslation(-ext.origin.x, -ext.origin.y));
    }
    const scale = Math.max(width / srcW, height / srcH) * (zoom || 1.0);
    let scaled = norm.imageByApplyingTransform($.CGAffineTransformMakeScale(scale, scale));
    const sW = srcW * scale;
    const sH = srcH * scale;
    const cropX = (sW - width) * 0.5;
    const cropY = (sH - height) * 0.5;
    scaled = scaled.imageByCroppingToRect($.CGRectMake(cropX, cropY, width, height));
    return scaled.imageByApplyingTransform($.CGAffineTransformMakeTranslation(-cropX, -cropY));
  }

  function buildBlurredBackdropCI(backdropImg, progress, blurSigma, zoomPct, style, width, height) {
    const zoom = 1.02 + (zoomPct !== undefined ? zoomPct : 0.06) * clamp(progress || 0, 0, 1);
    const scaled = aspectFillZoomCI(backdropImg, zoom, width, height);
    let out = scaled;
    if (blurSigma > 0.5) {
      const blur = $.CIFilter.filterWithName("CIGaussianBlur");
      if (blur) {
        blur.setValueForKey(scaled.imageByClampingToExtent, "inputImage");
        blur.setValueForKey(blurSigma, "inputRadius");
        out = blur.outputImage.imageByCroppingToRect($.CGRectMake(0, 0, width, height));
      }
    }
    if (root.TripStyleEngine && root.TripStyleEngine.applyStyleGrade) {
      out = root.TripStyleEngine.applyStyleGrade(out, style);
    }
    return out.imageByCroppingToRect($.CGRectMake(0, 0, width, height));
  }

  function drawDarkVignetteScrim(width, height, baseHex, dimAlpha, hasBackdrop) {
    if (!hasBackdrop) {
      hexToNSColor(baseHex || "#0F1115", 1.0).setFill;
      $.NSBezierPath.fillRect($.NSMakeRect(0, 0, width, height));
      return;
    }
    hexToNSColor(baseHex || "#0F1115", clamp(dimAlpha !== undefined ? dimAlpha : 0.50, 0.05, 0.90)).setFill;
    $.NSBezierPath.fillRect($.NSMakeRect(0, 0, width, height));

    const vigGrad = $.NSGradient.alloc.initWithStartingColorEndingColor(
      $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.03, 0.03, 0.04, 0.0),
      $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.02, 0.02, 0.03, 0.52)
    );
    if (vigGrad) {
      vigGrad.drawFromCenterRadiusToCenterRadiusOptions(
        $.NSMakePoint(width * 0.5, height * 0.5),
        Math.min(width, height) * 0.25,
        $.NSMakePoint(width * 0.5, height * 0.5),
        Math.max(width, height) * 0.68,
        $.NSGradientDrawsAfterEndingLocation
      );
    }
  }

  function toRoman(num) {
    const n = parseInt(num, 10);
    if (!n || n < 1 || n > 50) return String(num || "I");
    const vals = [50, 40, 10, 9, 5, 4, 1];
    const syms = ["L", "XL", "X", "IX", "V", "IV", "I"];
    let r = "";
    let rem = n;
    for (let i = 0; i < vals.length; i++) {
      while (rem >= vals[i]) {
        r += syms[i];
        rem -= vals[i];
      }
    }
    return r;
  }

  function formatDisplayText(str, typo) {
    const s = String(str || "");
    return typo && typo.display_uppercase ? s.toUpperCase() : s;
  }

  function formatMetaText(str, typo) {
    const s = String(str || "");
    return typo && typo.meta_uppercase ? s.toUpperCase() : s;
  }

  function formatStatNumber(val, frac) {
    if (typeof val === "number" && isFinite(val)) {
      const cur = val * clamp(frac, 0, 1);
      const isFloat = Math.abs(val - Math.round(val)) > 0.01;
      const numStr = isFloat ? cur.toFixed(1) : String(Math.round(cur));
      const parts = numStr.split(".");
      parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
      return parts.join(".");
    }
    return String(val !== undefined && val !== null ? val : "");
  }

  // =========================================================================
  // 1. CHAPTER TITLE CARD (All 7 Style Pack Variants!)
  // =========================================================================
  function renderChapterTitleCard(shot, progress, width, height, backdropImg, style) {
    const pal = (style && style.palette) || {};
    const typo = (style && style.typography) || {};
    const cards = (style && style.cards) || {};
    const variant = cards.chapter_card_variant || "editorial_gold_serif";
    const hasBackdrop = Boolean(backdropImg);
    const p = clamp(progress || 0, 0, 1);
    const isPortrait = height > width;
    const scaleFactor = isPortrait ? 0.72 : 1.0;

    const canvas = $.NSImage.alloc.initWithSize($.NSMakeSize(width, height));
    canvas.lockFocus;

    const titleRaw = shot.title || "Chapter";
    const titleStr = formatDisplayText(titleRaw, typo);
    const eyebrowRaw = shot.eyebrow || "";
    const subStr = shot.subtitle || [shot.date_label || shot.date_stamp, shot.coordinates_label].filter(Boolean).join("  ·  ");
    const shadow = makeTextShadow(0.62, 16.0, -3);

    if (variant === "kraft_stamp_typewriter") {
      // Analog Scrapbook: warm kraft paper panel over blurred backdrop + masking tape + terracotta stamp border
      drawDarkVignetteScrim(width, height, "#23201D", hasBackdrop ? 0.25 : 0.92, hasBackdrop);
      const panelW = Math.round(width * (isPortrait ? 0.84 : 0.64));
      const panelH = Math.round(height * (isPortrait ? 0.42 : 0.50));
      const px = Math.round((width - panelW) * 0.5);
      const py = Math.round((height - panelH) * 0.5);

      const pop = dampedSpring(clamp(p / 0.28, 0, 1));
      $.NSGraphicsContext.saveGraphicsState;
      const xf = $.NSAffineTransform.transform;
      xf.translateXByYBy(width * 0.5, height * 0.5 - (1.0 - pop) * 50.0);
      xf.rotateByDegrees(-1.4);
      xf.scaleBy(0.94 + 0.06 * pop);
      xf.translateXByYBy(-width * 0.5, -height * 0.5);
      xf.concat;

      const sh = $.NSShadow.alloc.init;
      sh.shadowOffset = $.NSMakeSize(4, -10);
      sh.shadowBlurRadius = 24;
      sh.shadowColor = $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0, 0, 0, 0.42);
      sh.set;
      const cardRect = $.NSMakeRect(px, py, panelW, panelH);
      hexToNSColor(pal.bg || "#F2E9DC", 0.96).setFill;
      $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius(cardRect, 6, 6).fill;

      // Terracotta double stamp border
      const outerB = $.NSBezierPath.bezierPathWithRect($.NSMakeRect(px + 22, py + 22, panelW - 44, panelH - 44));
      outerB.lineWidth = 2.5;
      hexToNSColor(pal.accent || "#C84B31", 0.85).setStroke;
      outerB.stroke;
      const innerB = $.NSBezierPath.bezierPathWithRect($.NSMakeRect(px + 30, py + 30, panelW - 60, panelH - 60));
      innerB.lineWidth = 1.0;
      hexToNSColor(pal.accent || "#C84B31", 0.50).setStroke;
      innerB.stroke;

      // Translucent masking tape at top corners
      const tapeCorners = [
        { x: px + 26, y: py + panelH - 10, deg: -26 },
        { x: px + panelW - 26, y: py + panelH - 10, deg: 24 },
      ];
      for (let i = 0; i < 2; i++) {
        $.NSGraphicsContext.saveGraphicsState;
        const tx = $.NSAffineTransform.transform;
        tx.translateXByYBy(tapeCorners[i].x, tapeCorners[i].y);
        tx.rotateByDegrees(tapeCorners[i].deg);
        tx.concat;
        hexToNSColor(pal.surface || "#EFE6C8", 0.78).setFill;
        $.NSBezierPath.fillRect($.NSMakeRect(-58, -16, 116, 32));
        $.NSGraphicsContext.restoreGraphicsState;
      }

      if (eyebrowRaw) {
        const eStr = formatMetaText(eyebrowRaw, typo);
        const eAttrs = makeTextAttrs(resolveFont(typo.meta_font || "Menlo-Regular", Math.round(20 * scaleFactor)), hexToNSColor(pal.accent || "#C84B31", 0.95), null);
        const eSz = $(eStr).sizeWithAttributes(eAttrs);
        $(eStr).drawAtPointWithAttributes($.NSMakePoint((width - eSz.width) * 0.5, py + panelH * 0.67), eAttrs);
      }

      const tFont = fitFontSize(titleStr, typo.display_font || "AmericanTypewriter-Bold", Math.round(62 * scaleFactor), panelW - 110, 30);
      const tAttrs = makeTextAttrs(tFont, hexToNSColor(pal.text_primary || "#23201D", 1.0), null);
      const tSz = $(titleStr).sizeWithAttributes(tAttrs);
      $(titleStr).drawAtPointWithAttributes($.NSMakePoint((width - tSz.width) * 0.5, py + panelH * 0.44), tAttrs);

      if (subStr) {
        const sAttrs = makeTextAttrs(resolveFont(typo.meta_font || "Menlo-Regular", Math.round(20 * scaleFactor)), hexToNSColor(pal.text_secondary || "#5E5448", 0.95), null);
        const sSz = $(subStr).sizeWithAttributes(sAttrs);
        $(subStr).drawAtPointWithAttributes($.NSMakePoint((width - sSz.width) * 0.5, py + panelH * 0.24), sAttrs);
      }
      $.NSGraphicsContext.restoreGraphicsState;

    } else if (variant === "pastel_futura_roman") {
      // Wes Postcard: cream postcard border + soft dusty-rose pastel wash + centered Roman numeral + Futura Bold yellow/white
      if (hasBackdrop) {
        hexToNSColor(pal.surface || "#E8B4B8", 0.58).setFill;
        $.NSBezierPath.fillRect($.NSMakeRect(0, 0, width, height));
      } else {
        hexToNSColor(pal.surface || "#E8B4B8", 1.0).setFill;
        $.NSBezierPath.fillRect($.NSMakeRect(0, 0, width, height));
      }
      // Vintage cream postcard border matte
      const bW = 34;
      const borderPath = $.NSBezierPath.bezierPathWithRect($.NSMakeRect(bW * 0.5, bW * 0.5, width - bW, height - bW));
      borderPath.lineWidth = bW;
      hexToNSColor(pal.border || "#FAF6EE", 1.0).setStroke;
      borderPath.stroke;

      const innerRule = $.NSBezierPath.bezierPathWithRect($.NSMakeRect(bW + 18, bW + 18, width - 2 * (bW + 18), height - 2 * (bW + 18)));
      innerRule.lineWidth = 2.0;
      hexToNSColor(pal.accent || "#F4D35E", 0.85).setStroke;
      innerRule.stroke;

      // Roman numeral eyebrow
      let eyeText = eyebrowRaw;
      if (cards.use_roman_numerals && eyeText) {
        eyeText = eyeText.replace(/\b(\d{1,2})\b/, function (_, d) { return toRoman(d); });
      }
      if (eyeText) {
        const eStr = formatMetaText(eyeText, typo);
        const eAttrs = makeTextAttrs(resolveFont(typo.meta_font || "Baskerville-SemiBold", Math.round(24 * scaleFactor)), hexToNSColor("#FAF6EE", 0.96), makeTextShadow(0.35, 6, -2));
        const eSz = $(eStr).sizeWithAttributes(eAttrs);
        $(eStr).drawAtPointWithAttributes($.NSMakePoint((width - eSz.width) * 0.5, height * 0.60), eAttrs);
      }

      const tEase = easeOutCubic(clamp(p / 0.22, 0, 1));
      const tFont = fitFontSize(titleStr, typo.display_font || "Futura-Bold", Math.round(76 * scaleFactor), width - 220, 34);
      const tAttrs = makeTextAttrs(tFont, hexToNSColor(pal.accent || "#F4D35E", tEase), makeTextShadow(0.45, 10, -3));
      const tSz = $(titleStr).sizeWithAttributes(tAttrs);
      $(titleStr).drawAtPointWithAttributes($.NSMakePoint((width - tSz.width) * 0.5, height * 0.46), tAttrs);

      if (subStr) {
        const sAttrs = makeTextAttrs(resolveFont(typo.meta_font || "Baskerville-SemiBold", Math.round(24 * scaleFactor)), hexToNSColor("#FAF6EE", 0.95 * tEase), makeTextShadow(0.35, 6, -2));
        const sSz = $(subStr).sizeWithAttributes(sAttrs);
        $(subStr).drawAtPointWithAttributes($.NSMakePoint((width - sSz.width) * 0.5, height * 0.36), sAttrs);
      }

    } else if (variant === "bold_condensed_kinetic") {
      // Kinetic Montage: dark high-contrast scrim + coral accent bar + huge condensed punch title
      drawDarkVignetteScrim(width, height, pal.bg || "#0B0D10", hasBackdrop ? 0.52 : 1.0, hasBackdrop);

      const slide = easeOutCubic(clamp(p / 0.22, 0, 1));
      const xShift = (1.0 - slide) * -120.0;

      // Diagonal accent bar on left
      const barH = Math.round(height * 0.28 * slide);
      const barX = Math.round(width * 0.09);
      const barY = Math.round(height * 0.36);
      hexToNSColor(pal.accent || "#FF5A36", 1.0).setFill;
      $.NSBezierPath.fillRect($.NSMakeRect(barX, barY, 14, barH));

      // Cyan secondary tick
      hexToNSColor(pal.accent_secondary || "#32D2E8", slide).setFill;
      $.NSBezierPath.fillRect($.NSMakeRect(barX, barY - 22, 14, 12));

      const leftX = barX + 44 + xShift;
      if (eyebrowRaw) {
        const eStr = formatMetaText(eyebrowRaw, typo);
        const eAttrs = makeTextAttrs(resolveFont(typo.meta_font || "HelveticaNeue-Bold", Math.round(22 * scaleFactor)), hexToNSColor(pal.accent_secondary || "#32D2E8", slide), shadow);
        $(eStr).drawAtPointWithAttributes($.NSMakePoint(leftX, height * 0.57), eAttrs);
      }

      const tFont = fitFontSize(titleStr, typo.display_font || "HelveticaNeue-CondensedBlack", Math.round(98 * scaleFactor), width - barX - 140, 44);
      const tAttrs = makeTextAttrs(tFont, hexToNSColor("#FFFFFF", slide), shadow);
      $(titleStr).drawAtPointWithAttributes($.NSMakePoint(leftX, height * 0.43), tAttrs);

      if (subStr) {
        const sStr = formatMetaText(subStr, typo);
        const sAttrs = makeTextAttrs(resolveFont(typo.accent_font || "Menlo-Bold", Math.round(20 * scaleFactor)), hexToNSColor(pal.text_secondary || "#A7B2C2", 0.92 * slide), shadow);
        $(sStr).drawAtPointWithAttributes($.NSMakePoint(leftX, height * 0.36), sAttrs);
      }

    } else if (variant === "swiss_linen_minimal") {
      // Editorial Bento: floating quiet-luxury warm linen card + Swiss grid 1px hairlines + micro EXIF
      drawDarkVignetteScrim(width, height, "#1C1C1B", hasBackdrop ? 0.26 : 0.95, hasBackdrop);
      const ease = easeOutCubic(clamp(p / 0.28, 0, 1));
      const cardW = Math.round(width * (isPortrait ? 0.84 : 0.58));
      const cardH = Math.round(height * (isPortrait ? 0.38 : 0.46));
      const cx = Math.round((width - cardW) * 0.5);
      const cy = Math.round((height - cardH) * 0.5 - (1.0 - ease) * 20.0);

      const cardPath = $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius($.NSMakeRect(cx, cy, cardW, cardH), 14, 14);
      hexToNSColor(pal.bg || "#F4F1EA", 0.96 * ease).setFill;
      cardPath.fill;
      hexToNSColor(pal.border || "#D2CBBF", 0.9 * ease).setStroke;
      cardPath.lineWidth = 1.5;
      cardPath.stroke;

      const pad = 54;
      if (eyebrowRaw) {
        const eStr = formatMetaText(eyebrowRaw, typo);
        const eAttrs = makeTextAttrs(resolveFont(typo.accent_font || "Menlo-Regular", Math.round(17 * scaleFactor)), hexToNSColor(pal.accent || "#8C6D46", ease), null);
        $(eStr).drawAtPointWithAttributes($.NSMakePoint(cx + pad, cy + cardH - pad - 24), eAttrs);
      }

      // Top hairline rule
      const ruleW = (cardW - 2 * pad) * ease;
      const r1 = $.NSBezierPath.bezierPath;
      r1.moveToPoint($.NSMakePoint(cx + pad, cy + cardH - pad - 44));
      r1.lineToPoint($.NSMakePoint(cx + pad + ruleW, cy + cardH - pad - 44));
      r1.lineWidth = 1.0;
      hexToNSColor(pal.border || "#D2CBBF", ease).setStroke;
      r1.stroke;

      const tFont = fitFontSize(titleStr, typo.display_font || "Georgia-Bold", Math.round(62 * scaleFactor), cardW - 2 * pad, 30);
      const tAttrs = makeTextAttrs(tFont, hexToNSColor(pal.text_primary || "#1C1C1B", ease), null);
      $(titleStr).drawAtPointWithAttributes($.NSMakePoint(cx + pad, cy + cardH * 0.42), tAttrs);

      if (subStr) {
        const sAttrs = makeTextAttrs(resolveFont(typo.meta_font || "HelveticaNeue-Medium", Math.round(20 * scaleFactor)), hexToNSColor(pal.text_secondary || "#66635C", ease), null);
        $(subStr).drawAtPointWithAttributes($.NSMakePoint(cx + pad, cy + pad), sAttrs);
      }

    } else if (variant === "vintage_super8_date") {
      // Super 8 Nostalgia: warm sepia glow scrim + rounded Super 8 film gate + amber corner date stamp
      drawDarkVignetteScrim(width, height, pal.bg || "#14110E", hasBackdrop ? 0.56 : 1.0, hasBackdrop);

      // Rounded gate border frame
      const mX = Math.round(width * 0.06);
      const mY = Math.round(height * 0.06);
      const gateFrame = $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius($.NSMakeRect(mX, mY, width - 2 * mX, height - 2 * mY), 34, 34);
      gateFrame.lineWidth = 3.0;
      hexToNSColor(pal.accent || "#E88D48", 0.35).setStroke;
      gateFrame.stroke;

      const ease = easeOutCubic(clamp(p / 0.28, 0, 1));
      if (eyebrowRaw) {
        const eStr = formatMetaText(eyebrowRaw, typo);
        const eAttrs = makeTextAttrs(resolveFont(typo.accent_font || "AmericanTypewriter", Math.round(22 * scaleFactor)), hexToNSColor(pal.accent || "#E88D48", ease), shadow);
        const eSz = $(eStr).sizeWithAttributes(eAttrs);
        $(eStr).drawAtPointWithAttributes($.NSMakePoint((width - eSz.width) * 0.5, height * 0.60), eAttrs);
      }

      const tFont = fitFontSize(titleStr, typo.display_font || "Georgia-Bold", Math.round(74 * scaleFactor), width - 220, 34);
      const tAttrs = makeTextAttrs(tFont, hexToNSColor(pal.text_primary || "#F7ECD8", ease), shadow);
      const tSz = $(titleStr).sizeWithAttributes(tAttrs);
      $(titleStr).drawAtPointWithAttributes($.NSMakePoint((width - tSz.width) * 0.5, height * 0.47), tAttrs);

      const lineW = 200 * ease;
      if (lineW > 2) {
        const div = $.NSBezierPath.bezierPath;
        div.moveToPoint($.NSMakePoint((width - lineW) * 0.5, height * 0.435));
        div.lineToPoint($.NSMakePoint((width + lineW) * 0.5, height * 0.435));
        div.lineWidth = 2.0;
        hexToNSColor(pal.accent || "#E88D48", 0.9).setStroke;
        div.stroke;
      }

      if (subStr) {
        const sAttrs = makeTextAttrs(resolveFont(typo.meta_font || "Menlo-Bold", Math.round(22 * scaleFactor)), hexToNSColor(pal.text_secondary || "#CBB89D", 0.9 * ease), shadow);
        const sSz = $(subStr).sizeWithAttributes(sAttrs);
        $(subStr).drawAtPointWithAttributes($.NSMakePoint((width - sSz.width) * 0.5, height * 0.365), sAttrs);
      }

      // Retro glowing quartz corner date stamp
      const stamp = shot.date_stamp || shot.date_label || "";
      if (stamp) {
        const glow = $.NSShadow.alloc.init;
        glow.shadowOffset = $.NSMakeSize(0, 0);
        glow.shadowBlurRadius = 12;
        glow.shadowColor = $.NSColor.colorWithCalibratedRedGreenBlueAlpha(1.0, 0.42, 0.12, 0.9);
        const stAttrs = makeTextAttrs(resolveFont("Menlo-Bold", 24), $.NSColor.colorWithCalibratedRedGreenBlueAlpha(1.0, 0.62, 0.24, 0.95), glow);
        const stSz = $(stamp).sizeWithAttributes(stAttrs);
        $(stamp).drawAtPointWithAttributes($.NSMakePoint(width - mX - stSz.width - 36, mY + 28), stAttrs);
      }

    } else if (variant === "bright_pop_sitcom") {
      // Sitcom Cast: bright playful pop-sticker tilted title pill + yellow drop-block + teal tag
      if (hasBackdrop) {
        hexToNSColor("#1E2430", 0.36).setFill;
        $.NSBezierPath.fillRect($.NSMakeRect(0, 0, width, height));
      } else {
        hexToNSColor(pal.bg || "#FFFDF7", 1.0).setFill;
        $.NSBezierPath.fillRect($.NSMakeRect(0, 0, width, height));
      }

      const sp = dampedSpring(clamp(p / 0.30, 0, 1));
      $.NSGraphicsContext.saveGraphicsState;
      const xf = $.NSAffineTransform.transform;
      xf.translateXByYBy(width * 0.5, height * 0.5);
      xf.rotateByDegrees(-2.5);
      xf.scaleBy(0.82 + 0.18 * sp);
      xf.translateXByYBy(-width * 0.5, -height * 0.5);
      xf.concat;

      const tFont = fitFontSize(titleStr, typo.display_font || "AvenirNext-Heavy", Math.round(78 * scaleFactor), width * 0.72, 34);
      const tAttrs = makeTextAttrs(tFont, $.NSColor.whiteColor, null);
      const tSz = $(titleStr).sizeWithAttributes(tAttrs);
      const pillW = tSz.width + 96;
      const pillH = tSz.height + 54;
      const px = (width - pillW) * 0.5;
      const py = (height - pillH) * 0.5;

      // Yellow hard offset pop shadow
      hexToNSColor(pal.border || "#FFD166", 1.0).setFill;
      $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius($.NSMakeRect(px + 14, py - 14, pillW, pillH), 22, 22).fill;
      // Coral main sticker pill
      hexToNSColor(pal.accent || "#FF6B6B", 1.0).setFill;
      $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius($.NSMakeRect(px, py, pillW, pillH), 22, 22).fill;
      $(titleStr).drawAtPointWithAttributes($.NSMakePoint(px + 48, py + 24), tAttrs);

      if (eyebrowRaw) {
        const eStr = formatMetaText(eyebrowRaw, typo);
        const eAttrs = makeTextAttrs(resolveFont(typo.meta_font || "HelveticaNeue-Bold", Math.round(20 * scaleFactor)), hexToNSColor("#1E2430", 1.0), null);
        const eSz = $(eStr).sizeWithAttributes(eAttrs);
        const epW = eSz.width + 40;
        const epH = 42;
        const epX = px + 24;
        const epY = py + pillH - 12;
        hexToNSColor(pal.accent_secondary || "#06D6A0", 1.0).setFill;
        $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius($.NSMakeRect(epX, epY, epW, epH), 12, 12).fill;
        $(eStr).drawAtPointWithAttributes($.NSMakePoint(epX + 20, epY + 9), eAttrs);
      }

      if (subStr) {
        const sAttrs = makeTextAttrs(resolveFont(typo.meta_font || "HelveticaNeue-Bold", Math.round(22 * scaleFactor)), hexToNSColor("#1E2430", 1.0), null);
        const sSz = $(subStr).sizeWithAttributes(sAttrs);
        const spW = sSz.width + 44;
        const spH = 44;
        const spX = (width - spW) * 0.5;
        const spY = py - spH - 22;
        hexToNSColor("#FFFDF7", 0.96).setFill;
        $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius($.NSMakeRect(spX, spY, spW, spH), 12, 12).fill;
        $(subStr).drawAtPointWithAttributes($.NSMakePoint(spX + 22, spY + 10), sAttrs);
      }
      $.NSGraphicsContext.restoreGraphicsState;

    } else {
      // DEFAULT: editorial_gold_serif (cinema_doc)
      drawDarkVignetteScrim(width, height, pal.bg || "#0F1115", hasBackdrop ? (cards.backdrop_dim_alpha || 0.54) : 1.0, hasBackdrop);

      const accentHex = pal.accent || "#D4AF37";
      const framePath = $.NSBezierPath.bezierPathWithRect($.NSMakeRect(48, 48, width - 96, height - 96));
      framePath.lineWidth = 1.5;
      hexToNSColor(accentHex, 0.28).setStroke;
      framePath.stroke;

      if (eyebrowRaw) {
        const eyeEase = easeOutCubic(clamp(p / 0.20, 0, 1));
        const eyeYOff = (1.0 - eyeEase) * -14.0;
        const eStr = formatMetaText(eyebrowRaw, typo);
        const eyeAttrs = makeTextAttrs(resolveFont(typo.meta_font || "HelveticaNeue-Medium", Math.round(20 * scaleFactor)), hexToNSColor(accentHex, eyeEase), shadow);
        const eSz = $(eStr).sizeWithAttributes(eyeAttrs);
        $(eStr).drawAtPointWithAttributes($.NSMakePoint((width - eSz.width) * 0.5, height * 0.60 + eyeYOff), eyeAttrs);
      }

      const titleEase = easeOutCubic(clamp(p / 0.28, 0, 1));
      const titleYOff = (1.0 - titleEase) * -18.0;
      const tFont = fitFontSize(titleStr, typo.display_font || "Baskerville-SemiBold", Math.round(74 * scaleFactor), width - 190, 34);
      const tAttrs = makeTextAttrs(tFont, hexToNSColor(pal.text_primary || "#F5F2EB", titleEase), shadow);
      const tSz = $(titleStr).sizeWithAttributes(tAttrs);
      $(titleStr).drawAtPointWithAttributes($.NSMakePoint((width - tSz.width) * 0.5, height * 0.48 + titleYOff), tAttrs);

      const lineEase = easeOutCubic(clamp((p - 0.08) / 0.28, 0, 1));
      const lineW = 220.0 * lineEase;
      if (lineW > 1.0 && cards.show_divider_rule !== false) {
        const divPath = $.NSBezierPath.bezierPath;
        divPath.lineWidth = 1.5;
        const divY = height * 0.445;
        divPath.moveToPoint($.NSMakePoint((width - lineW) * 0.5, divY));
        divPath.lineToPoint($.NSMakePoint((width + lineW) * 0.5, divY));
        hexToNSColor(accentHex, 1.0).setStroke;
        divPath.stroke;
      }

      if (subStr) {
        const subEase = easeOutCubic(clamp((p - 0.12) / 0.28, 0, 1));
        const sAttrs = makeTextAttrs(resolveFont(typo.meta_font || "HelveticaNeue-Medium", Math.round(23 * scaleFactor)), hexToNSColor(pal.text_secondary || "#EAE4D9", 0.90 * subEase), shadow);
        const sSz = $(subStr).sizeWithAttributes(sAttrs);
        $(subStr).drawAtPointWithAttributes($.NSMakePoint((width - sSz.width) * 0.5, height * 0.375), sAttrs);
      }
    }

    canvas.unlockFocus;
    const overlayCI = nsImageToCI(canvas, width, height);
    if (!hasBackdrop) return overlayCI;

    const blurSigma = Number(cards.backdrop_blur_sigma || 20.0);
    const zoomPct = Number(cards.backdrop_zoom_pct || 0.06);
    const bgCI = buildBlurredBackdropCI(backdropImg, p, blurSigma, zoomPct, style, width, height);
    return overlayCI.imageByCompositingOverImage(bgCI).imageByCroppingToRect($.CGRectMake(0, 0, width, height));
  }

  // =========================================================================
  // 2. TEXT BEHIND SUBJECT (Magazine Cover Depth Typography + Mask Fallback)
  // =========================================================================
  function renderTextBehindSubjectCard(shot, progress, width, height, heroImg, maskImg, style) {
    const rect = $.CGRectMake(0, 0, width, height);
    const pal = (style && style.palette) || {};
    const typo = (style && style.typography) || {};
    const p = clamp(progress || 0, 0, 1);

    const zoom = 1.0 + 0.05 * p;
    let basePhoto = heroImg
      ? aspectFillZoomCI(heroImg, zoom, width, height)
      : $.CIImage.imageWithColor(hexToCIColor(pal.bg || "#0B0D10", 1.0)).imageByCroppingToRect(rect);
    if (root.TripStyleEngine && root.TripStyleEngine.applyStyleGrade) {
      basePhoto = root.TripStyleEngine.applyStyleGrade(basePhoto, style);
    }

    const titleStr = String(shot.title || "DESTINATION").toUpperCase();
    const eyebrowStr = shot.eyebrow ? String(shot.eyebrow).toUpperCase() : "";
    const subStr = shot.subtitle || shot.date_label || "";

    // Build giant bold sliding title overlay
    const slideEase = easeOutCubic(clamp(p / 0.34, 0, 1));
    const yOffset = (1.0 - slideEase) * -85.0;

    const textCanvas = $.NSImage.alloc.initWithSize($.NSMakeSize(width, height));
    textCanvas.lockFocus;

    // If no mask_path, add a crisp subtle magazine-cover top gradient scrim so giant title always pops cleanly
    if (!maskImg) {
      const topGrad = $.NSGradient.alloc.initWithStartingColorEndingColor(
        $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.03, 0.04, 0.05, 0.62),
        $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.03, 0.04, 0.05, 0.0)
      );
      if (topGrad) {
        topGrad.drawInRectAngle($.NSMakeRect(0, height * 0.48, width, height * 0.52), -90);
      }
    }

    const baseFontSize = height > width ? 130 : 196;
    const displayFont = fitFontSize(titleStr, typo.display_font || "HelveticaNeue-CondensedBlack", baseFontSize, width * 0.88, 62);
    const titleColor = hexToNSColor(pal.text_primary && isLightColor(pal.text_primary) ? pal.text_primary : "#FFFFFF", 0.96 * slideEase);
    const tAttrs = makeTextAttrs(displayFont, titleColor, makeTextShadow(0.42, 18, -4));
    const tSz = $(titleStr).sizeWithAttributes(tAttrs);
    const titleY = (maskImg ? height * 0.52 : height * 0.62) + yOffset;
    $(titleStr).drawAtPointWithAttributes($.NSMakePoint((width - tSz.width) * 0.5, titleY), tAttrs);

    textCanvas.unlockFocus;
    const textCI = nsImageToCI(textCanvas, width, height);
    let composite = textCI.imageByCompositingOverImage(basePhoto).imageByCroppingToRect(rect);

    // If cached Vision foreground alpha mask is present, place subject IN FRONT of the giant title!
    if (maskImg && heroImg) {
      let scaledMask = aspectFillZoomCI(maskImg, zoom, width, height);
      const feather = $.CIFilter.filterWithName("CIGaussianBlur");
      if (feather) {
        feather.setValueForKey(scaledMask.imageByClampingToExtent, "inputImage");
        feather.setValueForKey(1.5, "inputRadius");
        scaledMask = feather.outputImage.imageByCroppingToRect(rect);
      }
      const blend = $.CIFilter.filterWithName("CIBlendWithMask");
      if (blend) {
        blend.setValueForKey(basePhoto, "inputImage");
        blend.setValueForKey(composite, "inputBackgroundImage");
        blend.setValueForKey(scaledMask, "inputMaskImage");
        composite = blend.outputImage.imageByCroppingToRect(rect);
      }
    }

    // Crisp foreground eyebrow/subtitle pill at bottom so metadata always sits cleanly in front
    if (eyebrowStr || subStr) {
      const hudCanvas = $.NSImage.alloc.initWithSize($.NSMakeSize(width, height));
      hudCanvas.lockFocus;
      const hudEase = easeOutCubic(clamp((p - 0.14) / 0.28, 0, 1));
      const hudText = [eyebrowStr, subStr].filter(Boolean).join("  ·  ");
      const hFont = resolveFont(typo.meta_font || "HelveticaNeue-Bold", 22, $.NSFontWeightBold);
      const hAttrs = makeTextAttrs(hFont, $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.98, 0.97, 0.95, hudEase), null);
      const hSz = $(hudText).sizeWithAttributes(hAttrs);
      const pW = hSz.width + 44;
      const pH = 44;
      const pX = (width - pW) * 0.5;
      const pY = 58;
      hexToNSColor(pal.bg || "#0B0D10", 0.78 * hudEase).setFill;
      $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius($.NSMakeRect(pX, pY, pW, pH), 10, 10).fill;
      hexToNSColor(pal.accent || "#FF5A36", 0.9 * hudEase).setStroke;
      const bp = $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius($.NSMakeRect(pX, pY, pW, pH), 10, 10);
      bp.lineWidth = 1.6;
      bp.stroke;
      $(hudText).drawAtPointWithAttributes($.NSMakePoint(pX + 22, pY + 10), hAttrs);
      hudCanvas.unlockFocus;
      composite = nsImageToCI(hudCanvas, width, height).imageByCompositingOverImage(composite).imageByCroppingToRect(rect);
    }

    return composite.imageByCroppingToRect(rect);
  }

  // =========================================================================
  // 3. ROUTE MAP CARD (Curved Bezier Arcs + Rotated Marker + Synced Elevation Profile)
  // =========================================================================
  function quadBezierPoint(p0, ctrl, p1, t) {
    const u = 1.0 - t;
    return {
      x: u * u * p0.x + 2 * u * t * ctrl.x + t * t * p1.x,
      y: u * u * p0.y + 2 * u * t * ctrl.y + t * t * p1.y,
    };
  }

  function quadBezierTangent(p0, ctrl, p1, t) {
    const u = 1.0 - t;
    return {
      dx: 2 * u * (ctrl.x - p0.x) + 2 * t * (p1.x - ctrl.x),
      dy: 2 * u * (ctrl.y - p0.y) + 2 * t * (p1.y - ctrl.y),
    };
  }

  function resolveMapSkinColors(style) {
    const pal = (style && style.palette) || {};
    const skin = (style && style.cards && style.cards.route_map_skin) || "dark_topo";
    const isLight = skin === "parchment_ink" || skin === "linen_minimal" || skin === "pastel_atlas" || skin === "bright_playful";
    if (skin === "parchment_ink") {
      return {
        isLight: true,
        scrim: "#F2E9DC",
        scrimAlpha: 0.86,
        grid: "#5E5448",
        line: "#C84B31",
        glow: "#C84B31",
        text: "#23201D",
        sub: "#C84B31",
        pillBg: "#FAF6EE",
        pillBorder: "#C84B31",
      };
    }
    if (skin === "pastel_atlas") {
      return {
        isLight: true,
        scrim: "#FAF6EE",
        scrimAlpha: 0.88,
        grid: "#90C2E7",
        line: "#C85A32",
        glow: "#F4D35E",
        text: "#262322",
        sub: "#5A534E",
        pillBg: "#E8B4B8",
        pillBorder: "#262322",
      };
    }
    if (skin === "linen_minimal") {
      return {
        isLight: true,
        scrim: "#F4F1EA",
        scrimAlpha: 0.90,
        grid: "#D2CBBF",
        line: "#8C6D46",
        glow: "#8C6D46",
        text: "#1C1C1B",
        sub: "#66635C",
        pillBg: "#EAE4D7",
        pillBorder: "#8C6D46",
      };
    }
    if (skin === "bright_playful") {
      return {
        isLight: true,
        scrim: "#FFFDF7",
        scrimAlpha: 0.88,
        grid: "#FFD166",
        line: "#FF6B6B",
        glow: "#06D6A0",
        text: "#1E2430",
        sub: "#118AB2",
        pillBg: "#FFF3D1",
        pillBorder: "#FF6B6B",
      };
    }
    if (skin === "high_contrast_kinetic") {
      return {
        isLight: false,
        scrim: "#0B0D10",
        scrimAlpha: 0.72,
        grid: "#32D2E8",
        line: "#FF5A36",
        glow: "#FF5A36",
        text: "#FFFFFF",
        sub: "#32D2E8",
        pillBg: "#151921",
        pillBorder: "#FF5A36",
      };
    }
    if (skin === "vintage_sepia") {
      return {
        isLight: false,
        scrim: "#14110E",
        scrimAlpha: 0.72,
        grid: "#E88D48",
        line: "#E88D48",
        glow: "#D64531",
        text: "#F7ECD8",
        sub: "#E88D48",
        pillBg: "#231D17",
        pillBorder: "#E88D48",
      };
    }
    return {
      isLight: isLight,
      scrim: pal.bg || "#0F1115",
      scrimAlpha: 0.64,
      grid: pal.accent || "#D4AF37",
      line: pal.accent || "#E8C58E",
      glow: pal.accent || "#E8C58E",
      text: pal.text_primary || "#F5F2EB",
      sub: pal.accent || "#E8C58E",
      pillBg: pal.surface || "#12151C",
      pillBorder: pal.accent || "#E8C58E",
    };
  }

  function extractElevationSeries(shot, wps) {
    const ep = shot.elevation_profile;
    if (ep === false) return null;
    if (ep && typeof ep === "object" && ep.enabled === false) return null;
    if (ep && typeof ep === "object" && Array.isArray(ep.points) && ep.points.length >= 2) {
      return {
        unit: ep.unit || "m",
        points: ep.points.map((pt) => ({ label: pt.label || "", elev: Number(pt.elevation_m) || 0 })),
      };
    }
    const elevWps = wps.filter((w) => w.elevation_m !== undefined && w.elevation_m !== null && isFinite(Number(w.elevation_m)));
    if (elevWps.length >= 2) {
      return {
        unit: (ep && typeof ep === "object" && ep.unit) ? ep.unit : "m",
        points: wps.map((w) => ({
          label: w.label || "",
          elev: isFinite(Number(w.elevation_m)) ? Number(w.elevation_m) : Number(elevWps[0].elevation_m),
        })),
      };
    }
    return null;
  }

  function renderRouteMapCard(shot, progress, width, height, backdropImg, style) {
    const wps = Array.isArray(shot.waypoints) ? shot.waypoints : [];
    if (wps.length === 0) {
      return renderChapterTitleCard(shot, progress, width, height, backdropImg, style);
    }

    const skin = resolveMapSkinColors(style);
    const typo = (style && style.typography) || {};
    const hasBackdrop = Boolean(backdropImg);
    const p = clamp(progress || 0, 0, 1);
    const shadow = skin.isLight ? null : makeTextShadow(0.55, 12, -2);

    const elevSeries = extractElevationSeries(shot, wps);
    const hasElevation = Boolean(elevSeries);
    const elevBandH = hasElevation ? Math.round(height * 0.21) : 0;

    const canvas = $.NSImage.alloc.initWithSize($.NSMakeSize(width, height));
    canvas.lockFocus;

    // Scrim / Paper background
    hexToNSColor(skin.scrim, hasBackdrop ? skin.scrimAlpha : 1.0).setFill;
    $.NSBezierPath.fillRect($.NSMakeRect(0, 0, width, height));

    // Faint latitude/longitude grid lines
    hexToNSColor(skin.grid, skin.isLight ? 0.16 : 0.08).setStroke;
    for (let gx = 160; gx < width; gx += 160) {
      const gp = $.NSBezierPath.bezierPath;
      gp.lineWidth = 1.0;
      gp.moveToPoint($.NSMakePoint(gx, 48));
      gp.lineToPoint($.NSMakePoint(gx, height - 48));
      gp.stroke;
    }
    for (let gy = 135; gy < height; gy += 135) {
      const gp = $.NSBezierPath.bezierPath;
      gp.lineWidth = 1.0;
      gp.moveToPoint($.NSMakePoint(48, gy));
      gp.lineToPoint($.NSMakePoint(width - 48, gy));
      gp.stroke;
    }

    // 48px inset framing border
    const framePath = $.NSBezierPath.bezierPathWithRect($.NSMakeRect(48, 48, width - 96, height - 96));
    framePath.lineWidth = 1.5;
    hexToNSColor(skin.line, 0.30).setStroke;
    framePath.stroke;

    // Header title
    if (shot.title) {
      const hFont = resolveFont(typo.display_font || "Baskerville-SemiBold", 42, $.NSFontWeightBold);
      const hAttrs = makeTextAttrs(hFont, hexToNSColor(skin.text, 0.96), shadow);
      $(formatDisplayText(shot.title, typo)).drawAtPointWithAttributes($.NSMakePoint(92, height - 122), hAttrs);
      const uPath = $.NSBezierPath.bezierPath;
      uPath.lineWidth = 2.0;
      uPath.moveToPoint($.NSMakePoint(92, height - 134));
      uPath.lineToPoint($.NSMakePoint(242, height - 134));
      hexToNSColor(skin.line, 0.85).setStroke;
      uPath.stroke;
    }

    // Map projection bounds (leave clean space at bottom for Elevation Profile sparkline!)
    const padX = Math.round(width * 0.15);
    const padTop = 195;
    const padBot = hasElevation ? (elevBandH + 105) : 185;
    const lats = wps.map((w) => Number(w.lat) || 0);
    const lons = wps.map((w) => Number(w.lon) || 0);
    const minLat = Math.min.apply(null, lats);
    const maxLat = Math.max.apply(null, lats);
    const minLon = Math.min.apply(null, lons);
    const maxLon = Math.max.apply(null, lons);
    const latSpan = Math.max(0.01, maxLat - minLat);
    const lonSpan = Math.max(0.01, maxLon - minLon);

    const pts = wps.map((w) => ({
      label: w.label || "",
      subtitle: w.subtitle || (w.elevation_m !== undefined ? formatStatNumber(Number(w.elevation_m), 1) + " m" : ""),
      x: padX + ((Number(w.lon) - minLon) / lonSpan) * (width - padX * 2),
      y: padBot + ((Number(w.lat) - minLat) / latSpan) * Math.max(120, height - padTop - padBot),
    }));

    // Subtle concentric topographic rings around each stop
    for (let i = 0; i < pts.length; i++) {
      const radii = [22, 40, 58];
      for (let rIdx = 0; rIdx < radii.length; rIdx++) {
        const r = radii[rIdx];
        const ring = $.NSBezierPath.bezierPathWithOvalInRect($.NSMakeRect(pts[i].x - r, pts[i].y - r, r * 2, r * 2));
        ring.lineWidth = 1.0;
        hexToNSColor(skin.line, 0.16 - rIdx * 0.04).setStroke;
        ring.stroke;
      }
    }

    // Route animation finishes reaching the FINAL stop by progress = 0.72 so ALL stop pills reach 100% before 0.85!
    const numSegs = Math.max(1, pts.length - 1);
    const routeEase = easeInOutCubic(clamp(p / 0.72, 0.0, 1.0));
    const totalPos = routeEase * numSegs;

    const routePath = $.NSBezierPath.bezierPath;
    routePath.lineCapStyle = $.NSLineCapStyleRound;
    routePath.moveToPoint($.NSMakePoint(pts[0].x, pts[0].y));
    let headPt = { x: pts[0].x, y: pts[0].y };
    let headAngleRad = 0.0;

    for (let i = 0; i < numSegs; i++) {
      const segT = clamp(totalPos - i, 0.0, 1.0);
      if (segT <= 0.0) break;
      const p0 = pts[i];
      const p1 = pts[Math.min(pts.length - 1, i + 1)];
      const dx = p1.x - p0.x;
      const dy = p1.y - p0.y;
      const sign = i % 2 === 0 ? 1.0 : -1.0;
      const ctrl = {
        x: (p0.x + p1.x) * 0.5 - dy * 0.18 * sign,
        y: (p0.y + p1.y) * 0.5 + dx * 0.18 * sign,
      };
      const steps = 36;
      const maxStep = Math.max(1, Math.round(steps * segT));
      for (let s = 1; s <= maxStep; s++) {
        const t = (s / maxStep) * segT;
        headPt = quadBezierPoint(p0, ctrl, p1, t);
        routePath.lineToPoint($.NSMakePoint(headPt.x, headPt.y));
      }
      const tang = quadBezierTangent(p0, ctrl, p1, segT);
      if (Math.abs(tang.dx) > 0.001 || Math.abs(tang.dy) > 0.001) {
        headAngleRad = Math.atan2(tang.dy, tang.dx);
      }
    }

    // Soft glow halo + crisp route stroke
    hexToNSColor(skin.glow, 0.28).setStroke;
    routePath.lineWidth = 11.0;
    routePath.stroke;

    hexToNSColor(skin.line, 0.96).setStroke;
    routePath.lineWidth = 3.8;
    routePath.stroke;

    // Moving rotated vector travel marker (chevron/plane nose aligned to exact Bezier tangent!)
    hexToNSColor(skin.glow, 0.34).setFill;
    $.NSBezierPath.bezierPathWithOvalInRect($.NSMakeRect(headPt.x - 16, headPt.y - 16, 32, 32)).fill;

    $.NSGraphicsContext.saveGraphicsState;
    const mxf = $.NSAffineTransform.transform;
    mxf.translateXByYBy(headPt.x, headPt.y);
    mxf.rotateByRadians(headAngleRad);
    mxf.concat;
    const marker = $.NSBezierPath.bezierPath;
    marker.moveToPoint($.NSMakePoint(13, 0));
    marker.lineToPoint($.NSMakePoint(-8, 8));
    marker.lineToPoint($.NSMakePoint(-4, 0));
    marker.lineToPoint($.NSMakePoint(-8, -8));
    marker.closePath;
    hexToNSColor(skin.isLight ? skin.text : "#FFF8EB", 1.0).setFill;
    marker.fill;
    $.NSGraphicsContext.restoreGraphicsState;

    // Crisp stop pills guaranteed to reveal 100% cleanly before progress = 0.85!
    for (let i = 0; i < pts.length; i++) {
      // Stop i begins revealing at p_i = (i / numSegs) * 0.70 and completes within 0.12 -> last stop hits 1.0 by p = 0.82!
      const stopStart = pts.length <= 1 ? 0.0 : (i / numSegs) * 0.70;
      const pinAlpha = easeOutCubic(clamp((p - stopStart) / 0.12, 0.0, 1.0));
      if (pinAlpha <= 0.02) continue;

      hexToNSColor(skin.line, pinAlpha).setFill;
      $.NSBezierPath.bezierPathWithOvalInRect($.NSMakeRect(pts[i].x - 7, pts[i].y - 7, 14, 14)).fill;

      const lblAttrs = makeTextAttrs(resolveFont(typo.meta_font || "HelveticaNeue-Bold", 21, $.NSFontWeightBold), hexToNSColor(skin.text, pinAlpha), shadow);
      const subAttrs = makeTextAttrs(resolveFont(typo.accent_font || "Menlo-Regular", 15, $.NSFontWeightMedium), hexToNSColor(skin.sub, pinAlpha * 0.95), null);

      const lblText = pts[i].label;
      const subText = pts[i].subtitle;
      const lblSz = $(lblText).sizeWithAttributes(lblAttrs);
      const subSz = subText ? $(subText).sizeWithAttributes(subAttrs) : { width: 0, height: 0 };
      const pillW = Math.max(lblSz.width, subSz.width) + 30;
      const pillH = subText ? 58 : 38;

      // Flip pill to left side if close to right margin
      let pillX = pts[i].x > width * 0.68 ? (pts[i].x - pillW - 18) : (pts[i].x + 18);
      pillX = clamp(pillX, 64, width - pillW - 64);
      let pillY = clamp(pts[i].y - pillH * 0.5, padBot - 24, height - pillH - 80);

      const pill = $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius($.NSMakeRect(pillX, pillY, pillW, pillH), 9, 9);
      hexToNSColor(skin.pillBg, 0.88 * pinAlpha).setFill;
      pill.fill;
      hexToNSColor(skin.pillBorder, 0.52 * pinAlpha).setStroke;
      pill.lineWidth = 1.2;
      pill.stroke;

      $(lblText).drawAtPointWithAttributes($.NSMakePoint(pillX + 15, pillY + (subText ? 28 : 8)), lblAttrs);
      if (subText) {
        $(subText).drawAtPointWithAttributes($.NSMakePoint(pillX + 15, pillY + 8), subAttrs);
      }
    }

    // Synced Elevation Profile Sparkline Panel at bottom
    if (hasElevation) {
      const ePts = elevSeries.points;
      const unit = elevSeries.unit || "m";
      const vals = ePts.map((ep) => ep.elev);
      const minE = Math.min.apply(null, vals);
      const maxE = Math.max.apply(null, vals);
      const eSpan = Math.max(10, maxE - minE);

      const boxX = 92;
      const boxY = 74;
      const boxW = width - 184;
      const boxH = elevBandH - 18;

      // Translucent panel box
      const panel = $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius($.NSMakeRect(boxX, boxY, boxW, boxH), 12, 12);
      hexToNSColor(skin.pillBg, 0.82).setFill;
      panel.fill;
      hexToNSColor(skin.pillBorder, 0.32).setStroke;
      panel.lineWidth = 1.0;
      panel.stroke;

      const chartX = boxX + 26;
      const chartY = boxY + 26;
      const chartW = boxW - 52;
      const chartH = boxH - 64;

      // Header label + peak altitude badge
      const epTitleAttrs = makeTextAttrs(resolveFont(typo.meta_font || "Menlo-Regular", 15, $.NSFontWeightBold), hexToNSColor(skin.sub, 0.95), null);
      $("ELEVATION PROFILE").drawAtPointWithAttributes($.NSMakePoint(chartX, boxY + boxH - 28), epTitleAttrs);

      const peakStr = "LOW " + formatStatNumber(minE, 1) + unit + "  ·  PEAK " + formatStatNumber(maxE, 1) + unit;
      const pkSz = $(peakStr).sizeWithAttributes(epTitleAttrs);
      $(peakStr).drawAtPointWithAttributes($.NSMakePoint(boxX + boxW - pkSz.width - 26, boxY + boxH - 28), epTitleAttrs);

      // Subtle baseline
      const baseL = $.NSBezierPath.bezierPath;
      baseL.moveToPoint($.NSMakePoint(chartX, chartY));
      baseL.lineToPoint($.NSMakePoint(chartX + chartW, chartY));
      baseL.lineWidth = 1.0;
      hexToNSColor(skin.grid, 0.25).setStroke;
      baseL.stroke;

      // Map elevation samples to chart coords
      const cPts = ePts.map((ep, idx) => ({
        x: chartX + (ePts.length <= 1 ? 0 : (idx / (ePts.length - 1)) * chartW),
        y: chartY + 6 + ((ep.elev - minE) / eSpan) * Math.max(20, chartH - 12),
        elev: ep.elev,
        label: ep.label,
      }));

      // Build clipped reveal synced to routeEase!
      const revealX = chartX + chartW * routeEase;
      const eSegs = Math.max(1, cPts.length - 1);
      const ePos = routeEase * eSegs;
      const eIdx = Math.min(eSegs - 1, Math.floor(ePos));
      const eFrac = ePos - eIdx;
      const activeX = cPts[eIdx].x + (cPts[eIdx + 1].x - cPts[eIdx].x) * eFrac;
      const activeY = cPts[eIdx].y + (cPts[eIdx + 1].y - cPts[eIdx].y) * eFrac;
      const activeElev = cPts[eIdx].elev + (cPts[eIdx + 1].elev - cPts[eIdx].elev) * eFrac;

      $.NSGraphicsContext.saveGraphicsState;
      $.NSBezierPath.clipRect($.NSMakeRect(chartX - 2, chartY - 2, Math.max(2, revealX - chartX + 4), chartH + 20));

      // Area fill under elevation profile
      const areaPath = $.NSBezierPath.bezierPath;
      areaPath.moveToPoint($.NSMakePoint(cPts[0].x, chartY));
      for (let i = 0; i < cPts.length; i++) {
        areaPath.lineToPoint($.NSMakePoint(cPts[i].x, cPts[i].y));
      }
      areaPath.lineToPoint($.NSMakePoint(cPts[cPts.length - 1].x, chartY));
      areaPath.closePath;
      hexToNSColor(skin.line, 0.20).setFill;
      areaPath.fill;

      // Elevation stroke curve
      const linePath = $.NSBezierPath.bezierPath;
      linePath.lineWidth = 3.0;
      linePath.lineJoinStyle = $.NSLineJoinStyleRound;
      linePath.moveToPoint($.NSMakePoint(cPts[0].x, cPts[0].y));
      for (let i = 1; i < cPts.length; i++) {
        linePath.lineToPoint($.NSMakePoint(cPts[i].x, cPts[i].y));
      }
      hexToNSColor(skin.line, 0.95).setStroke;
      linePath.stroke;

      $.NSGraphicsContext.restoreGraphicsState;

      // Glowing live altitude dot + current elevation readout
      hexToNSColor(skin.glow, 0.35).setFill;
      $.NSBezierPath.bezierPathWithOvalInRect($.NSMakeRect(activeX - 10, activeY - 10, 20, 20)).fill;
      hexToNSColor(skin.isLight ? skin.text : "#FFFFFF", 1.0).setFill;
      $.NSBezierPath.bezierPathWithOvalInRect($.NSMakeRect(activeX - 5, activeY - 5, 10, 10)).fill;

      const curElevStr = formatStatNumber(Math.round(activeElev), 1) + " " + unit;
      const curAttrs = makeTextAttrs(resolveFont("Menlo-Bold", 15, $.NSFontWeightBold), hexToNSColor(skin.text, 0.96), null);
      const curSz = $(curElevStr).sizeWithAttributes(curAttrs);
      const lblX = clamp(activeX - curSz.width * 0.5, chartX, chartX + chartW - curSz.width);
      const lblY = (activeY + 12 > boxY + boxH - 54) ? Math.max(chartY + 4, activeY - 28) : (activeY + 12);
      $(curElevStr).drawAtPointWithAttributes($.NSMakePoint(lblX, lblY), curAttrs);
    }

    canvas.unlockFocus;
    const overlayCI = nsImageToCI(canvas, width, height);
    if (!hasBackdrop) return overlayCI;
    const bgCI = buildBlurredBackdropCI(backdropImg, p, 24.0, 0.04, style, width, height);
    return overlayCI.imageByCompositingOverImage(bgCI).imageByCroppingToRect($.CGRectMake(0, 0, width, height));
  }

  // =========================================================================
  // 4. TRIP WRAPPED CARD (Animated EXIF Stats Dashboard)
  // =========================================================================
  function renderTripWrappedCard(shot, progress, width, height, backdropImg, style) {
    const pal = (style && style.palette) || {};
    const typo = (style && style.typography) || {};
    const hasBackdrop = Boolean(backdropImg);
    const p = clamp(progress || 0, 0, 1);
    const isLight = isLightColor(pal.bg || "#0F1115");
    const shadow = isLight ? null : makeTextShadow(0.55, 14, -2);
    const stats = Array.isArray(shot.stats) && shot.stats.length > 0
      ? shot.stats.slice(0, 6)
      : [{ label: "MEMORIES CAPTURED", value: 100, unit: "%" }];

    const canvas = $.NSImage.alloc.initWithSize($.NSMakeSize(width, height));
    canvas.lockFocus;

    hexToNSColor(pal.bg || "#0F1115", hasBackdrop ? (isLight ? 0.88 : 0.78) : 1.0).setFill;
    $.NSBezierPath.fillRect($.NSMakeRect(0, 0, width, height));

    const headerEase = easeOutCubic(clamp(p / 0.22, 0, 1));
    const eyeText = formatMetaText(shot.eyebrow || "TRIP WRAPPED", typo);
    const titleText = formatDisplayText(shot.title || "BY THE NUMBERS", typo);

    const eyeAttrs = makeTextAttrs(resolveFont(typo.meta_font || "Menlo-Bold", 20, $.NSFontWeightBold), hexToNSColor(pal.accent || "#D4AF37", headerEase), shadow);
    const eSz = $(eyeText).sizeWithAttributes(eyeAttrs);
    $(eyeText).drawAtPointWithAttributes($.NSMakePoint((width - eSz.width) * 0.5, height - 122), eyeAttrs);

    const tFont = fitFontSize(titleText, typo.display_font || "Baskerville-SemiBold", height > width ? 52 : 62, width - 160, 28);
    const tAttrs = makeTextAttrs(tFont, hexToNSColor(pal.text_primary || "#F5F2EB", headerEase), shadow);
    const tSz = $(titleText).sizeWithAttributes(tAttrs);
    $(titleText).drawAtPointWithAttributes($.NSMakePoint((width - tSz.width) * 0.5, height - 196), tAttrs);

    // Balanced stat grid (2 cols for <=4 or portrait, 3 cols for 5-6 in landscape)
    const isPortrait = height > width;
    const cols = isPortrait ? 2 : (stats.length <= 2 ? stats.length : stats.length <= 4 ? 2 : 3);
    const rows = Math.ceil(stats.length / cols);
    const gridPadX = Math.round(width * 0.08);
    const gridTop = height - 245;
    const gridBot = 128;
    const gutter = 24;
    const tileW = Math.floor((width - 2 * gridPadX - (cols - 1) * gutter) / cols);
    const tileH = Math.min(250, Math.floor((gridTop - gridBot - (rows - 1) * gutter) / rows));
    const totalGridH = rows * tileH + (rows - 1) * gutter;
    const startY = gridBot + (gridTop - gridBot - totalGridH) * 0.5;

    for (let i = 0; i < stats.length; i++) {
      const st = stats[i];
      const col = i % cols;
      const row = Math.floor(i / cols);
      const tx = gridPadX + col * (tileW + gutter);
      const ty = startY + (rows - 1 - row) * (tileH + gutter);

      const stagger = 0.08 + i * 0.06;
      const cardPop = easeOutCubic(clamp((p - stagger) / 0.24, 0, 1));
      const countFrac = easeOutCubic(clamp((p - stagger) / 0.48, 0, 1));
      if (cardPop <= 0.01) continue;

      const ySlide = (1.0 - cardPop) * -20.0;
      const cardRect = $.NSMakeRect(tx, ty + ySlide, tileW, tileH);
      const cardPath = $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius(cardRect, 16, 16);
      hexToNSColor(pal.surface || "#181B22", 0.90 * cardPop).setFill;
      cardPath.fill;

      const accentHex = i % 2 === 0 ? (pal.accent || "#D4AF37") : (pal.accent_secondary || pal.accent || "#7A9E9F");
      hexToNSColor(accentHex, 0.45 * cardPop).setStroke;
      cardPath.lineWidth = 1.6;
      cardPath.stroke;

      // Top accent strip
      hexToNSColor(accentHex, cardPop).setFill;
      $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius($.NSMakeRect(tx + 24, ty + ySlide + tileH - 12, 54, 5), 2.5, 2.5).fill;

      // Animated counter value
      const valStr = formatStatNumber(st.value, countFrac) + (st.unit ? " " + st.unit : "");
      const vFont = fitFontSize(valStr, typo.display_font || "HelveticaNeue-Bold", Math.round(tileH * 0.30), tileW - 48, 28);
      const vAttrs = makeTextAttrs(vFont, hexToNSColor(pal.text_primary || "#F5F2EB", cardPop), shadow);
      const vSz = $(valStr).sizeWithAttributes(vAttrs);
      $(valStr).drawAtPointWithAttributes($.NSMakePoint(tx + (tileW - vSz.width) * 0.5, ty + ySlide + tileH * 0.42), vAttrs);

      // Metric label
      const lblStr = formatMetaText(st.label || "", typo);
      const lFont = fitFontSize(lblStr, typo.meta_font || "Menlo-Regular", 17, tileW - 40, 12);
      const lAttrs = makeTextAttrs(lFont, hexToNSColor(pal.text_secondary || "#B9B2A3", 0.95 * cardPop), null);
      const lSz = $(lblStr).sizeWithAttributes(lAttrs);
      $(lblStr).drawAtPointWithAttributes($.NSMakePoint(tx + (tileW - lSz.width) * 0.5, ty + ySlide + tileH * 0.18), lAttrs);
    }

    // Palette swatch footer dots
    const swatches = Array.isArray(pal.swatches) ? pal.swatches.slice(0, 5) : [];
    if (swatches.length > 0) {
      const dotR = 9;
      const gap = 28;
      const totalW = (swatches.length - 1) * gap;
      const sx0 = (width - totalW) * 0.5;
      for (let i = 0; i < swatches.length; i++) {
        hexToNSColor(swatches[i], headerEase).setFill;
        $.NSBezierPath.bezierPathWithOvalInRect($.NSMakeRect(sx0 + i * gap - dotR, 58 - dotR, dotR * 2, dotR * 2)).fill;
      }
    }

    canvas.unlockFocus;
    const overlayCI = nsImageToCI(canvas, width, height);
    if (!hasBackdrop) return overlayCI;
    const bgCI = buildBlurredBackdropCI(backdropImg, p, 26.0, 0.04, style, width, height);
    return overlayCI.imageByCompositingOverImage(bgCI).imageByCroppingToRect($.CGRectMake(0, 0, width, height));
  }

  // =========================================================================
  // 5. CAST INTRO CARD (Freeze-Frame Hero Cutout or Tilted Polaroid + Nickname/Role)
  // =========================================================================
  function renderCastIntroCard(shot, progress, width, height, heroImg, maskImg, style) {
    const rect = $.CGRectMake(0, 0, width, height);
    const pal = (style && style.palette) || {};
    const typo = (style && style.typography) || {};
    const p = clamp(progress || 0, 0, 1);
    const cm = shot.cast_member || {};
    const nameStr = formatDisplayText(cm.name || shot.title || "THE TRAVELER", typo);
    const nickStr = cm.nickname || shot.subtitle || "";
    const roleStr = formatMetaText(cm.role || shot.eyebrow || "MEET THE CREW", typo);
    const isPortrait = height > width;

    // 1. Stylized graphic background (playful burst or rich graded frame)
    const bgCanvas = $.NSImage.alloc.initWithSize($.NSMakeSize(width, height));
    bgCanvas.lockFocus;
    hexToNSColor(pal.bg || "#151921", 1.0).setFill;
    $.NSBezierPath.fillRect($.NSMakeRect(0, 0, width, height));

    // Accent geometric stripe / radial backdrop burst
    const accentGrad = $.NSGradient.alloc.initWithStartingColorEndingColor(
      hexToNSColor(pal.accent || "#FF6B6B", 0.36),
      hexToNSColor(pal.bg || "#151921", 0.0)
    );
    if (accentGrad) {
      accentGrad.drawFromCenterRadiusToCenterRadiusOptions(
        $.NSMakePoint(isPortrait ? width * 0.5 : width * 0.32, isPortrait ? height * 0.62 : height * 0.50),
        40,
        $.NSMakePoint(isPortrait ? width * 0.5 : width * 0.32, isPortrait ? height * 0.62 : height * 0.50),
        Math.max(width, height) * 0.55,
        0
      );
    }
    bgCanvas.unlockFocus;
    let out = nsImageToCI(bgCanvas, width, height);

    // 2. Subject cutout (if maskImg exists) OR tilted Polaroid portrait frame
    if (heroImg && maskImg) {
      const freezeScale = 1.0 + 0.06 * easeOutCubic(clamp(p / 0.22, 0, 1));
      let subject = aspectFillZoomCI(heroImg, freezeScale, width, height);
      if (root.TripStyleEngine && root.TripStyleEngine.applyStyleGrade) {
        subject = root.TripStyleEngine.applyStyleGrade(subject, style);
      }
      const scaledMask = aspectFillZoomCI(maskImg, freezeScale, width, height);

      // Crisp white sitcom cutout outline around silhouette
      const dilate = $.CIFilter.filterWithName("CIGaussianBlur");
      if (dilate) {
        dilate.setValueForKey(scaledMask.imageByClampingToExtent, "inputImage");
        dilate.setValueForKey(7.0, "inputRadius");
        const whiteCard = $.CIImage.imageWithColor($.CIColor.colorWithRedGreenBlueAlpha(1, 1, 1, 1)).imageByCroppingToRect(rect);
        const outlineBlend = $.CIFilter.filterWithName("CIBlendWithMask");
        if (outlineBlend) {
          outlineBlend.setValueForKey(whiteCard, "inputImage");
          outlineBlend.setValueForKey(out, "inputBackgroundImage");
          outlineBlend.setValueForKey(dilate.outputImage.imageByCroppingToRect(rect), "inputMaskImage");
          out = outlineBlend.outputImage.imageByCroppingToRect(rect);
        }
      }
      const fgBlend = $.CIFilter.filterWithName("CIBlendWithMask");
      if (fgBlend) {
        fgBlend.setValueForKey(subject, "inputImage");
        fgBlend.setValueForKey(out, "inputBackgroundImage");
        fgBlend.setValueForKey(scaledMask, "inputMaskImage");
        out = fgBlend.outputImage.imageByCroppingToRect(rect);
      }
    } else if (heroImg) {
      // Tilted Polaroid portrait frame on left/upper half
      let gradedHero = heroImg;
      if (root.TripStyleEngine && root.TripStyleEngine.applyStyleGrade) {
        gradedHero = root.TripStyleEngine.applyStyleGrade(heroImg, style);
      }
      const pop = dampedSpring(clamp(p / 0.30, 0, 1));
      const cardW = Math.round(isPortrait ? width * 0.62 : height * 0.56);
      const cardH = Math.round(cardW * 1.18);
      const margin = 48;
      const boxW = cardW + margin * 2;
      const boxH = cardH + margin * 2;
      const padS = Math.round(cardW * 0.055);
      const padB = Math.round(cardH * 0.16);
      const phW = cardW - 2 * padS;
      const phH = cardH - padS - padB;

      const pCard = $.NSImage.alloc.initWithSize($.NSMakeSize(boxW, boxH));
      pCard.lockFocus;
      const sh = $.NSShadow.alloc.init;
      sh.shadowOffset = $.NSMakeSize(6, -12);
      sh.shadowBlurRadius = 26;
      sh.shadowColor = $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0, 0, 0, 0.45);
      sh.set;
      $.NSColor.colorWithCalibratedRedGreenBlueAlpha(0.985, 0.978, 0.96, 1.0).setFill;
      $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius($.NSMakeRect(margin, margin, cardW, cardH), 8, 8).fill;
      pCard.unlockFocus;

      const cardCI = nsImageToCI(pCard, boxW, boxH);
      const mediaCI = aspectFillZoomCI(gradedHero, 1.02, phW, phH)
        .imageByApplyingTransform($.CGAffineTransformMakeTranslation(margin + padS, margin + padB));
      const fullCard = mediaCI.imageByCompositingOverImage(cardCI).imageByCroppingToRect($.CGRectMake(0, 0, boxW, boxH));

      const rad = (-4.2 * Math.PI) / 180.0;
      const sc = 0.88 + 0.12 * pop;
      const cosR = Math.cos(rad) * sc;
      const sinR = Math.sin(rad) * sc;
      const cx = isPortrait ? width * 0.50 : width * 0.30;
      const cy = isPortrait ? height * 0.64 : height * 0.50;
      const tx = cx - (cosR * boxW * 0.5 - sinR * boxH * 0.5);
      const ty = cy - (sinR * boxW * 0.5 + cosR * boxH * 0.5);
      out = fullCard.imageByApplyingTransform($.CGAffineTransformMake(cosR, sinR, -sinR, cosR, tx, ty))
        .imageByCompositingOverImage(out).imageByCroppingToRect(rect);
    }

    // 3. Nameplate & Role Banner (bouncy slide-in)
    const nameEase = dampedSpring(clamp((p - 0.14) / 0.30, 0, 1));
    const hud = $.NSImage.alloc.initWithSize($.NSMakeSize(width, height));
    hud.lockFocus;

    const panelX = isPortrait ? Math.round(width * 0.10) : Math.round(width * 0.54);
    const panelMaxW = isPortrait ? Math.round(width * 0.80) : Math.round(width * 0.39);
    const baseY = isPortrait ? Math.round(height * 0.24) : Math.round(height * 0.44);
    const xSlide = (1.0 - nameEase) * 90.0;

    // Name sticker block (measured first so role pill clears nameplate cleanly on all font sizes)
    const nFont = fitFontSize(nameStr, typo.display_font || "AvenirNext-Heavy", 68, panelMaxW - 36, 32);
    const accentHex = pal.accent || "#FF6B6B";
    const accentIsLight = isLightColor(accentHex);
    const nTextColor = accentIsLight ? hexToNSColor("#14161A", 1.0) : $.NSColor.whiteColor;
    const nShadow = accentIsLight ? null : makeTextShadow(0.45, 10, -3);
    const nAttrs = makeTextAttrs(nFont, nTextColor, nShadow);
    const nSz = $(nameStr).sizeWithAttributes(nAttrs);
    const npW = Math.min(panelMaxW, nSz.width + 48);
    const npH = nSz.height + 34;

    // Role accent pill cleanly above nameplate (zero overlap)
    const rFont = resolveFont(typo.meta_font || "HelveticaNeue-Bold", 20, $.NSFontWeightBold);
    const roleBgHex = pal.accent_secondary || pal.accent || "#06D6A0";
    const rAttrs = makeTextAttrs(rFont, hexToNSColor(isLightColor(roleBgHex) ? "#14161A" : "#FFFFFF", 1.0), null);
    const rSz = $(roleStr).sizeWithAttributes(rAttrs);
    const rpW = rSz.width + 36;
    const rpH = 40;
    const roleY = baseY + 12 + npH + 18;
    hexToNSColor(roleBgHex, nameEase).setFill;
    $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius($.NSMakeRect(panelX + xSlide, roleY, rpW, rpH), 10, 10).fill;
    $(roleStr).drawAtPointWithAttributes($.NSMakePoint(panelX + xSlide + 18, roleY + 8), rAttrs);

    hexToNSColor(accentHex, nameEase).setFill;
    $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius($.NSMakeRect(panelX + xSlide, baseY + 12, npW, npH), 14, 14).fill;
    $(nameStr).drawAtPointWithAttributes($.NSMakePoint(panelX + xSlide + 24, baseY + 26), nAttrs);

    // Playful nickname subtitle
    if (nickStr) {
      const kFont = fitFontSize(nickStr, typo.accent_font || "Georgia-Italic", 30, panelMaxW, 18);
      const isLightBg = isLightColor(pal.bg || "#151921");
      const kAttrs = makeTextAttrs(kFont, hexToNSColor(isLightBg ? "#1E2430" : "#F5F2EB", 0.94 * nameEase), isLightBg ? null : makeTextShadow(0.5, 8, -2));
      $(nickStr).drawAtPointWithAttributes($.NSMakePoint(panelX + xSlide + 6, baseY - 40), kAttrs);
    }

    hud.unlockFocus;
    out = nsImageToCI(hud, width, height).imageByCompositingOverImage(out).imageByCroppingToRect(rect);

    // Quick camera shutter flash snap at p ≈ 0.14
    const snap = Math.exp(-Math.pow((p - 0.14) / 0.045, 2));
    if (snap > 0.04) {
      const flash = $.CIImage.imageWithColor($.CIColor.colorWithRedGreenBlueAlpha(1, 1, 1, clamp(snap * 0.55, 0, 0.65))).imageByCroppingToRect(rect);
      out = flash.imageByCompositingOverImage(out).imageByCroppingToRect(rect);
    }

    return out.imageByCroppingToRect(rect);
  }

  // =========================================================================
  // 6. BLOOPER TITLE CARD (Playful Post-Credits Outtakes Stinger)
  // =========================================================================
  function renderBlooperTitleCard(shot, progress, width, height, backdropImg, style) {
    const pal = (style && style.palette) || {};
    const typo = (style && style.typography) || {};
    const hasBackdrop = Boolean(backdropImg);
    const p = clamp(progress || 0, 0, 1);
    const sp = dampedSpring(clamp(p / 0.28, 0, 1));

    const canvas = $.NSImage.alloc.initWithSize($.NSMakeSize(width, height));
    canvas.lockFocus;

    hexToNSColor("#111318", hasBackdrop ? 0.64 : 1.0).setFill;
    $.NSBezierPath.fillRect($.NSMakeRect(0, 0, width, height));

    // Retro clapperboard diagonal stripe bars (top & bottom)
    const stripeH = 38;
    const stripeW = 54;
    const drawClapperBar = function (yPos) {
      $.NSGraphicsContext.saveGraphicsState;
      $.NSBezierPath.clipRect($.NSMakeRect(0, yPos, width, stripeH));
      hexToNSColor("#1A1D24", 1.0).setFill;
      $.NSBezierPath.fillRect($.NSMakeRect(0, yPos, width, stripeH));
      const shift = Math.round(p * 120) % (stripeW * 2);
      for (let x = -stripeW * 2 + shift; x < width + stripeW * 2; x += stripeW * 2) {
        const para = $.NSBezierPath.bezierPath;
        para.moveToPoint($.NSMakePoint(x, yPos));
        para.lineToPoint($.NSMakePoint(x + stripeW, yPos));
        para.lineToPoint($.NSMakePoint(x + stripeW + 24, yPos + stripeH));
        para.lineToPoint($.NSMakePoint(x + 24, yPos + stripeH));
        para.closePath;
        hexToNSColor(pal.accent || "#FFD166", 0.95).setFill;
        para.fill;
      }
      $.NSGraphicsContext.restoreGraphicsState;
    };
    drawClapperBar(height - stripeH);
    drawClapperBar(0);

    // Playful tilted centerpiece badge
    $.NSGraphicsContext.saveGraphicsState;
    const xf = $.NSAffineTransform.transform;
    xf.translateXByYBy(width * 0.5, height * 0.5);
    xf.rotateByDegrees(-2.2);
    xf.scaleBy(0.84 + 0.16 * sp);
    xf.translateXByYBy(-width * 0.5, -height * 0.5);
    xf.concat;

    const eyeStr = String(shot.eyebrow || "POST-CREDITS STINGER").toUpperCase();
    const titleStr = String(shot.title || "OUTTAKES & BLOOPERS").toUpperCase();
    const subStr = shot.subtitle || "THE TAKES THAT ALMOST DIDN'T MAKE THE CUT";

    const eFont = resolveFont("Menlo-Bold", 22, $.NSFontWeightBold);
    const eAttrs = makeTextAttrs(eFont, hexToNSColor("#111318", 1.0), null);
    const eSz = $(eyeStr).sizeWithAttributes(eAttrs);
    const epW = eSz.width + 40;
    hexToNSColor(pal.accent_secondary || "#06D6A0", 1.0).setFill;
    $.NSBezierPath.bezierPathWithRoundedRectXRadiusYRadius($.NSMakeRect((width - epW) * 0.5, height * 0.61, epW, 42), 10, 10).fill;
    $(eyeStr).drawAtPointWithAttributes($.NSMakePoint((width - eSz.width) * 0.5, height * 0.61 + 8), eAttrs);

    const tFont = fitFontSize(titleStr, typo.display_font || "AvenirNext-Heavy", height > width ? 64 : 88, width * 0.82, 34);
    const tAttrs = makeTextAttrs(tFont, $.NSColor.whiteColor, makeTextShadow(0.65, 16, -4));
    const tSz = $(titleStr).sizeWithAttributes(tAttrs);
    $(titleStr).drawAtPointWithAttributes($.NSMakePoint((width - tSz.width) * 0.5, height * 0.46), tAttrs);

    const sFont = fitFontSize(subStr, typo.meta_font || "HelveticaNeue-Bold", 24, width * 0.80, 16);
    const sAttrs = makeTextAttrs(sFont, hexToNSColor(pal.accent || "#FFD166", 0.96), makeTextShadow(0.5, 8, -2));
    const sSz = $(subStr).sizeWithAttributes(sAttrs);
    $(subStr).drawAtPointWithAttributes($.NSMakePoint((width - sSz.width) * 0.5, height * 0.37), sAttrs);

    $.NSGraphicsContext.restoreGraphicsState;
    canvas.unlockFocus;

    const overlayCI = nsImageToCI(canvas, width, height);
    if (!hasBackdrop) return overlayCI;
    const bgCI = buildBlurredBackdropCI(backdropImg, p, 16.0, 0.05, style, width, height);
    return overlayCI.imageByCompositingOverImage(bgCI).imageByCroppingToRect($.CGRectMake(0, 0, width, height));
  }

  // Unified Card Dispatcher
  function renderCardFrame(shot, progress, width, height, assets, style) {
    const gType = String((shot && shot.graphic_type) || "chapter_title").toLowerCase().trim();
    const backdropImg = assets && assets.backdropImg ? assets.backdropImg : null;
    const heroImg = assets && assets.heroImg ? assets.heroImg : backdropImg;
    const maskImg = assets && assets.maskImg ? assets.maskImg : null;

    switch (gType) {
      case "route_map":
        return renderRouteMapCard(shot, progress, width, height, backdropImg, style);
      case "text_behind_subject":
        return renderTextBehindSubjectCard(shot, progress, width, height, heroImg, maskImg, style);
      case "trip_wrapped":
        return renderTripWrappedCard(shot, progress, width, height, backdropImg, style);
      case "cast_intro":
        return renderCastIntroCard(shot, progress, width, height, heroImg, maskImg, style);
      case "blooper_title":
        return renderBlooperTitleCard(shot, progress, width, height, backdropImg, style);
      case "chapter_title":
      default:
        return renderChapterTitleCard(shot, progress, width, height, backdropImg, style);
    }
  }

  const api = {
    renderCardFrame: renderCardFrame,
    renderChapterTitleCard: renderChapterTitleCard,
    renderTextBehindSubjectCard: renderTextBehindSubjectCard,
    renderRouteMapCard: renderRouteMapCard,
    renderTripWrappedCard: renderTripWrappedCard,
    renderCastIntroCard: renderCastIntroCard,
    renderBlooperTitleCard: renderBlooperTitleCard,
  };

  if (typeof exports !== "undefined") {
    const ks = Object.keys(api);
    for (let i = 0; i < ks.length; i++) exports[ks[i]] = api[ks[i]];
  }
  root.TripCards = api;
})(this);
