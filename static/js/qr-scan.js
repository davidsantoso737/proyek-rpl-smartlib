/* SmartLib shared QR scanner behavior (F-P03).
 *
 * Extracted verbatim from the previously duplicated per-page implementations
 * (handover / return / opname). All fallbacks are preserved:
 * - Html5Qrcode availability check with manual-entry fallback message
 * - camera permission denial message
 * - secure-context (HTTPS/localhost) message
 * - generic unavailable message
 * - scanner stop/cleanup that always restores the toggle UI
 *
 * Page-specific workflow (which input receives the decoded text and which
 * form is submitted, plus the exact user-facing messages) is configured via
 * data attributes rendered by templates/partials/_scanner.html, so this file
 * contains no business logic.
 */
(function () {
    function initOne(root) {
        var toggle = root.querySelector("[data-qr-toggle]");
        var readerBox = root.querySelector("[data-qr-reader]");
        var statusEl = root.querySelector("[data-qr-status]");
        if (!toggle || !readerBox || !statusEl) {
            return;
        }
        var input = document.getElementById(root.dataset.input || "");
        var form = document.getElementById(root.dataset.form || "");
        var scanner = null;
        var scanning = false;

        function setStatus(text) {
            statusEl.textContent = text;
        }

        async function stopCamera(silent) {
            if (scanner && scanning) {
                try {
                    await scanner.stop();
                } catch (err) {
                    /* abaikan: kamera mungkin sudah mati */
                }
                scanning = false;
            }
            readerBox.classList.add("hidden");
            readerBox.innerHTML = "";
            toggle.textContent = "Nyalakan kamera";
            if (!silent) {
                setStatus(root.dataset.msgStopped || "");
            }
        }

        toggle.addEventListener("click", async function () {
            if (scanning) {
                await stopCamera(false);
                return;
            }
            if (typeof Html5Qrcode === "undefined") {
                setStatus(root.dataset.msgNolib || "");
                return;
            }
            scanner = new Html5Qrcode(readerBox.id || "qr-reader");
            readerBox.classList.remove("hidden");
            toggle.textContent = "Matikan kamera";
            setStatus(root.dataset.msgScanning || "");
            try {
                await scanner.start(
                    { facingMode: "environment" },
                    { fps: 10, qrbox: { width: 250, height: 250 } },
                    function (decodedText) {
                        if (input) {
                            input.value = decodedText;
                        }
                        setStatus((root.dataset.msgReading || "").replace("{code}", decodedText));
                        stopCamera(true).then(function () {
                            if (form) {
                                form.submit();
                            }
                        });
                    },
                    function () {
                        /* frame tanpa QR: abaikan agar bisa coba lagi */
                    }
                );
                scanning = true;
            } catch (err) {
                var name = err && err.name ? err.name : "";
                var message = err && err.message ? err.message : "";
                readerBox.classList.add("hidden");
                toggle.textContent = "Nyalakan kamera";
                if (name === "NotAllowedError" || /permission|denied/i.test(message)) {
                    setStatus(root.dataset.msgDenied || "");
                } else if (/secure|https/i.test(message)) {
                    setStatus(root.dataset.msgSecure || "");
                } else {
                    setStatus(root.dataset.msgUnavailable || "");
                }
            }
        });
    }

    /* Original pages set the "aim at camera" status text when opening the
     * scanner; preserve that by reading it from data-msg-scanning. */
    document.querySelectorAll("[data-qr-scanner]").forEach(initOne);
})();
