/* =========================================================================
   PREPARAR A FOTO ANTES DE ENVIAR — Cantinho Família
   A Vercel recusa qualquer envio acima de ~4,5 MB (a página nem chega no
   Django) e foto de celular passa disso fácil. Aqui, no navegador, a foto é
   reduzida para até 2000 px e regravada leve (WebP, ou JPEG onde o
   navegador não grava WebP) ANTES de subir. Vale para o admin (todo campo
   de arquivo) e para a tela "Restaurar fotos".

   window.cfPrepararFoto(arquivo, limite) -> Promise<{ arquivo, mudou }>
   Rejeita com Error(mensagem para o usuário) quando não há como caber.
   ========================================================================= */
(function () {
    "use strict";
    if (window.cfPrepararFoto) return;

    var LIMITE_PADRAO = 3.5 * 1024 * 1024;   // folga sob os ~4,5 MB da Vercel
    var REDUZIR_ACIMA = 1.5 * 1024 * 1024;   // abaixo disso a foto vai como está
    var LADO_MAXIMO = 2000;
    var DIRETOS = /^image\/(jpeg|png|webp|gif)$/i;
    var EH_IMAGEM = /\.(jpe?g|jfif|png|webp|gif|bmp|tiff?|avif|heic|heif)$/i;

    function tamanho(bytes) {
        return bytes >= 1048576 ? (bytes / 1048576).toFixed(1).replace(".", ",") + " MB"
            : Math.max(1, Math.round(bytes / 1024)) + " KB";
    }

    function ehImagem(arquivo) {
        return /^image\//i.test(arquivo.type || "") || EH_IMAGEM.test(arquivo.name || "");
    }

    function abrir(arquivo) {
        function pelaTag() {
            return new Promise(function (ok, falhou) {
                var url = URL.createObjectURL(arquivo);
                var img = new Image();
                img.onload = function () { ok({ fonte: img, largura: img.naturalWidth, altura: img.naturalHeight, soltar: function () { URL.revokeObjectURL(url); } }); };
                img.onerror = function () { URL.revokeObjectURL(url); falhou(new Error("o navegador não abriu esta imagem")); };
                img.src = url;
            });
        }
        if (typeof createImageBitmap !== "function") return pelaTag();
        return createImageBitmap(arquivo, { imageOrientation: "from-image" }).then(function (bmp) {
            return { fonte: bmp, largura: bmp.width, altura: bmp.height, soltar: function () { if (bmp.close) bmp.close(); } };
        }, pelaTag);
    }

    function gravar(tela, tipo, qualidade) {
        return new Promise(function (ok) { tela.toBlob(ok, tipo, qualidade); });
    }

    function desenhar(imagem, lado, fundoBranco) {
        var escala = Math.min(1, lado / Math.max(imagem.largura, imagem.altura));
        var tela = document.createElement("canvas");
        tela.width = Math.max(1, Math.round(imagem.largura * escala));
        tela.height = Math.max(1, Math.round(imagem.altura * escala));
        var ctx = tela.getContext("2d");
        if (fundoBranco) { ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, tela.width, tela.height); }
        ctx.drawImage(imagem.fonte, 0, 0, tela.width, tela.height);
        return tela;
    }

    async function reduzir(arquivo, limite) {
        var imagem = await abrir(arquivo);
        try {
            var tentativas = [[LADO_MAXIMO, 0.86], [LADO_MAXIMO, 0.76], [1600, 0.76], [1280, 0.72], [1000, 0.7]];
            for (var i = 0; i < tentativas.length; i++) {
                var lado = tentativas[i][0], qualidade = tentativas[i][1];
                var blob = await gravar(desenhar(imagem, lado, false), "image/webp", qualidade);
                if (!blob || blob.type !== "image/webp") {   // Safari antigo não grava WebP
                    blob = await gravar(desenhar(imagem, lado, true), "image/jpeg", qualidade);
                }
                if (blob && blob.size <= limite) {
                    var extensao = blob.type === "image/webp" ? ".webp" : ".jpg";
                    var nome = (arquivo.name || "foto").replace(/\.[^./\\]+$/, "") + extensao;
                    return new File([blob], nome, { type: blob.type, lastModified: Date.now() });
                }
            }
        } finally {
            imagem.soltar();
        }
        throw new Error("não coube no limite de envio");
    }

    window.cfPrepararFoto = async function (arquivo, limite) {
        limite = limite || LIMITE_PADRAO;
        if (!ehImagem(arquivo)) {
            if (arquivo.size > limite) throw new Error("Arquivo maior que " + tamanho(limite) + " por envio.");
            return { arquivo: arquivo, mudou: false };
        }
        var direto = DIRETOS.test(arquivo.type || "");
        var animada = /gif$/i.test(arquivo.type || "");
        if (direto && (arquivo.size <= REDUZIR_ACIMA || animada) && arquivo.size <= limite) {
            return { arquivo: arquivo, mudou: false };
        }
        try {
            var menor = await reduzir(arquivo, limite);
            // Só troca quando ajuda: ficou menor, ou o original não é um formato da web.
            if (!direto || menor.size < arquivo.size) return { arquivo: menor, mudou: true };
        } catch (erro) {
            if (arquivo.size > limite) {
                throw new Error(
                    "Esta foto tem " + tamanho(arquivo.size) + " e o navegador não conseguiu reduzi-la" +
                    " (o envio aceita até " + tamanho(limite) + "). Salve-a como JPG ou PNG e tente de novo."
                );
            }
        }
        if (arquivo.size > limite) throw new Error("Foto maior que " + tamanho(limite) + " por envio.");
        return { arquivo: arquivo, mudou: false };
    };
    window.cfPrepararFoto.tamanho = tamanho;

    /* ------------------------------------------------------------ admin
       Todo campo de arquivo do admin: a foto é preparada assim que é
       escolhida, e o formulário só sai quando ela está pronta. */
    function ligarNoAdmin() {
        if (!document.body || !/\b(change-form|change-list)\b/.test(document.body.className)) return;
        var pendentes = 0;

        function aviso(entrada, texto, cor) {
            var el = entrada.parentNode.querySelector(".cf-foto-aviso");
            if (!el) {
                el = document.createElement("div");
                el.className = "cf-foto-aviso";
                el.style.cssText = "margin-top:6px;font-size:12px;font-weight:700;";
                entrada.parentNode.appendChild(el);
            }
            el.textContent = texto;
            el.style.color = cor;
        }

        function botoes(form, travar) {
            form.querySelectorAll("input[type=submit], button[type=submit]").forEach(function (b) { b.disabled = travar; });
        }

        document.addEventListener("change", async function (evento) {
            var entrada = evento.target;
            if (!entrada || entrada.type !== "file" || !entrada.files || !entrada.files.length || entrada.multiple) return;
            var original = entrada.files[0];
            var form = entrada.form;
            pendentes += 1;
            if (form) botoes(form, true);
            aviso(entrada, "Preparando a foto…", "#64748b");
            try {
                var pronto = await window.cfPrepararFoto(original);
                if (pronto.mudou && typeof DataTransfer === "function") {
                    var troca = new DataTransfer();
                    troca.items.add(pronto.arquivo);
                    entrada.files = troca.files;
                    aviso(entrada, "Foto pronta para enviar: " + tamanho(original.size) + " → " + tamanho(pronto.arquivo.size) + ".", "#15803d");
                } else {
                    aviso(entrada, "Foto pronta para enviar (" + tamanho(original.size) + ").", "#15803d");
                }
            } catch (erro) {
                entrada.value = "";
                aviso(entrada, erro.message, "#b91c1c");
            } finally {
                pendentes -= 1;
                if (form && !pendentes) botoes(form, false);
            }
        });

        document.addEventListener("submit", function (evento) {
            if (pendentes) { evento.preventDefault(); return; }
            var grande = Array.prototype.find.call(
                evento.target.querySelectorAll("input[type=file]"),
                function (e) { return e.files && e.files[0] && e.files[0].size > 4 * 1024 * 1024; }
            );
            if (grande) {
                evento.preventDefault();
                aviso(grande, "Esta foto é grande demais para o envio (máximo 4 MB). Escolha outra ou salve-a como JPG.", "#b91c1c");
                grande.scrollIntoView({ block: "center" });
            }
        }, true);
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ligarNoAdmin);
    else ligarNoAdmin();
})();
