const reportData = JSON.parse(document.getElementById("report-data").textContent);

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

  return `
    <button class="shot-frame ${compact ? "compact" : ""}" data-shot="${escapeHtml(shot)}" data-alt="${escapeHtml(item.criterion || item.name || "Audit screenshot")}">
      <img src="${escapeHtml(shot)}" alt="${escapeHtml(item.criterion || item.name || "Audit screenshot")}" loading="lazy">
    </button>
  `;
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
}

function renderSpotlightCanvas(canvas) {
  const raw = canvas.dataset.spotlight;
  if (!raw) return;

  let spotlight;
  try {
    spotlight = JSON.parse(raw);
  } catch {
    return;
  }

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
    const scaleX = frameWidth / cropWidth;
    const scaleY = frameHeight / cropHeight;

    canvas.width = frameWidth;
    canvas.height = frameHeight;
    canvas.style.aspectRatio = `${frameWidth} / ${frameHeight}`;

    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, frameWidth, frameHeight);
    ctx.drawImage(image, cropX, cropY, cropWidth, cropHeight, 0, 0, frameWidth, frameHeight);

    const highlightX = ((spotlight.highlight?.x || 0) - cropX) * scaleX;
    const highlightY = ((spotlight.highlight?.y || 0) - cropY) * scaleY;
    const highlightW = (spotlight.highlight?.width || 0) * scaleX;
    const highlightH = (spotlight.highlight?.height || 0) * scaleY;

    if (highlightW > 0 && highlightH > 0) {
      const inset = 1.5;
      const boundedX = Math.max(inset, highlightX);
      const boundedY = Math.max(inset, highlightY);
      const boundedW = Math.max(8, Math.min(frameWidth - boundedX - inset, highlightW));
      const boundedH = Math.max(8, Math.min(frameHeight - boundedY - inset, highlightH));

      ctx.save();
      ctx.strokeStyle = "#ef2b2d";
      ctx.lineWidth = Math.max(3, frameWidth / 640);
      drawRoundedRect(
        ctx,
        boundedX,
        boundedY,
        boundedW,
        boundedH,
        Math.max(14, frameWidth / 72),
      );
      ctx.stroke();
      ctx.restore();
    }

    setRenderedShot(canvas);
  };
  image.src = spotlight.image;
}

function renderPreviewCanvas(canvas) {
  const raw = canvas.dataset.preview;
  if (!raw) return;

  let preview;
  try {
    preview = JSON.parse(raw);
  } catch {
    return;
  }

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

    canvas.width = frameWidth;
    canvas.height = frameHeight;
    canvas.style.aspectRatio = `${frameWidth} / ${frameHeight}`;

    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, frameWidth, frameHeight);
    ctx.drawImage(image, cropX, cropY, cropWidth, cropHeight, 0, 0, frameWidth, frameHeight);
    setRenderedShot(canvas);
  };
  image.src = preview.image;
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
  document.getElementById("sheet-sections").innerHTML = reportData.sheets
    .map((sheet) => {
      const issueCards = sheet.findings.length
        ? sheet.findings.map((item) => findingCard(item, true)).join("")
        : `<p class="empty-note">No issues were flagged in ${escapeHtml(sheet.name.toLowerCase())}.</p>`;
      const strengthCards = sheet.strengths.length
        ? sheet.strengths.slice(0, 3).map((item) => findingCard(item, true)).join("")
        : `<p class="empty-note">No high-confidence strengths were captured.</p>`;

      return `
        <section class="sheet-section">
          <div class="sheet-section-head">
            <div>
              <p class="eyebrow">Dimension</p>
              <h3>${escapeHtml(sheet.name)}</h3>
            </div>
            <div class="sheet-section-score">${escapeHtml(sheet.score)}/100</div>
          </div>
          <div class="sheet-columns">
            <div>
              <h4 class="mini-title">Issues</h4>
              <div class="mini-grid">${issueCards}</div>
            </div>
            <div>
              <h4 class="mini-title">Strengths</h4>
              <div class="mini-grid">${strengthCards}</div>
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

  document.getElementById("methodology-list").innerHTML = (reportData.methodology || [])
    .map(
      (step, index) => `
        <article class="method-card">
          <div class="method-index">${index + 1}</div>
          <div>
            <h3>${escapeHtml(step.step)}</h3>
            <p>${escapeHtml(step.description)}</p>
            <span class="mini-pill">${escapeHtml(step.outputs)}</span>
          </div>
        </article>
      `,
    )
    .join("");
}

function renderArtifacts() {
  const labels = {
    workbook: "Excel workbook",
    checksJson: "Checks JSON",
    websiteMenu: "Website menu",
    cleanedJson: "Cleaned content JSON",
    renderedJson: "Rendered UI JSON",
  };

  document.getElementById("artifact-links").innerHTML = Object.entries(labels)
    .filter(([key]) => reportData.artifacts[key])
    .map(
      ([key, label]) => `
        <a class="artifact-card" href="${escapeHtml(reportData.artifacts[key])}" target="_blank" rel="noreferrer">
          <span class="artifact-label">${escapeHtml(label)}</span>
          <span class="artifact-arrow">Open</span>
        </a>
      `,
    )
    .join("");
}

function setupLightbox() {
  const dialog = document.getElementById("lightbox");
  const image = document.getElementById("lightbox-image");
  const closeButton = document.getElementById("lightbox-close");

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-shot]");
    if (!trigger || !trigger.dataset.shot) return;
    image.src = trigger.dataset.shot;
    image.alt = trigger.dataset.alt || "Audit screenshot";
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
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
  renderHero();
  renderStats();
  renderSheetsOverview();
  renderFindings();
  renderStrengths();
  renderDeepDive();
  renderPages();
  renderNavigation();
  renderProcess();
  renderArtifacts();
  document.querySelectorAll(".spotlight-canvas").forEach(renderSpotlightCanvas);
  document.querySelectorAll(".preview-canvas").forEach(renderPreviewCanvas);
  setupLightbox();
}

init();
