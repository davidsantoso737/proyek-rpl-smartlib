(function () {
    "use strict";

    var forms = document.querySelectorAll("form[data-live-filter]");
    Array.prototype.forEach.call(forms, function (form) {
        var fields = form.querySelectorAll("[data-live-filter-select]");
        Array.prototype.forEach.call(fields, function (field) {
            field.addEventListener("change", function () {
                if (typeof form.requestSubmit === "function") {
                    form.requestSubmit();
                } else {
                    form.submit();
                }
            });
        });
    });
})();
