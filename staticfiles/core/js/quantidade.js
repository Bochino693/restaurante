/* =========================================================================
   QUANTIDADE — modal para escolher quantas unidades adicionar (bebidas).

   Usado no Caixa e no cardápio do cliente:

       Quantidade.abrir({
           nome, preco, imagem,          // o que aparece no modal
           max: 99,                      // limite do seletor
           origem: elemento,             // de onde o modal "nasce" (o card)
           destino: [el1, el2],          // para onde a foto voa (o 1º visível)
           aoConfirmar: qtd => {...}     // chamado quando a foto chega
       });

   Teclado: números digitam a quantidade, + e − (ou setas) mudam,
   Enter confirma, Esc fecha.
   ========================================================================= */
(function () {
    "use strict";

    const reais = v => Number(v || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
    const semAnimacao = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const ATALHOS = [1, 2, 3, 6, 12];

    let raiz = null;
    let estado = null;
    let quemAbriu = null;
    let digitado = "";
    let timerDigitado = null;
    let timerFechar = null;

    function el(sel) { return raiz.querySelector(sel); }

    function montar() {
        raiz = document.createElement("div");
        raiz.className = "qtd-fundo";
        raiz.hidden = true;
        raiz.innerHTML = `
            <div class="qtd-caixa" role="dialog" aria-modal="true" aria-labelledby="qtdNome">
                <button type="button" class="qtd-fechar" data-qtd="fechar" aria-label="Fechar"><i class="fas fa-xmark"></i></button>
                <div class="qtd-foto"><img alt="" decoding="async"><i class="fas fa-bottle-water"></i></div>
                <div class="qtd-corpo">
                    <h2 id="qtdNome" class="qtd-nome"></h2>
                    <p class="qtd-preco"></p>
                    <div class="qtd-seletor">
                        <button type="button" class="qtd-passo" data-qtd="menos" aria-label="Menos uma"><i class="fas fa-minus"></i></button>
                        <output class="qtd-numero" aria-live="polite" aria-label="Quantidade">1</output>
                        <button type="button" class="qtd-passo" data-qtd="mais" aria-label="Mais uma"><i class="fas fa-plus"></i></button>
                    </div>
                    <div class="qtd-atalhos" role="group" aria-label="Quantidades rápidas">
                        ${ATALHOS.map(n => `<button type="button" class="qtd-atalho" data-qtd="definir" data-valor="${n}">${n}</button>`).join("")}
                    </div>
                    <button type="button" class="qtd-confirmar" data-qtd="confirmar">
                        <span class="qtd-confirmar-texto"><span>Adicionar <b class="qtd-n">1</b></span><b class="qtd-total">R$ 0,00</b></span>
                        <span class="qtd-feito"><i class="fas fa-check"></i> Adicionado!</span>
                    </button>
                    <small class="qtd-dica">Digite o número ou use + e −. Enter confirma.</small>
                </div>
            </div>`;
        document.body.appendChild(raiz);

        raiz.addEventListener("click", e => {
            if (e.target === raiz) return fechar();
            const botao = e.target.closest("[data-qtd]");
            if (!botao || !estado) return;
            const acao = botao.dataset.qtd;
            if (acao === "fechar") fechar();
            if (acao === "menos") mudar(estado.qtd - 1);
            if (acao === "mais") mudar(estado.qtd + 1);
            if (acao === "definir") mudar(Number(botao.dataset.valor));
            if (acao === "confirmar") confirmar();
        });

        // Captura: as teclas do modal não chegam aos atalhos da página.
        window.addEventListener("keydown", e => {
            if (!estado || raiz.hidden) return;
            const tecla = e.key;
            let tratou = true;
            if (tecla === "Escape") fechar();
            else if (tecla === "Enter") confirmar();
            else if (tecla === "+" || tecla === "ArrowUp" || tecla === "ArrowRight") mudar(estado.qtd + 1);
            else if (tecla === "-" || tecla === "ArrowDown" || tecla === "ArrowLeft") mudar(estado.qtd - 1);
            else if (/^[0-9]$/.test(tecla)) digitar(tecla);
            else if (tecla === "Backspace") { digitado = digitado.slice(0, -1); if (digitado) mudar(Number(digitado)); }
            else if (tecla === "Tab") prenderFoco(e);
            else tratou = false;
            if (tratou && tecla !== "Tab") { e.preventDefault(); e.stopPropagation(); }
        }, true);
    }

    function digitar(tecla) {
        clearTimeout(timerDigitado);
        digitado = (digitado + tecla).replace(/^0+/, "").slice(0, String(estado.max).length);
        if (digitado) mudar(Number(digitado));
        timerDigitado = setTimeout(() => { digitado = ""; }, 1200);
    }

    function prenderFoco(e) {
        const focaveis = [...raiz.querySelectorAll("button")];
        const primeiro = focaveis[0];
        const ultimo = focaveis[focaveis.length - 1];
        if (e.shiftKey && document.activeElement === primeiro) { e.preventDefault(); ultimo.focus(); }
        else if (!e.shiftKey && document.activeElement === ultimo) { e.preventDefault(); primeiro.focus(); }
    }

    function mudar(qtd) {
        if (!estado || estado.confirmando) return;
        const novo = Math.max(1, Math.min(estado.max, Math.round(qtd) || 1));
        const subiu = novo > estado.qtd;
        estado.qtd = novo;
        const numero = el(".qtd-numero");
        numero.textContent = novo;
        numero.classList.remove("sobe", "desce");
        void numero.offsetWidth;   // reinicia a animação
        if (novo !== estado.anterior) numero.classList.add(subiu ? "sobe" : "desce");
        estado.anterior = novo;
        el(".qtd-n").textContent = novo;
        el(".qtd-total").textContent = reais(estado.preco * novo);
        el('[data-qtd="menos"]').disabled = novo <= 1;
        el('[data-qtd="mais"]').disabled = novo >= estado.max;
        raiz.querySelectorAll(".qtd-atalho").forEach(b => {
            b.classList.toggle("ativo", Number(b.dataset.valor) === novo);
            b.hidden = Number(b.dataset.valor) > estado.max;
        });
    }

    function abrir(opcoes) {
        if (!raiz) montar();
        clearTimeout(timerFechar);
        estado = {
            nome: opcoes.nome || "",
            preco: Number(opcoes.preco || 0),
            max: Math.max(1, Number(opcoes.max || 99)),
            destino: [].concat(opcoes.destino || []),
            aoConfirmar: opcoes.aoConfirmar || function () {},
            qtd: 1,
            anterior: 1,
            confirmando: false,
        };
        digitado = "";
        quemAbriu = document.activeElement;

        el(".qtd-nome").textContent = estado.nome;
        el(".qtd-preco").textContent = reais(estado.preco) + " cada";
        const foto = el(".qtd-foto");
        const img = foto.querySelector("img");
        foto.classList.toggle("sem-foto", !opcoes.imagem);
        if (opcoes.imagem) img.src = opcoes.imagem; else img.removeAttribute("src");
        raiz.classList.remove("confirmado", "aberto");
        mudar(1);
        el(".qtd-numero").classList.remove("sobe", "desce");

        // O modal cresce a partir do card clicado.
        const caixa = el(".qtd-caixa");
        caixa.style.transformOrigin = "50% 50%";
        if (opcoes.origem && opcoes.origem.getBoundingClientRect) {
            const r = opcoes.origem.getBoundingClientRect();
            raiz.hidden = false;
            const c = caixa.getBoundingClientRect();
            caixa.style.transformOrigin = `${r.left + r.width / 2 - c.left}px ${r.top + r.height / 2 - c.top}px`;
        }
        raiz.hidden = false;
        document.documentElement.classList.add("qtd-travado");
        requestAnimationFrame(() => requestAnimationFrame(() => raiz.classList.add("aberto")));
        setTimeout(() => el(".qtd-confirmar").focus({ preventScroll: true }), 80);
    }

    function fechar() {
        if (!raiz || raiz.hidden) return;
        raiz.classList.remove("aberto");
        document.documentElement.classList.remove("qtd-travado");
        const fim = () => { raiz.hidden = true; raiz.classList.remove("confirmado"); estado = null; };
        if (semAnimacao()) fim(); else timerFechar = setTimeout(fim, 260);
        if (quemAbriu && document.contains(quemAbriu) && quemAbriu.focus) quemAbriu.focus({ preventScroll: true });
    }

    function alvoVisivel(lista) {
        for (const alvo of lista) {
            const elemento = typeof alvo === "string" ? document.querySelector(alvo) : alvo;
            if (!elemento) continue;
            const r = elemento.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) return elemento;
        }
        return null;
    }

    /* A foto (com o "+N") voa num arco até o carrinho; quando chega, o item
       entra na lista e o carrinho dá um pulo. */
    function confirmar() {
        if (!estado || estado.confirmando) return;
        estado.confirmando = true;
        const { qtd, aoConfirmar } = estado;
        raiz.classList.add("confirmado");
        const destino = alvoVisivel(estado.destino);
        const foto = el(".qtd-foto");

        const chegar = () => {
            try { aoConfirmar(qtd); } catch (erro) { console.error(erro); }
            if (destino) {
                destino.classList.remove("qtd-chegou");
                void destino.offsetWidth;
                destino.classList.add("qtd-chegou");
                setTimeout(() => destino.classList.remove("qtd-chegou"), 700);
            }
        };

        if (semAnimacao() || !destino || !foto.animate) {
            chegar();
            setTimeout(fechar, semAnimacao() ? 400 : 650);
            return;
        }

        const de = foto.getBoundingClientRect();
        const para = destino.getBoundingClientRect();
        const voo = document.createElement("div");
        voo.className = "qtd-voo";
        voo.style.left = de.left + "px";
        voo.style.top = de.top + "px";
        voo.style.width = de.width + "px";
        voo.style.height = de.height + "px";
        voo.innerHTML = (foto.classList.contains("sem-foto")
            ? '<i class="fas fa-bottle-water"></i>'
            : `<img src="${foto.querySelector("img").src}" alt="">`) + `<b>+${qtd}</b>`;
        document.body.appendChild(voo);

        const dx = para.left + para.width / 2 - (de.left + de.width / 2);
        const dy = para.top + para.height / 2 - (de.top + de.height / 2);
        const alto = Math.min(dy, 0) - 90;   // o arco passa acima dos dois pontos
        const duracao = 720;
        setTimeout(() => {
            voo.animate([
                { transform: "translate(0, 0) scale(1) rotate(0deg)", opacity: 1 },
                { transform: `translate(${dx * 0.45}px, ${alto}px) scale(.62) rotate(-8deg)`, opacity: 1, offset: 0.45 },
                { transform: `translate(${dx}px, ${dy}px) scale(.12) rotate(6deg)`, opacity: .35 }
            ], { duration: duracao, easing: "cubic-bezier(.45, 0, .25, 1)", fill: "forwards" })
                .finished.then(() => { voo.remove(); chegar(); }, () => { voo.remove(); chegar(); });
        }, 260);   // primeiro o botão vira "Adicionado!"
        setTimeout(fechar, 420);
    }

    window.Quantidade = { abrir, fechar };
})();
