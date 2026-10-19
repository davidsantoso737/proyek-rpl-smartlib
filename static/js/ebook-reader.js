// E-book reader built on the vendored PDF.js build (static/vendor/pdfjs).
//
// Why a custom viewer instead of the browser's built-in PDF viewer: the
// native viewer ships its own toolbar with download/print (and its own
// overflow menu), and none of that can be removed reliably from the page.
// Because the document is served from a protected circulation endpoint and
// must not be handed out as a savable file, this reader renders the pages
// itself and exposes only reading controls: page navigation, zoom, and
// fit-width. There is deliberately no download, print, or share control.
//
// Responsibilities kept apart: this module never decides who may read a
// document. Authorisation stays server-side in circulation.views; the file
// URL handed in here already passed that check.

const MIN_SCALE = 0.25;
const MAX_SCALE = 5;
const ZOOM_STEP = 1.25;
const STAGE_PADDING = 24;

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}

export function initEbookReader(pdfjsLib, assets) {
  const root = document.getElementById("ebook-reader");
  if (!root) {
    return;
  }

  pdfjsLib.GlobalWorkerOptions.workerSrc = assets.workerSrc;

  const stage = root.querySelector("[data-reader-stage]");
  const pageWrap = root.querySelector("[data-reader-page]");
  const canvas = root.querySelector("[data-reader-canvas]");
  const textLayerContainer = root.querySelector("[data-reader-textlayer]");
  const statusBox = root.querySelector("[data-reader-status]");
  const errorBox = root.querySelector("[data-reader-error]");
  const pageInput = root.querySelector("[data-reader-page-input]");
  const pageTotal = root.querySelector("[data-reader-page-total]");
  const prevButton = root.querySelector("[data-reader-prev]");
  const nextButton = root.querySelector("[data-reader-next]");
  const zoomOutButton = root.querySelector("[data-reader-zoom-out]");
  const zoomInButton = root.querySelector("[data-reader-zoom-in]");
  const fitButton = root.querySelector("[data-reader-fit]");
  const zoomLabel = root.querySelector("[data-reader-zoom-label]");

  const pdfUrl = root.dataset.pdfUrl;
  if (!stage || !pageWrap || !canvas || !pdfUrl) {
    return;
  }

  let pdfDocument = null;
  let currentPage = 1;
  let pageCount = 0;
  let baseScale = 1;
  let zoom = 1;
  let renderTask = null;
  let textLayer = null;
  let resizeTimer = null;
  let loadingTask = null;
  let renderToken = 0;

  function showError(message) {
    if (statusBox) {
      statusBox.hidden = true;
    }
    if (errorBox) {
      errorBox.hidden = false;
      if (message) {
        const detail = errorBox.querySelector("[data-reader-error-detail]");
        if (detail) {
          detail.textContent = message;
        }
      }
    }
  }

  function setStatus(message, visible) {
    if (!statusBox) {
      return;
    }
    statusBox.hidden = !visible;
    if (message) {
      statusBox.textContent = message;
    }
  }

  function fitScaleFor(viewport) {
    const available = Math.max(stage.clientWidth - STAGE_PADDING, 240);
    return available / viewport.width;
  }

  function effectiveScale() {
    return baseScale * zoom;
  }

  function updateControls() {
    if (pageInput) {
      pageInput.value = String(currentPage);
      pageInput.max = String(pageCount);
    }
    if (pageTotal) {
      pageTotal.textContent = String(pageCount);
    }
    if (prevButton) {
      prevButton.disabled = currentPage <= 1;
    }
    if (nextButton) {
      nextButton.disabled = currentPage >= pageCount;
    }
    if (zoomLabel) {
      zoomLabel.textContent = Math.round(zoom * 100) + "%";
    }
    if (zoomOutButton) {
      zoomOutButton.disabled = effectiveScale() <= MIN_SCALE + 0.001;
    }
    if (zoomInButton) {
      zoomInButton.disabled = effectiveScale() >= MAX_SCALE - 0.001;
    }
    if (fitButton) {
      fitButton.setAttribute("aria-pressed", zoom === 1 ? "true" : "false");
    }
  }

  async function renderPage(number) {
    if (!pdfDocument) {
      return;
    }
    const target = clamp(number, 1, pageCount);
    const pageChanged = target !== currentPage;
    currentPage = target;
    const token = ++renderToken;

    if (pageChanged) {
      stage.scrollTop = 0;
      stage.scrollLeft = 0;
    }

    if (renderTask) {
      renderTask.cancel();
      renderTask = null;
    }
    if (textLayer) {
      textLayer.cancel();
      textLayer = null;
    }

    setStatus("Menampilkan halaman " + target + "\u2026", true);
    let layer = null;

    try {
      const page = await pdfDocument.getPage(target);
      if (token !== renderToken) {
        return;
      }

      const unscaled = page.getViewport({ scale: 1 });
      baseScale = fitScaleFor(unscaled);
      const viewport = page.getViewport({ scale: effectiveScale() });

      const outputScale = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * outputScale);
      canvas.height = Math.floor(viewport.height * outputScale);
      canvas.style.width = Math.floor(viewport.width) + "px";
      canvas.style.height = Math.floor(viewport.height) + "px";
      pageWrap.style.width = Math.floor(viewport.width) + "px";
      pageWrap.style.height = Math.floor(viewport.height) + "px";
      pageWrap.style.setProperty(
        "--total-scale-factor",
        String(viewport.scale)
      );

      const context = canvas.getContext("2d", { alpha: false });
      const task = page.render({
        canvasContext: context,
        viewport,
        transform:
          outputScale !== 1
            ? [outputScale, 0, 0, outputScale, 0, 0]
            : null,
      });
      renderTask = task;

      if (textLayerContainer) {
        textLayerContainer.replaceChildren();
        textLayerContainer.style.setProperty(
          "--total-scale-factor",
          String(viewport.scale)
        );
        layer = new pdfjsLib.TextLayer({
          textContentSource: page.streamTextContent(),
          container: textLayerContainer,
          viewport,
        });
        textLayer = layer;
      }

      await task.promise;
      if (token !== renderToken) {
        return;
      }
      if (layer) {
        await layer.render();
      }
      if (token !== renderToken) {
        return;
      }
      setStatus("", false);
    } catch (error) {
      if (token !== renderToken) {
        return;
      }
      if (error && error.name === "RenderingCancelledException") {
        return;
      }
      setStatus("", false);
      showError("Halaman tidak dapat ditampilkan.");
      return;
    }

    updateControls();
    pageWrap.hidden = false;
  }

  function goToPage(number) {
    if (!pdfDocument) {
      return;
    }
    const target = clamp(number, 1, pageCount);
    if (target === currentPage) {
      return;
    }
    renderPage(target);
  }

  function applyZoom(nextZoom) {
    if (!pdfDocument) {
      return;
    }
    const maxRatio = MAX_SCALE / baseScale;
    const minRatio = MIN_SCALE / baseScale;
    const clamped = clamp(nextZoom, Math.max(minRatio, 0.1), maxRatio);
    if (Math.abs(clamped - zoom) < 0.001) {
      return;
    }
    zoom = clamped;
    renderPage(currentPage);
  }

  function zoomIn() {
    applyZoom(zoom * ZOOM_STEP);
  }

  function zoomOut() {
    applyZoom(zoom / ZOOM_STEP);
  }

  function fitWidth() {
    if (!pdfDocument) {
      return;
    }
    zoom = 1;
    renderPage(currentPage);
  }

  function bindControls() {
    if (prevButton) {
      prevButton.addEventListener("click", function () {
        goToPage(currentPage - 1);
      });
    }
    if (nextButton) {
      nextButton.addEventListener("click", function () {
        goToPage(currentPage + 1);
      });
    }
    if (zoomInButton) {
      zoomInButton.addEventListener("click", zoomIn);
    }
    if (zoomOutButton) {
      zoomOutButton.addEventListener("click", zoomOut);
    }
    if (fitButton) {
      fitButton.addEventListener("click", fitWidth);
    }
    if (pageInput) {
      const commit = function () {
        const parsed = parseInt(pageInput.value, 10);
        if (Number.isNaN(parsed)) {
          updateControls();
          return;
        }
        if (parsed === currentPage) {
          updateControls();
          return;
        }
        renderPage(parsed);
      };
      pageInput.addEventListener("change", commit);
      pageInput.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
          event.preventDefault();
          commit();
        }
      });
    }

    stage.addEventListener("keydown", function (event) {
      if (event.target && event.target.matches("input")) {
        return;
      }
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        // Once the page is wider than the stage the arrow keys scroll it, so
        // they only turn pages while there is nothing left to scroll sideways.
        if (stage.scrollWidth > stage.clientWidth + 1) {
          return;
        }
        event.preventDefault();
        goToPage(event.key === "ArrowLeft" ? currentPage - 1 : currentPage + 1);
        return;
      }
      // PageUp/PageDown and ArrowUp/ArrowDown stay with the browser so the
      // stage scrolls like any other document region.
      switch (event.key) {
        case "+":
        case "=":
          event.preventDefault();
          zoomIn();
          break;
        case "-":
        case "_":
          event.preventDefault();
          zoomOut();
          break;
        case "0":
          event.preventDefault();
          fitWidth();
          break;
        default:
          break;
      }
    });

    window.addEventListener("resize", function () {
      if (resizeTimer) {
        window.clearTimeout(resizeTimer);
      }
      resizeTimer = window.setTimeout(function () {
        if (pdfDocument && zoom === 1) {
          renderPage(currentPage);
        }
      }, 200);
    });
  }

  async function start() {
    bindControls();
    setStatus("Memuat dokumen\u2026", true);
    try {
      loadingTask = pdfjsLib.getDocument({
        url: pdfUrl,
        cMapUrl: assets.cMapUrl,
        cMapPacked: true,
        standardFontDataUrl: assets.standardFontsUrl,
        // Scanned books store their content pages as JBIG2 (and sometimes JPX)
        // images. The decoder wasm and the CMYK ICC profile are only loaded
        // when these paths are given, so without them those pages stay blank.
        wasmUrl: assets.wasmUrl,
        iccUrl: assets.iccUrl,
        isEvalSupported: false,
      });
      pdfDocument = await loadingTask.promise;
      pageCount = pdfDocument.numPages;
      pageWrap.hidden = true;
      updateControls();
      await renderPage(1);
    } catch (error) {
      setStatus("", false);
      showError(
        error && error.message
          ? "Dokumen tidak dapat dimuat. " + error.message
          : "Dokumen tidak dapat dimuat."
      );
    }
  }

  window.addEventListener("beforeunload", function () {
    if (loadingTask) {
      loadingTask.destroy();
    }
  });

  start();
}
