/* =========================================================================
   TEMAS — Sol, Lua, Eclipse e Automático (Sol de dia, Lua à noite).
   O primeiro tema é aplicado por um script curto no <head> (ver
   core/templates/_tema_head.html), antes de a página aparecer: sem piscar.
   ========================================================================= */
(function () {
    "use strict";

    const CHAVE = "cf_tema";
    const TEMAS = {
        sol: { nome: "Sol", dica: "Claro e quente", icone: '<i class="fas fa-sun"></i>', bolinha: '<i class="fas fa-sun"></i>' },
        lua: { nome: "Lua", dica: "Noite estrelada", icone: '<i class="fas fa-moon"></i>', bolinha: '<i class="fas fa-moon"></i>' },
        eclipse: { nome: "Eclipse", dica: "Preto com a coroa da marca", icone: '<span class="cf-icone-eclipse"></span>', bolinha: '<span class="cf-icone-eclipse"></span>' },
        auto: { nome: "Automático", dica: "Sol de dia, Lua à noite", icone: '<i class="fas fa-circle-half-stroke"></i>', bolinha: '<i class="fas fa-clock"></i>' }
    };

    function escolhido() {
        try {
            const t = localStorage.getItem(CHAVE);
            return TEMAS[t] ? t : "sol";
        } catch (_) {
            return "sol";
        }
    }

    function resolver(t) {
        if (t === "auto") {
            const h = new Date().getHours();
            return h >= 6 && h < 18 ? "sol" : "lua";
        }
        return TEMAS[t] ? t : "sol";
    }

    function pintar(real) {
        const html = document.documentElement;
        html.dataset.tema = real;
        html.classList.toggle("cf-escuro", real !== "sol");
        const cor = getComputedStyle(html).getPropertyValue("--cf-fundo").trim();
        let meta = document.querySelector('meta[name="theme-color"]');
        if (!meta) {
            meta = document.createElement("meta");
            meta.name = "theme-color";
            document.head.appendChild(meta);
        }
        meta.content = cor || "#f7f4ef";
        document.dispatchEvent(new CustomEvent("cf:tema", { detail: { tema: real } }));
    }

    /** Troca o tema. Com `origem` (o botão clicado), a nova cor se abre em
     *  círculo a partir dele — como o sol nascendo. */
    function aplicar(tema, origem) {
        try { localStorage.setItem(CHAVE, tema); } catch (_) { /* navegação privada */ }
        const real = resolver(tema);
        atualizarBotoes();
        if (real === document.documentElement.dataset.tema) return;

        const reduzido = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        if (document.startViewTransition && origem && !reduzido) {
            const r = origem.getBoundingClientRect();
            const x = r.left + r.width / 2;
            const y = r.top + r.height / 2;
            const raio = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
            const transicao = document.startViewTransition(() => pintar(real));
            transicao.ready.then(() => {
                document.documentElement.animate(
                    { clipPath: [`circle(0 at ${x}px ${y}px)`, `circle(${raio}px at ${x}px ${y}px)`] },
                    { duration: 650, easing: "cubic-bezier(.4, 0, .2, 1)", pseudoElement: "::view-transition-new(root)" }
                );
            }).catch(() => {});
            return;
        }
        const html = document.documentElement;
        html.classList.add("cf-trocando");
        pintar(real);
        setTimeout(() => html.classList.remove("cf-trocando"), 500);
    }

    function atualizarBotoes() {
        const atual = escolhido();
        document.querySelectorAll("[data-cf-tema-botao]").forEach(b => {
            b.innerHTML = TEMAS[atual].icone;
            b.title = "Tema: " + TEMAS[atual].nome;
        });
        document.querySelectorAll(".cf-tema-opcao").forEach(o => {
            o.classList.toggle("ativo", o.dataset.opcao === atual);
        });
    }

    function fecharMenus() {
        document.querySelectorAll(".cf-tema-menu").forEach(m => m.remove());
    }

    function abrirMenu(botao) {
        const aberto = botao.parentElement.querySelector(".cf-tema-menu");
        fecharMenus();
        if (aberto) return;
        const menu = document.createElement("div");
        menu.className = "cf-tema-menu";
        menu.setAttribute("role", "menu");
        menu.innerHTML = Object.entries(TEMAS).map(([chave, t]) => `
            <button type="button" class="cf-tema-opcao" data-opcao="${chave}" role="menuitem">
                <span class="cf-bolinha cf-bolinha-${chave}">${t.bolinha}</span>
                <span>${t.nome}<small>${t.dica}</small></span>
            </button>`).join("");
        menu.addEventListener("click", e => {
            const opcao = e.target.closest(".cf-tema-opcao");
            if (!opcao) return;
            aplicar(opcao.dataset.opcao, botao);
            fecharMenus();
        });
        botao.parentElement.style.position = "relative";
        botao.parentElement.appendChild(menu);
        atualizarBotoes();
    }

    document.addEventListener("click", e => {
        const botao = e.target.closest("[data-cf-tema-botao]");
        if (botao) {
            e.preventDefault();
            abrirMenu(botao);
            return;
        }
        if (!e.target.closest(".cf-tema-menu")) fecharMenus();
    });
    document.addEventListener("keydown", e => { if (e.key === "Escape") fecharMenus(); });

    // No automático, a virada das 6h/18h acontece sem recarregar.
    setInterval(() => {
        if (escolhido() === "auto") {
            const real = resolver("auto");
            if (real !== document.documentElement.dataset.tema) aplicar("auto");
        }
    }, 60000);

    // Barra de progresso fina ao navegar entre as telas.
    document.addEventListener("click", e => {
        const link = e.target.closest("a[href]");
        if (!link || link.target === "_blank" || e.ctrlKey || e.metaKey || e.shiftKey) return;
        const href = link.getAttribute("href");
        if (!href || href.startsWith("#") || href.startsWith("javascript") || link.hasAttribute("download")) return;
        let barra = document.querySelector(".cf-progresso");
        if (!barra) {
            barra = document.createElement("div");
            barra.className = "cf-progresso";
            document.body.appendChild(barra);
        }
        requestAnimationFrame(() => barra.classList.add("andando"));
    });
    window.addEventListener("pageshow", () => {
        document.querySelectorAll(".cf-progresso").forEach(b => b.remove());
    });

    window.CFTema = { aplicar, escolhido, resolver };
    document.addEventListener("DOMContentLoaded", atualizarBotoes);
    atualizarBotoes();
})();
