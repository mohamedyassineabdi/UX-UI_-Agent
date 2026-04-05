const reportData = JSON.parse(document.getElementById("report-data").textContent);
const LIGHTBOX_RENDER_QUEUE = new WeakSet();

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function statusLabel(status) {
  const normalized = String(status || "N/A").toUpperCase();
  if (normalized === "TRUE") return "Pass";
  if (normalized === "FALSE") return "Issue";
  return "N/A";
}

function confidenceTone(confidence) {
  if (confidence >= 80) return "high";
  if (confidence >= 55) return "medium";
  return "low";
}

function renderMetaPills(items) {
  return items
    .filter(Boolean)
    .map((item) => `<span class="meta-pill">${escapeHtml(item)}</span>`)
    .join("");
}

function renderEvidence(items) {
  const list = (items || []).filter(Boolean);
  if (!list.length) {
    return `<p class="empty-note">No explicit evidence captured for this item.</p>`;
  }
  return `<ul class="evidence-list">${list
    .slice(0, 6)
    .map((item) => `<li>${escapeHtml(item)}</li>`)
    .join("")}</ul>`;
}

function renderSourceLinks(sourcePages) {
  const pages = (sourcePages || []).filter((item) => item && (item.name || item.url));
  if (!pages.length) {
    return "";
  }
  return `<div class="source-links">${pages
    .slice(0, 4)
    .map((page) => {
      const label = page.name || page.url;
      if (page.url) {
        return `<a href="${escapeHtml(page.url)}" target="_blank" rel="noreferrer">${escapeHtml(label)}</a>`;
      }
      return `<span>${escapeHtml(label)}</span>`;
    })
    .join("")}</div>`;
}

function renderHeader() {
  const nameTarget = document.getElementById("client-brand-name");
  const logoWrap = document.getElementById("client-brand-logo-wrap");
  const logo = document.getElementById("client-brand-logo");

  if (nameTarget) {
    nameTarget.textContent = reportData.site.displayName || reportData.site.domain || reportData.site.title;
  }

  if (!logoWrap || !logo) return;

  if (reportData.site.logo) {
    logo.src = reportData.site.logo;
    logo.alt = `${reportData.site.displayName || reportData.site.domain || "Client"} logo`;
    logoWrap.classList.remove("is-empty");
  } else {
    logo.removeAttribute("src");
    logo.alt = "";
    logoWrap.classList.add("is-empty");
  }
}

function safeRender(name, fn) {
  try {
    fn();
  } catch (error) {
    console.error(`Report render failed in ${name}`, error);
  }
}

function screenshotFigure(item, compact = false) {
  const shot = item.evidenceShot || item.screenshot;
  const spotlight = item.spotlight;
  if (!shot && !spotlight) {
    return "";
  }

  if (spotlight && spotlight.preRenderedImage) {
    return `
      <button class="shot-frame ${compact ? "compact" : ""}" data-shot="${escapeHtml(spotlight.preRenderedImage)}" data-alt="${escapeHtml(item.criterion || item.name || "Audit screenshot")}">
        <img src="${escapeHtml(spotlight.preRenderedImage)}" alt="${escapeHtml(item.criterion || item.name || "Audit screenshot")}" loading="lazy">
      </button>
    `;
  }

  if (spotlight && spotlight.image) {
    return `
      <button class="shot-frame spotlight-frame ${compact ? "compact" : ""}" data-alt="${escapeHtml(item.criterion || item.name || "Audit screenshot")}">
        <canvas class="spotlight-canvas" data-spotlight='${escapeHtml(JSON.stringify(spotlight))}'></canvas>
      </button>
    `;
  }

  if (shot) {
    const preview = JSON.stringify({
      image: shot,
      frame: { width: 1920, height: 1080 },
    });
    return `
      <button class="shot-frame preview-frame ${compact ? "compact" : ""}" data-alt="${escapeHtml(item.criterion || item.name || "Audit screenshot")}">
        <canvas class="preview-canvas" data-preview='${escapeHtml(preview)}' aria-label="${escapeHtml(item.criterion || item.name || "Audit screenshot")}"></canvas>
      </button>
    `;
  }

  return "";
}

function drawRoundedRect(ctx, x, y, width, height, radius) {
  const safeRadius = Math.max(0, Math.min(radius, width / 2, height / 2));
  ctx.beginPath();
  ctx.moveTo(x + safeRadius, y);
  ctx.lineTo(x + width - safeRadius, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + safeRadius);
  ctx.lineTo(x + width, y + height - safeRadius);
  ctx.quadraticCurveTo(x + width, y + height, x + width - safeRadius, y + height);
  ctx.lineTo(x + safeRadius, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - safeRadius);
  ctx.lineTo(x, y + safeRadius);
  ctx.quadraticCurveTo(x, y, x + safeRadius, y);
  ctx.closePath();
}

function setRenderedShot(canvas) {
  const button = canvas.closest("[data-alt]");
  if (!button) return;

  try {
    button.dataset.shot = canvas.toDataURL("image/png");
  } catch {
    button.dataset.shot = "";
  }

  canvas.dataset.rendered = "true";
  canvas.dataset.rendering = "";

  if (button.dataset.pendingOpen === "true" && button.dataset.shot) {
    button.dataset.pendingOpen = "";
    openLightboxWithTrigger(button);
  }
}

function markRenderFailed(canvas) {
  canvas.dataset.rendering = "";
  canvas.dataset.renderFailed = "true";
}

function renderDimensionsForCanvas(canvas, frameWidth, frameHeight, minimumWidth) {
  const ratio = frameWidth / frameHeight;
  const cssWidth = Math.max(
    minimumWidth,
    Math.ceil(canvas.getBoundingClientRect().width || canvas.parentElement?.getBoundingClientRect().width || minimumWidth),
  );
  const renderWidth = Math.max(
    minimumWidth,
    Math.min(frameWidth, Math.ceil(cssWidth * Math.max(1, Math.min(window.devicePixelRatio || 1, 2)))),
  );
  const renderHeight = Math.max(1, Math.round(renderWidth / ratio));
  return { width: renderWidth, height: renderHeight };
}

function openLightboxWithTrigger(trigger) {
  const dialog = document.getElementById("lightbox");
  const image = document.getElementById("lightbox-image");
  image.src = trigger.dataset.shot;
  image.alt = trigger.dataset.alt || "Audit screenshot";
  if (typeof dialog.showModal === "function") {
    dialog.showModal();
  }
}

function renderSpotlightCanvas(canvas) {
  if (!canvas || canvas.dataset.rendered === "true" || canvas.dataset.rendering === "true") return;

  const raw = canvas.dataset.spotlight;
  if (!raw) return;

  let spotlight;
  try {
    spotlight = JSON.parse(raw);
  } catch {
    return;
  }

  canvas.dataset.rendering = "true";

  const image = new Image();
  image.loading = "lazy";
  image.decoding = "async";
  image.onload = () => {
    const maxCropX = Math.max(0, image.naturalWidth - 1);
    const maxCropY = Math.max(0, image.naturalHeight - 1);
    const cropX = Math.min(Math.max(0, spotlight.crop?.x || 0), maxCropX);
    const cropY = Math.min(Math.max(0, spotlight.crop?.y || 0), maxCropY);
    const cropWidth = Math.max(1, Math.min(spotlight.crop?.width || image.naturalWidth, image.naturalWidth - cropX));
    const cropHeight = Math.max(1, Math.min(spotlight.crop?.height || image.naturalHeight, image.naturalHeight - cropY));
    if (cropWidth <= 0 || cropHeight <= 0) return;

    const frameWidth = Math.max(1, Number(spotlight.frame?.width) || 1920);
    const frameHeight = Math.max(1, Number(spotlight.frame?.height) || 1080);
    const renderSize = renderDimensionsForCanvas(canvas, frameWidth, frameHeight, 640);
    const scaleX = renderSize.width / cropWidth;
    const scaleY = renderSize.height / cropHeight;

    canvas.width = renderSize.width;
    canvas.height = renderSize.height;
    canvas.style.aspectRatio = `${frameWidth} / ${frameHeight}`;

    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, renderSize.width, renderSize.height);
    ctx.drawImage(image, cropX, cropY, cropWidth, cropHeight, 0, 0, renderSize.width, renderSize.height);

    const highlightX = ((spotlight.highlight?.x || 0) - cropX) * scaleX;
    const highlightY = ((spotlight.highlight?.y || 0) - cropY) * scaleY;
    const highlightW = (spotlight.highlight?.width || 0) * scaleX;
    const highlightH = (spotlight.highlight?.height || 0) * scaleY;

    if (highlightW > 0 && highlightH > 0) {
      const inset = 1.5;
      const boundedX = Math.max(inset, highlightX);
      const boundedY = Math.max(inset, highlightY);
      const boundedW = Math.max(8, Math.min(renderSize.width - boundedX - inset, highlightW));
      const boundedH = Math.max(8, Math.min(renderSize.height - boundedY - inset, highlightH));

      ctx.save();
      ctx.strokeStyle = "#ef2b2d";
      ctx.lineWidth = Math.max(2.5, renderSize.width / 640);
      drawRoundedRect(
        ctx,
        boundedX,
        boundedY,
        boundedW,
        boundedH,
        Math.max(12, renderSize.width / 72),
      );
      ctx.stroke();
      ctx.restore();
    }

    setRenderedShot(canvas);
  };
  image.onerror = () => {
    markRenderFailed(canvas);
  };
  image.src = spotlight.image;
}

function renderPreviewCanvas(canvas) {
  if (!canvas || canvas.dataset.rendered === "true" || canvas.dataset.rendering === "true") return;

  const raw = canvas.dataset.preview;
  if (!raw) return;

  let preview;
  try {
    preview = JSON.parse(raw);
  } catch {
    return;
  }

  canvas.dataset.rendering = "true";

  const image = new Image();
  image.loading = "eager";
  image.decoding = "async";
  image.onload = () => {
    const frameWidth = Math.max(1, Number(preview.frame?.width) || 1920);
    const frameHeight = Math.max(1, Number(preview.frame?.height) || 1080);
    const targetRatio = frameWidth / frameHeight;

    let cropWidth = image.naturalWidth;
    let cropHeight = Math.round(cropWidth / targetRatio);

    if (cropHeight > image.naturalHeight) {
      cropHeight = image.naturalHeight;
      cropWidth = Math.max(1, Math.round(cropHeight * targetRatio));
    }

    const cropX = Math.max(0, Math.round((image.naturalWidth - cropWidth) / 2));
    const cropY = 0;
    const renderSize = renderDimensionsForCanvas(canvas, frameWidth, frameHeight, 720);

    canvas.width = renderSize.width;
    canvas.height = renderSize.height;
    canvas.style.aspectRatio = `${frameWidth} / ${frameHeight}`;

    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, renderSize.width, renderSize.height);
    ctx.drawImage(image, cropX, cropY, cropWidth, cropHeight, 0, 0, renderSize.width, renderSize.height);
    setRenderedShot(canvas);
  };
  image.onerror = () => {
    markRenderFailed(canvas);
  };
  image.src = preview.image;
}

function renderDeferredCanvases() {
  const canvases = [...document.querySelectorAll(".spotlight-canvas, .preview-canvas")];
  if (!canvases.length) return;

  const renderCanvas = (canvas) => {
    if (canvas.classList.contains("spotlight-canvas")) {
      renderSpotlightCanvas(canvas);
    } else if (canvas.classList.contains("preview-canvas")) {
      renderPreviewCanvas(canvas);
    }
  };

  if (!("IntersectionObserver" in window)) {
    canvases.forEach(renderCanvas);
    return;
  }

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        renderCanvas(entry.target);
        observer.unobserve(entry.target);
      });
    },
    { rootMargin: "240px 0px" },
  );

  canvases.forEach((canvas) => observer.observe(canvas));
}

function setupRevealAnimations() {
  const targets = document.querySelectorAll(
    ".section-block, .stat-card, .sheet-card, .finding-card, .page-card, .method-card, .nav-node, .artifact-card",
  );

  if (!targets.length) return;

  document.documentElement.classList.add("enhanced-reveal");

  if (!("IntersectionObserver" in window)) {
    targets.forEach((target) => target.classList.add("is-visible"));
    return;
  }

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      });
    },
    { threshold: 0.12, rootMargin: "0px 0px -8% 0px" },
  );

  targets.forEach((target) => observer.observe(target));
}

function findingCard(item, compact = false) {
  const status = String(item.status || "N/A").toUpperCase();
  const tone = status === "FALSE" ? item.severity : status === "TRUE" ? "positive" : "neutral";
  return `
    <article class="finding-card ${tone}">
      <div class="finding-head">
        <div>
          <p class="finding-sheet">${escapeHtml(item.sheet)}</p>
          <h3>${escapeHtml(item.criterion)}</h3>
        </div>
        <div class="finding-badges">
          <span class="status-pill ${status.toLowerCase()}">${statusLabel(status)}</span>
          <span class="confidence-pill ${confidenceTone(item.confidencePercent)}">${escapeHtml(item.confidencePercent)}% confidence</span>
        </div>
      </div>
      ${screenshotFigure(item, compact)}
      <p class="finding-rationale">${escapeHtml(item.rationale)}</p>
      <div class="meta-row">
        ${renderMetaPills([
          item.pageName ? `Page: ${item.pageName}` : "",
          item.pageUrl ? "Source page linked" : "",
          item.decisionBasis ? `Basis: ${item.decisionBasis}` : "",
        ])}
      </div>
      ${renderEvidence(item.evidence)}
      ${renderSourceLinks(item.sourcePages)}
    </article>
  `;
}

function deepDiveCard(item) {
  const status = String(item.status || "N/A").toUpperCase();
  const tone = status === "FALSE" ? item.severity : status === "TRUE" ? "positive" : "neutral";
  return `
    <article class="finding-card deep-dive-card ${tone}">
      <div class="finding-head">
        <div>
          <p class="finding-sheet">${escapeHtml(item.sheet)}</p>
          <h3>${escapeHtml(item.criterion)}</h3>
        </div>
        <div class="finding-badges">
          <span class="status-pill ${status.toLowerCase()}">${statusLabel(status)}</span>
          <span class="confidence-pill ${confidenceTone(item.confidencePercent)}">${escapeHtml(item.confidencePercent)}% confidence</span>
        </div>
      </div>
      ${screenshotFigure(item, true)}
      <p class="finding-rationale">${escapeHtml(item.rationale)}</p>
      <div class="meta-row">
        ${renderMetaPills([
          item.pageName ? `Page: ${item.pageName}` : "",
          item.decisionBasis ? `Basis: ${item.decisionBasis}` : "",
        ])}
      </div>
      ${renderEvidence((item.evidence || []).slice(0, 3))}
    </article>
  `;
}

function renderHero() {
  document.title = reportData.site.title;
  document.getElementById("hero-title").textContent = reportData.site.title;
  document.getElementById("hero-summary").textContent = reportData.executiveSummary;
  document.getElementById("overall-score").textContent = `${reportData.summary.overallScore}`;

  document.getElementById("hero-meta").innerHTML = renderMetaPills([
    reportData.site.domain,
    reportData.site.language ? `Language: ${reportData.site.language}` : "",
    `Generated ${reportData.site.generatedAt}`,
    `${reportData.summary.pagesAudited} pages audited`,
  ]);

  const workbookButton = document.getElementById("download-workbook");
  if (reportData.artifacts.workbook) {
    workbookButton.href = reportData.artifacts.workbook;
  } else {
    workbookButton.classList.add("is-disabled");
    workbookButton.removeAttribute("href");
    workbookButton.textContent = "Workbook unavailable";
  }

  const heroPage = reportData.pages.find((page) => page.screenshot) || reportData.pages[0];
  const heroShot = document.getElementById("hero-shot");
  if (heroPage && heroPage.screenshot) {
    const preview = JSON.stringify({
      image: heroPage.screenshot,
      frame: { width: 1920, height: 1080 },
    });
    heroShot.innerHTML = `
      <div class="hero-shot-card">
        <button class="shot-frame preview-frame" data-alt="${escapeHtml(heroPage.name)}">
          <canvas class="preview-canvas" data-preview='${escapeHtml(preview)}' aria-label="${escapeHtml(heroPage.name)}"></canvas>
        </button>
        <div class="hero-shot-copy">
          <p class="hero-shot-label">Featured screen</p>
          <h3>${escapeHtml(heroPage.name)}</h3>
          <p>${escapeHtml(heroPage.title || heroPage.finalUrl || heroPage.url)}</p>
        </div>
      </div>
    `;
  } else {
    heroShot.innerHTML = `<div class="hero-shot-empty">No page screenshot was available for the hero preview.</div>`;
  }
}

function renderStats() {
  const items = [
    ["Checks passed", reportData.summary.passed],
    ["Issues flagged", reportData.summary.failed],
    ["Not applicable", reportData.summary.notApplicable],
    ["Interactions tested", reportData.summary.interactionsTested],
    ["Screenshots created", reportData.summary.screenshotsCreated],
    ["Navigation items", reportData.summary.navigationItems],
  ];

  document.getElementById("stats-grid").innerHTML = items
    .map(
      ([label, value]) => `
        <article class="stat-card">
          <p class="stat-label">${escapeHtml(label)}</p>
          <p class="stat-value">${escapeHtml(value)}</p>
        </article>
      `,
    )
    .join("");
}

function renderSheetsOverview() {
  document.getElementById("sheet-grid").innerHTML = reportData.sheets
    .map(
      (sheet) => `
        <article class="sheet-card">
          <div class="sheet-top">
            <div>
              <p class="sheet-name">${escapeHtml(sheet.name)}</p>
              <p class="sheet-subline">${escapeHtml(sheet.failed)} issues, ${escapeHtml(sheet.passed)} strengths</p>
            </div>
            <p class="sheet-score">${escapeHtml(sheet.score)}<span>/100</span></p>
          </div>
          <div class="sheet-bar"><span style="width:${sheet.score}%"></span></div>
          <div class="sheet-meta">
            <span>Pass ${escapeHtml(sheet.passed)}</span>
            <span>Issue ${escapeHtml(sheet.failed)}</span>
            <span>N/A ${escapeHtml(sheet.na)}</span>
          </div>
        </article>
      `,
    )
    .join("");
}

function renderFindings() {
  const findings = reportData.topFindings || [];
  document.getElementById("priority-findings").innerHTML = findings.length
    ? findings.map((item) => findingCard(item)).join("")
    : `<p class="empty-state">No FALSE findings were captured in this run.</p>`;
}

function renderStrengths() {
  const strengths = reportData.topStrengths || [];
  document.getElementById("top-strengths").innerHTML = strengths.length
    ? strengths.map((item) => findingCard(item, true)).join("")
    : `<p class="empty-state">No high-confidence strengths were available.</p>`;
}

function renderDeepDive() {
  const container = document.getElementById("sheet-sections");
  if (!container) return;

  container.innerHTML = (reportData.sheets || [])
    .map((sheet) => {
      const topIssue = (sheet.findings || [])[0];
      const topStrength = (sheet.strengths || [])[0];
      const issueMarkup = topIssue
        ? deepDiveCard(topIssue)
        : `
            <article class="finding-card deep-dive-card neutral">
              <p class="finding-sheet">Summary</p>
              <h3>No issue was flagged in this dimension.</h3>
              <p class="finding-rationale">${escapeHtml(`${sheet.failed || 0} issues were flagged across this dimension.`)}</p>
            </article>
          `;
      const strengthMarkup = topStrength
        ? deepDiveCard(topStrength)
        : `
            <article class="finding-card deep-dive-card neutral">
              <p class="finding-sheet">Summary</p>
              <h3>No high-confidence strength was captured in this dimension.</h3>
              <p class="finding-rationale">${escapeHtml(`${sheet.passed || 0} checks passed in this dimension.`)}</p>
            </article>
          `;

      return `
        <section class="sheet-section">
          <div class="sheet-section-head">
            <div>
              <p class="eyebrow">Dimension</p>
              <h3>${escapeHtml(sheet.name || "Untitled dimension")}</h3>
            </div>
            <div class="sheet-section-score">${escapeHtml(sheet.score ?? 0)}/100</div>
          </div>
          <div class="sheet-columns">
            <div>
              <h4 class="mini-title">Issues</h4>
              <div class="mini-grid">${issueMarkup}</div>
            </div>
            <div>
              <h4 class="mini-title">Strengths</h4>
              <div class="mini-grid">${strengthMarkup}</div>
            </div>
          </div>
        </section>
      `;
    })
    .join("");
}

function renderPages() {
  document.getElementById("page-gallery").innerHTML = reportData.pages
    .map((page) => `
      <article class="page-card">
        ${screenshotFigure(page)}
        <div class="page-copy">
          <div class="page-head">
            <h3>${escapeHtml(page.name)}</h3>
            <span class="mini-pill">${escapeHtml(page.designHealth || 0)}/100 design health</span>
          </div>
          <p class="page-title">${escapeHtml(page.title || page.finalUrl || page.url)}</p>
          <div class="meta-row">
            ${renderMetaPills([
              page.language ? `Language: ${page.language}` : "",
              page.forms ? `${page.forms} form(s)` : "No forms detected",
              page.images ? `${page.images} image(s)` : "",
              page.links ? `${page.links} link(s)` : "",
            ])}
          </div>
          <p class="page-url"><a href="${escapeHtml(page.finalUrl || page.url)}" target="_blank" rel="noreferrer">${escapeHtml(page.finalUrl || page.url)}</a></p>
        </div>
      </article>
    `)
    .join("");
}

function renderNavigationTreeItem(item) {
  const childMarkup = (item.children || []).length
    ? `<div class="nav-children">${item.children.map((child) => renderNavigationTreeItem(child)).join("")}</div>`
    : "";

  const label = item.url
    ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.name || item.url)}</a>`
    : `<span>${escapeHtml(item.name || "Untitled")}</span>`;

  return `
    <article class="nav-node">
      <div class="nav-node-head">
        <span class="mini-pill">${escapeHtml(item.type || "link")}</span>
        ${label}
      </div>
      ${childMarkup}
    </article>
  `;
}

function renderNavigation() {
  document.getElementById("navigation-tree").innerHTML = (reportData.navigation || []).length
    ? reportData.navigation.map((item) => renderNavigationTreeItem(item)).join("")
    : `<p class="empty-state">The crawler did not return a navigation tree for this run.</p>`;
}

function renderProcess() {
  const visual = reportData.visualSummary || {};
  document.getElementById("visual-summary").innerHTML = `
    <div class="visual-card">
      <p class="visual-label">Visual system snapshot</p>
      <h3>${escapeHtml(visual.designHealth || 0)}/100 average design-system health</h3>
      <p>Component consistency averaged ${escapeHtml(visual.componentConsistency || 0)}/100 across the audited pages.</p>
      <div class="visual-swatches">
        ${(visual.backgrounds || []).slice(0, 5).map((color) => `<span class="swatch"><span class="swatch-chip" style="background:${escapeHtml(color)}"></span>${escapeHtml(color)}</span>`).join("")}
      </div>
      <div class="meta-row">
        ${renderMetaPills((visual.fontFamilies || []).map((font) => `Font: ${font}`))}
      </div>
    </div>
  `;
}

function setupLightbox() {
  const dialog = document.getElementById("lightbox");
  const image = document.getElementById("lightbox-image");
  const closeButton = document.getElementById("lightbox-close");

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest(".shot-frame");
    if (!trigger) return;

    if (trigger.dataset.shot) {
      openLightboxWithTrigger(trigger);
      return;
    }

    const canvas = trigger.querySelector(".spotlight-canvas, .preview-canvas");
    if (!canvas || LIGHTBOX_RENDER_QUEUE.has(canvas)) return;

    LIGHTBOX_RENDER_QUEUE.add(canvas);
    trigger.dataset.pendingOpen = "true";
    if (canvas.classList.contains("spotlight-canvas")) {
      renderSpotlightCanvas(canvas);
    } else {
      renderPreviewCanvas(canvas);
    }
  });

  closeButton.addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      dialog.close();
    }
  });
}

function init() {
  safeRender("header", renderHeader);
  safeRender("hero", renderHero);
  safeRender("stats", renderStats);
  safeRender("sheets overview", renderSheetsOverview);
  safeRender("findings", renderFindings);
  safeRender("strengths", renderStrengths);
  safeRender("deep dive", renderDeepDive);
  safeRender("pages", renderPages);
  safeRender("navigation", renderNavigation);
  safeRender("process", renderProcess);
  safeRender("deferred canvases", renderDeferredCanvases);
  safeRender("reveal animations", setupRevealAnimations);
  safeRender("lightbox", setupLightbox);
}

init();
