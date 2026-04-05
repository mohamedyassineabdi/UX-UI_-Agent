from __future__ import annotations

from typing import Any, Dict, List


async def detect_runtime_motion(page, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Detect repeated motion and auto-updating visual changes over time.

    This complements CSS-based animation checks by catching:
    - JS-driven movement
    - auto-playing sliders/carousels
    - continuous transforms
    - repeated layout shifts

    Returns a page-level summary plus suspicious elements.
    """
    runtime_cfg = (config.get("presentationChecks") or {}).get("runtimeMotion") or {}

    enabled = runtime_cfg.get("enabled", True)
    if not enabled:
        return {
            "enabled": False,
            "checked": False,
            "suspiciousElements": [],
            "summary": {
                "sampleCount": 0,
                "suspiciousCount": 0,
            },
        }

    sample_interval_ms = int(runtime_cfg.get("sampleIntervalMs", 350))
    sample_count = int(runtime_cfg.get("sampleCount", 4))
    motion_threshold_px = float(runtime_cfg.get("motionThresholdPx", 6))
    opacity_threshold = float(runtime_cfg.get("opacityThreshold", 0.2))
    max_elements = int(runtime_cfg.get("maxElements", 120))

    js = """
    async ({ sampleIntervalMs, sampleCount, motionThresholdPx, opacityThreshold, maxElements }) => {
      const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

      function isVisible(el) {
        const style = window.getComputedStyle(el);
        if (!style) return false;
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        if (parseFloat(style.opacity || '1') === 0) return false;

        const rect = el.getBoundingClientRect();
        if (!rect || rect.width <= 0 || rect.height <= 0) return false;
        return true;
      }

      function getLabel(el) {
        return (
          el.getAttribute('aria-label') ||
          el.getAttribute('title') ||
          el.innerText ||
          el.textContent ||
          ''
        ).trim().slice(0, 120);
      }

      function getXPathHint(el) {
        if (el.id) return `${el.tagName.toLowerCase()}#${el.id}`;
        const cls = (el.className && typeof el.className === 'string')
          ? el.className.trim().split(/\\s+/).slice(0, 4).join('.')
          : '';
        return cls
          ? `${el.tagName.toLowerCase()}.${cls}`
          : el.tagName.toLowerCase();
      }

      const selector = [
        'a',
        'button',
        'input',
        'select',
        'textarea',
        '[role="button"]',
        '[role="dialog"]',
        '[role="alert"]',
        '[role="status"]',
        '[class*="slider"]',
        '[class*="carousel"]',
        '[class*="marquee"]',
        '[class*="banner"]',
        '[class*="toast"]',
        '[class*="modal"]',
        '[class*="popup"]',
        '[class*="animation"]',
        '[style*="animation"]'
      ].join(',');

      let candidates = Array.from(document.querySelectorAll(selector))
        .filter(isVisible)
        .slice(0, maxElements);

      const snapshots = [];

      for (let i = 0; i < sampleCount; i++) {
        const frame = candidates.map((el) => {
          const rect = el.getBoundingClientRect();
          const style = window.getComputedStyle(el);

          return {
            xpathHint: getXPathHint(el),
            tag: el.tagName.toLowerCase(),
            text: getLabel(el),
            x: rect.x,
            y: rect.y,
            width: rect.width,
            height: rect.height,
            opacity: parseFloat(style.opacity || '1'),
            transform: style.transform || 'none',
            animation: style.animation || 'none',
            transition: style.transition || 'none'
          };
        });

        snapshots.push(frame);

        if (i < sampleCount - 1) {
          await sleep(sampleIntervalMs);
        }
      }

      const suspicious = [];

      if (snapshots.length >= 2) {
        const baseline = snapshots[0];

        for (let idx = 0; idx < baseline.length; idx++) {
          const first = baseline[idx];
          let maxDelta = 0;
          let opacityDelta = 0;
          let transformChanged = false;

          for (let s = 1; s < snapshots.length; s++) {
            const current = snapshots[s][idx];
            if (!current) continue;

            const dx = Math.abs(current.x - first.x);
            const dy = Math.abs(current.y - first.y);
            const dw = Math.abs(current.width - first.width);
            const dh = Math.abs(current.height - first.height);

            maxDelta = Math.max(maxDelta, dx, dy, dw, dh);
            opacityDelta = Math.max(opacityDelta, Math.abs((current.opacity ?? 1) - (first.opacity ?? 1)));

            if ((current.transform || 'none') !== (first.transform || 'none')) {
              transformChanged = true;
            }
          }

          const reasons = [];
          let severity = 'medium';

          if (maxDelta >= motionThresholdPx) {
            reasons.push('runtime-motion');
          }
          if (opacityDelta >= opacityThreshold) {
            reasons.push('runtime-opacity-change');
            severity = 'high';
          }
          if (transformChanged) {
            reasons.push('runtime-transform-change');
          }

          if (reasons.length > 0) {
            suspicious.push({
              xpathHint: first.xpathHint,
              tag: first.tag,
              text: first.text,
              reasons,
              severity,
              maxDelta,
              opacityDelta,
              animation: first.animation,
              transition: first.transition,
              transform: first.transform
            });
          }
        }
      }

      return {
        enabled: true,
        checked: true,
        suspiciousElements: suspicious,
        summary: {
          sampleCount,
          candidateCount: candidates.length,
          suspiciousCount: suspicious.length,
          sampleIntervalMs
        }
      };
    }
    """

    try:
        result = await page.evaluate(
            js,
            {
                "sampleIntervalMs": sample_interval_ms,
                "sampleCount": sample_count,
                "motionThresholdPx": motion_threshold_px,
                "opacityThreshold": opacity_threshold,
                "maxElements": max_elements,
            },
        )
        return result
    except Exception as error:
        return {
            "enabled": True,
            "checked": False,
            "error": str(error),
            "suspiciousElements": [],
            "summary": {
                "sampleCount": sample_count,
                "suspiciousCount": 0,
            },
        }