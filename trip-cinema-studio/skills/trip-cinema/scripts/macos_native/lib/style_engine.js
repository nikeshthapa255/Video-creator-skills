/**
 * Trip Cinema Studio v0.2.0 — Modular Style Engine (macOS Native JXA CoreImage/AppKit).
 *
 * Loads declarative styles/<style_id>.json presets (with safe fallback defaults),
 * resolves palette colors & macOS fonts, and applies style-driven color grading
 * and procedural CoreImage textures (grain, halation, vignette, gate weave, letterbox).
 */

(function (root) {
  "use strict";

  const DEFAULT_STYLE = {
    style_id: "cinema_doc",
    display_name: "Cinema Documentary",
    palette: {
      bg: "#0F1115",
      surface: "#181B22",
      text_primary: "#F5F2EB",
      text_secondary: "#B9B2A3",
      accent: "#D4AF37",
      accent_secondary: "#7A9E9F",
      border: "#2E333D",
      swatches: ["#0F1115", "#D4AF37", "#F5F2EB", "#7A9E9F", "#3A404D"],
    },
    typography: {
      display_font: "Baskerville-SemiBold",
      meta_font: "HelveticaNeue-Medium",
      accent_font: "Georgia-Italic",
      display_uppercase: false,
      meta_uppercase: true,
      display_letter_spacing: 0.03,
      meta_letter_spacing: 0.22,
    },
    color_grade: {
      look_id: "warm_film",
      coreimage: {
        contrast: 1.06,
        saturation: 1.06,
        brightness: 0.01,
        temperature_target: 5800,
        tint_target: 6,
        sepia_intensity: 0.0,
        exposure_ev: 0.05,
        fade_blacks: 0.02,
      },
    },
    textures: {
      grain_amount: 0.14,
      vignette_intensity: 0.55,
      vignette_radius: 1.6,
      gate_weave_px: 0.0,
      overlay_fps_step: 0,
      letterbox_ratio: 0,
      frame_style: "editorial_clean",
      halation_amount: 0.08,
    },
    layouts: {
      default_portrait_layout: "split_diptych_pair",
      supported_layouts: [
        "single",
        "split_diptych_pair",
        "blurred_bg_pillarbox",
        "split_2up_video",
        "triptych_3up_video",
        "bento_living_grid",
      ],
      gutter_px: 10,
      corner_radius_px: 0,
      border_px: 0,
      polaroid_tilt_deg: 0.0,
      pillarbox_dim_alpha: 0.28,
      pillarbox_blur_sigma: 28.0,
      allow_multi_video_grids: true,
    },
    cards: {
      chapter_card_variant: "editorial_gold_serif",
      backdrop_blur_sigma: 22.0,
      backdrop_dim_alpha: 0.45,
      backdrop_zoom_pct: 0.06,
      use_roman_numerals: false,
      show_divider_rule: true,
      show_date_stamp: false,
      route_map_skin: "dark_topo",
      cast_intro_style: "editorial_frame",
    },
    transitions: {
      default_in_chapter: "cut",
      default_between_chapters: "dissolve",
      allowed: ["cut", "dissolve", "light_leak", "zoom_punch"],
      default_duration_sec: 0.5,
      signature_label: "J/L Bridges + Soft Dissolves",
    },
    pacing_rules: {
      target_bpm_min: 70,
      target_bpm_max: 95,
      max_consecutive_photos: 2,
      prefer_static_frames: false,
    },
    audio: {
      default_bgm_volume: 0.78,
      duck_bgm_db: -14.0,
      sonic_postcard_nat_gain_db: 1.0,
      default_nat_bed_volume: 0.24,
      default_j_cut_sec: 0.8,
      default_l_cut_sec: 0.7,
      sfx_pack: "ambient_foley",
    },
  };

  const LEGACY_LOOK_PRESETS = {
    warm_film: {
      contrast: 1.06,
      saturation: 1.08,
      brightness: 0.01,
      temperature_target: 5600,
      tint_target: 8,
      sepia_intensity: 0.0,
      exposure_ev: 0.02,
      fade_blacks: 0.02,
    },
    moody_teal_orange: {
      contrast: 1.14,
      saturation: 1.04,
      brightness: -0.02,
      temperature_target: 7100,
      tint_target: -10,
      sepia_intensity: 0.0,
      exposure_ev: 0.0,
      fade_blacks: 0.0,
    },
    soft_atmospheric: {
      contrast: 0.93,
      saturation: 0.90,
      brightness: 0.03,
      temperature_target: 6200,
      tint_target: 4,
      sepia_intensity: 0.0,
      exposure_ev: 0.04,
      fade_blacks: 0.04,
    },
    clean_natural: {
      contrast: 1.03,
      saturation: 1.03,
      brightness: 0.0,
      temperature_target: 6500,
      tint_target: 0,
      sepia_intensity: 0.0,
      exposure_ev: 0.0,
      fade_blacks: 0.0,
    },
  };

  function deepMerge(base, override) {
    if (!override || typeof override !== "object") return cloneObj(base);
    const out = cloneObj(base);
    const keys = Object.keys(override);
    for (let i = 0; i < keys.length; i++) {
      const k = keys[i];
      const v = override[k];
      if (v === undefined || v === null) continue;
      if (Array.isArray(v)) {
        out[k] = v.slice();
      } else if (typeof v === "object" && out[k] && typeof out[k] === "object" && !Array.isArray(out[k])) {
        out[k] = deepMerge(out[k], v);
      } else {
        out[k] = v;
      }
    }
    return out;
  }

  function cloneObj(o) {
    return JSON.parse(JSON.stringify(o));
  }

  function clamp(v, lo, hi) {
    return Math.min(hi, Math.max(lo, v));
  }

  function hexToRGBA(hex, alphaOverride) {
    let s = String(hex || "#0F1115").trim();
    if (s.charAt(0) === "#") s = s.slice(1);
    if (s.length === 3) {
      s = s[0] + s[0] + s[1] + s[1] + s[2] + s[2];
    }
    let r = 15, g = 17, b = 21, a = 1.0;
    if (s.length === 6 || s.length === 8) {
      const nr = parseInt(s.slice(0, 2), 16);
      const ng = parseInt(s.slice(2, 4), 16);
      const nb = parseInt(s.slice(4, 6), 16);
      if (!isNaN(nr)) r = nr;
      if (!isNaN(ng)) g = ng;
      if (!isNaN(nb)) b = nb;
      if (s.length === 8) {
        const na = parseInt(s.slice(6, 8), 16);
        if (!isNaN(na)) a = na / 255.0;
      }
    }
    if (alphaOverride !== undefined && alphaOverride !== null) {
      a = clamp(Number(alphaOverride), 0.0, 1.0);
    }
    return { r: r / 255.0, g: g / 255.0, b: b / 255.0, a: a };
  }

  function hexToNSColor(hex, alphaOverride) {
    const c = hexToRGBA(hex, alphaOverride);
    return $.NSColor.colorWithCalibratedRedGreenBlueAlpha(c.r, c.g, c.b, c.a);
  }

  function hexToCIColor(hex, alphaOverride) {
    const c = hexToRGBA(hex, alphaOverride);
    return $.CIColor.colorWithRedGreenBlueAlpha(c.r, c.g, c.b, c.a);
  }

  function isLightColor(hex) {
    const c = hexToRGBA(hex, 1.0);
    const lum = 0.299 * c.r + 0.587 * c.g + 0.114 * c.b;
    return lum > 0.58;
  }

  function resolveFont(fontSpec, size, fallbackWeight) {
    const sz = Math.max(8, Number(size) || 24);
    const candidates = [];
    if (Array.isArray(fontSpec)) {
      for (let i = 0; i < fontSpec.length; i++) candidates.push(String(fontSpec[i]));
    } else if (typeof fontSpec === "string" && fontSpec.length > 0) {
      candidates.push(fontSpec);
    }
    // Curated macOS fallback stack for known style fonts
    const fallbacks = {
      "Baskerville-SemiBold": ["Georgia-Bold", "TimesNewRomanPS-BoldMT"],
      "AmericanTypewriter-Bold": ["Courier-Bold", "Menlo-Bold"],
      "Futura-Bold": ["AvenirNext-Heavy", "HelveticaNeue-Bold"],
      "HelveticaNeue-CondensedBlack": ["HelveticaNeue-Bold", "AvenirNext-Heavy"],
      "AvenirNext-Heavy": ["HelveticaNeue-Bold", "Futura-Bold"],
      "MarkerFelt-Wide": ["Noteworthy-Bold", "Georgia-Bold"],
      "Menlo-Regular": ["Courier", "Monaco"],
      "Menlo-Bold": ["Courier-Bold", "Menlo-Regular"],
    };
    const expanded = [];
    for (let i = 0; i < candidates.length; i++) {
      const name = candidates[i];
      expanded.push(name);
      if (fallbacks[name]) {
        for (let j = 0; j < fallbacks[name].length; j++) expanded.push(fallbacks[name][j]);
      }
    }
    for (let i = 0; i < expanded.length; i++) {
      const f = $.NSFont.fontWithNameSize(expanded[i], sz);
      if (f) return f;
    }
    return $.NSFont.systemFontOfSizeWeight(sz, fallbackWeight !== undefined ? fallbackWeight : $.NSFontWeightBold);
  }

  function loadStylePack(styleId, stylesDir, fallbackLookId, overrides) {
    const fm = $.NSFileManager.defaultManager;
    const requestedId = styleId || "cinema_doc";
    let loaded = null;

    if (stylesDir) {
      const path = stylesDir.replace(/\/+$/, "") + "/" + requestedId + ".json";
      if (fm.fileExistsAtPath(path)) {
        const nsStr = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
        if (nsStr) {
          try {
            loaded = JSON.parse(ObjC.unwrap(nsStr));
          } catch (e) {
            loaded = null;
          }
        }
      }
    }

    let style = loaded ? deepMerge(DEFAULT_STYLE, loaded) : cloneObj(DEFAULT_STYLE);
    if (!loaded && requestedId && requestedId !== "cinema_doc") {
      style.style_id = requestedId;
    }

    // Legacy v1.0 look_id / color_look override if explicitly requested (or when no style_id json was loaded)
    if (fallbackLookId && LEGACY_LOOK_PRESETS[fallbackLookId]) {
      if (!styleId || !loaded) {
        style.color_grade = style.color_grade || {};
        style.color_grade.look_id = fallbackLookId;
        style.color_grade.coreimage = cloneObj(LEGACY_LOOK_PRESETS[fallbackLookId]);
      }
    }

    if (overrides && typeof overrides === "object") {
      style = deepMerge(style, overrides);
    }
    return style;
  }

  function resolveGradeParams(style, lookOverride) {
    if (lookOverride && LEGACY_LOOK_PRESETS[lookOverride]) {
      return LEGACY_LOOK_PRESETS[lookOverride];
    }
    const cg = (style && style.color_grade) ? style.color_grade : {};
    if (cg.coreimage) {
      return cg.coreimage;
    }
    // Compatibility with alternate nested keys (ci_controls / ci_temp_tint)
    if (cg.ci_controls || cg.ci_temp_tint) {
      const ctrl = cg.ci_controls || {};
      const tt = cg.ci_temp_tint || {};
      return {
        contrast: ctrl.contrast !== undefined ? ctrl.contrast : 1.03,
        saturation: ctrl.saturation !== undefined ? ctrl.saturation : 1.03,
        brightness: ctrl.brightness !== undefined ? ctrl.brightness : 0.0,
        temperature_target: tt.target_kelvin !== undefined ? tt.target_kelvin : 6500,
        tint_target: tt.tint !== undefined ? tt.tint : 0,
        sepia_intensity: 0.0,
        exposure_ev: 0.0,
        fade_blacks: 0.0,
        ci_photo_effect: cg.ci_photo_effect || null,
      };
    }
    if (cg.look_id && LEGACY_LOOK_PRESETS[cg.look_id]) {
      return LEGACY_LOOK_PRESETS[cg.look_id];
    }
    return LEGACY_LOOK_PRESETS.clean_natural;
  }

  function applyStyleGrade(ciImg, style, lookOverride) {
    if (!ciImg) return ciImg;
    const origExt = ciImg.extent;
    const p = resolveGradeParams(style, lookOverride);
    let out = ciImg;

    const ev = Number(p.exposure_ev || 0.0);
    if (Math.abs(ev) > 0.004) {
      const expF = $.CIFilter.filterWithName("CIExposureAdjust");
      if (expF) {
        expF.setValueForKey(out, "inputImage");
        expF.setValueForKey(ev, "inputEV");
        out = expF.outputImage;
      }
    }

    const contrast = p.contrast !== undefined ? Number(p.contrast) : 1.03;
    const saturation = p.saturation !== undefined ? Number(p.saturation) : 1.03;
    const brightness = p.brightness !== undefined ? Number(p.brightness) : 0.0;
    const cc = $.CIFilter.filterWithName("CIColorControls");
    if (cc) {
      cc.setValueForKey(out, "inputImage");
      cc.setValueForKey(contrast, "inputContrast");
      cc.setValueForKey(saturation, "inputSaturation");
      cc.setValueForKey(brightness, "inputBrightness");
      out = cc.outputImage;
    }

    const tempTarget = p.temperature_target !== undefined ? Number(p.temperature_target) : 6500;
    const tintTarget = p.tint_target !== undefined ? Number(p.tint_target) : 0;
    if (Math.abs(tempTarget - 6500) > 15 || Math.abs(tintTarget) > 0.5) {
      const tt = $.CIFilter.filterWithName("CITemperatureAndTint");
      if (tt) {
        tt.setValueForKey(out, "inputImage");
        tt.setValueForKey($.CIVector.vectorWithXY(6500, 0), "inputNeutral");
        tt.setValueForKey($.CIVector.vectorWithXY(tempTarget, tintTarget), "inputTargetNeutral");
        out = tt.outputImage;
      }
    }

    const sepia = Number(p.sepia_intensity || 0.0);
    if (sepia > 0.005) {
      const sep = $.CIFilter.filterWithName("CISepiaTone");
      if (sep) {
        sep.setValueForKey(out, "inputImage");
        sep.setValueForKey(clamp(sepia, 0.0, 1.0), "inputIntensity");
        out = sep.outputImage;
      }
    }

    // Matte lifted film blacks (genuine analog toe lift without blowing highlights)
    const fadeBlacks = Number(p.fade_blacks || 0.0);
    if (fadeBlacks > 0.004) {
      const fb = clamp(fadeBlacks, 0.0, 0.25);
      const scale = 1.0 - fb;
      const mat = $.CIFilter.filterWithName("CIColorMatrix");
      if (mat) {
        mat.setValueForKey(out, "inputImage");
        mat.setValueForKey($.CIVector.vectorWithXYZW(scale, 0, 0, 0), "inputRVector");
        mat.setValueForKey($.CIVector.vectorWithXYZW(0, scale, 0, 0), "inputGVector");
        mat.setValueForKey($.CIVector.vectorWithXYZW(0, 0, scale, 0), "inputBVector");
        mat.setValueForKey($.CIVector.vectorWithXYZW(0, 0, 0, 1), "inputAVector");
        mat.setValueForKey($.CIVector.vectorWithXYZW(fb * 0.92, fb * 0.88, fb * 0.82, 0), "inputBiasVector");
        out = mat.outputImage;
      }
    }

    const photoEffect = p.ci_photo_effect || (style && style.color_grade && style.color_grade.ci_photo_effect);
    if (photoEffect && typeof photoEffect === "string") {
      const pe = $.CIFilter.filterWithName(photoEffect);
      if (pe) {
        pe.setValueForKey(out, "inputImage");
        out = pe.outputImage;
      }
    }

    return out.imageByCroppingToRect(origExt);
  }

  function applyStyleTextures(ciImg, frameIdx, width, height, style, fps) {
    if (!ciImg) return ciImg;
    const tex = (style && style.textures) ? style.textures : {};
    const rect = $.CGRectMake(0, 0, width, height);
    const outFps = fps || 30;
    const stepFps = Number(tex.overlay_fps_step || 0);
    const effectiveFrame = stepFps > 0
      ? Math.floor(frameIdx / Math.max(1, Math.round(outFps / stepFps)))
      : frameIdx;

    let out = ciImg.imageByCroppingToRect(rect);

    // 1. Subtle organic film gate weave micro-jitter (edge-clamped so zero black edge flicker)
    const weavePx = Number(tex.gate_weave_px || 0.0);
    if (weavePx > 0.05) {
      const t = effectiveFrame * 0.73;
      const dx = weavePx * (0.62 * Math.sin(t * 1.91) + 0.38 * Math.cos(t * 4.17));
      const dy = weavePx * (0.62 * Math.cos(t * 2.33) + 0.38 * Math.sin(t * 3.71));
      out = out
        .imageByClampingToExtent
        .imageByApplyingTransform($.CGAffineTransformMakeTranslation(dx, dy))
        .imageByCroppingToRect(rect);
    }

    // 2. Subtle warm film halation bloom on highlights (e.g. Super 8 / Cinema Doc)
    const halation = Number(tex.halation_amount || 0.0);
    if (halation >= 0.05) {
      const thresh = $.CIFilter.filterWithName("CIColorMatrix");
      const blur = $.CIFilter.filterWithName("CIGaussianBlur");
      const addBlend = $.CIFilter.filterWithName("CIScreenBlendMode");
      if (thresh && blur && addBlend) {
        // Isolate warm bright highlights by subtracting 0.72 and tinting amber-red
        const gain = clamp(halation * 0.55, 0.02, 0.25);
        thresh.setValueForKey(out, "inputImage");
        thresh.setValueForKey($.CIVector.vectorWithXYZW(gain * 1.35, 0, 0, 0), "inputRVector");
        thresh.setValueForKey($.CIVector.vectorWithXYZW(0, gain * 0.65, 0, 0), "inputGVector");
        thresh.setValueForKey($.CIVector.vectorWithXYZW(0, 0, gain * 0.22, 0), "inputBVector");
        thresh.setValueForKey($.CIVector.vectorWithXYZW(0, 0, 0, 1), "inputAVector");
        thresh.setValueForKey($.CIVector.vectorWithXYZW(-0.72 * gain, -0.72 * gain * 0.65, -0.72 * gain * 0.22, 0), "inputBiasVector");

        blur.setValueForKey(thresh.outputImage.imageByClampingToExtent, "inputImage");
        blur.setValueForKey(16.0, "inputRadius");
        const glow = blur.outputImage.imageByCroppingToRect(rect);

        addBlend.setValueForKey(glow, "inputImage");
        addBlend.setValueForKey(out, "inputBackgroundImage");
        out = addBlend.outputImage.imageByCroppingToRect(rect);
      }
    }

    // 3. Procedural CIRandomGenerator film grain via CIScreenBlendMode
    const grainAmt = Number(tex.grain_amount || 0.0);
    if (grainAmt > 0.01) {
      const rng = $.CIFilter.filterWithName("CIRandomGenerator");
      const gray = $.CIFilter.filterWithName("CIColorControls");
      const alphaScale = $.CIFilter.filterWithName("CIColorMatrix");
      const screen = $.CIFilter.filterWithName("CIScreenBlendMode");
      if (rng && gray && alphaScale && screen) {
        const shiftX = (effectiveFrame * 173) % 997;
        const shiftY = (effectiveFrame * 251) % 991;
        let noise = rng.outputImage
          .imageByApplyingTransform($.CGAffineTransformMakeTranslation(shiftX, shiftY))
          .imageByCroppingToRect(rect);

        gray.setValueForKey(noise, "inputImage");
        gray.setValueForKey(0.0, "inputSaturation");
        gray.setValueForKey(1.25, "inputContrast");
        gray.setValueForKey(-0.12, "inputBrightness");
        noise = gray.outputImage;

        // Subtle organic opacity so blacks stay clean while midtones get tactile grain
        const gOpacity = clamp(grainAmt * 0.24, 0.01, 0.14);
        alphaScale.setValueForKey(noise, "inputImage");
        alphaScale.setValueForKey($.CIVector.vectorWithXYZW(gOpacity, 0, 0, 0), "inputRVector");
        alphaScale.setValueForKey($.CIVector.vectorWithXYZW(0, gOpacity, 0, 0), "inputGVector");
        alphaScale.setValueForKey($.CIVector.vectorWithXYZW(0, 0, gOpacity, 0), "inputBVector");
        alphaScale.setValueForKey($.CIVector.vectorWithXYZW(0, 0, 0, 1.0), "inputAVector");
        alphaScale.setValueForKey($.CIVector.vectorWithXYZW(0, 0, 0, 0), "inputBiasVector");
        const scaledNoise = alphaScale.outputImage.imageByCroppingToRect(rect);

        screen.setValueForKey(scaledNoise, "inputImage");
        screen.setValueForKey(out, "inputBackgroundImage");
        out = screen.outputImage.imageByCroppingToRect(rect);
      }
    }

    // 4. Warm / dark CIVignette
    const vigInt = Number(tex.vignette_intensity || 0.0);
    if (vigInt > 0.02) {
      const vig = $.CIFilter.filterWithName("CIVignette");
      if (vig) {
        vig.setValueForKey(out, "inputImage");
        vig.setValueForKey(clamp(vigInt, 0.0, 2.0), "inputIntensity");
        vig.setValueForKey(Number(tex.vignette_radius || 1.6), "inputRadius");
        out = vig.outputImage.imageByCroppingToRect(rect);
      }
    }

    // 5. Optional anamorphic 2.39:1 letterbox matte (only on landscape canvas)
    const lbRatio = Number(tex.letterbox_ratio || 0.0);
    if (lbRatio > 1.85 && width > height) {
      const canvasRatio = width / height;
      if (lbRatio > canvasRatio + 0.05) {
        const activeH = Math.round(width / lbRatio);
        const barH = Math.max(0, Math.floor((height - activeH) / 2));
        if (barH > 2) {
          const black = $.CIImage.imageWithColor($.CIColor.colorWithRedGreenBlueAlpha(0.0, 0.0, 0.0, 1.0));
          const botBar = black.imageByCroppingToRect($.CGRectMake(0, 0, width, barH));
          const topBar = black.imageByCroppingToRect($.CGRectMake(0, height - barH, width, barH));
          out = botBar.imageByCompositingOverImage(out);
          out = topBar.imageByCompositingOverImage(out);
        }
      }
    }

    return out.imageByCroppingToRect(rect);
  }

  const api = {
    DEFAULT_STYLE: DEFAULT_STYLE,
    LEGACY_LOOK_PRESETS: LEGACY_LOOK_PRESETS,
    deepMerge: deepMerge,
    clamp: clamp,
    hexToRGBA: hexToRGBA,
    hexToNSColor: hexToNSColor,
    hexToCIColor: hexToCIColor,
    isLightColor: isLightColor,
    resolveFont: resolveFont,
    loadStylePack: loadStylePack,
    applyStyleGrade: applyStyleGrade,
    applyStyleTextures: applyStyleTextures,
  };

  if (typeof exports !== "undefined") {
    const ks = Object.keys(api);
    for (let i = 0; i < ks.length; i++) exports[ks[i]] = api[ks[i]];
  }
  root.TripStyleEngine = api;
})(this);
