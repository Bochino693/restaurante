/* =========================================================================
   FOTO QUE NÃO CARREGA — Cantinho Família
   Em vez do ícone de imagem quebrada, um quadro limpo "Foto em atualização"
   no mesmo lugar e tamanho. Vale para o cardápio, o painel e o admin, e
   para fotos trocadas depois pelo JavaScript (modais).
   ========================================================================= */
(function () {
    "use strict";
    if (window.cfFotos) return;

    var SVG =
        '<svg xmlns="http://www.w3.org/2000/svg" width="112" height="100" viewBox="0 0 112 100">' +
        '<g fill="none" stroke="#9aa3b2" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">' +
        '<rect x="36" y="14" width="40" height="30" rx="6"/>' +
        '<circle cx="56" cy="29" r="7.5"/>' +
        '<path d="M46 14l3-5h14l3 5"/></g>' +
        '<g fill="#8b93a3" font-family="system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif" ' +
        'font-size="12.5" font-weight="700" text-anchor="middle">' +
        '<text x="56" y="66">Foto em</text><text x="56" y="82">atualização</text></g></svg>';
    var RESERVA = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(SVG);

    function marcar(img) {
        img.setAttribute("data-foto-falhou", "1");
        img.style.setProperty("object-fit", "scale-down", "important");
        img.style.setProperty("object-position", "center", "important");
        img.style.setProperty("background-color", "var(--cf-superficie-2, #f1f3f6)", "important");
    }

    function desmarcar(img) {
        img.removeAttribute("data-foto-falhou");
        ["object-fit", "object-position", "background-color"].forEach(function (p) { img.style.removeProperty(p); });
    }

    function falhou(img) {
        if (!img || img.tagName !== "IMG") return;
        var src = img.getAttribute("src");
        if (!src || src === RESERVA) return;
        img.setAttribute("data-foto-original", src);
        img.removeAttribute("srcset");
        marcar(img);
        img.src = RESERVA;
    }

    // Captura: o evento "error" de <img> não borbulha.
    document.addEventListener("error", function (e) { falhou(e.target); }, true);
    document.addEventListener("load", function (e) {
        var img = e.target;
        if (!img || img.tagName !== "IMG") return;
        if (img.getAttribute("src") === RESERVA) marcar(img);
        else if (img.hasAttribute("data-foto-falhou")) desmarcar(img);  // trocou de foto e carregou
    }, true);

    // Fotos que já falharam antes deste script rodar.
    function varrer() {
        Array.prototype.forEach.call(document.images, function (img) {
            if (!img.complete || img.naturalWidth || !img.getAttribute("src")) return;
            if (img.getAttribute("src") === RESERVA) return;
            if (img.decode) img.decode().catch(function () { falhou(img); });
            else falhou(img);
        });
    }
    varrer();
    document.addEventListener("DOMContentLoaded", varrer);

    window.cfFotos = {
        reserva: RESERVA,
        // true quando a <img> é uma foto de verdade (carregada e não o quadro).
        temFoto: function (img) {
            return !!(img && img.style.display !== "none" && img.complete && img.naturalWidth &&
                !img.hasAttribute("data-foto-falhou"));
        }
    };
})();
