/* =========================================================================
   QUANTIDADE — o modal de quantidade (bebidas) e o efeito de "entrar no
   carrinho" de todos os itens. Usado no Caixa e no cardápio do cliente.

       Quantidade.abrir({
           nome, preco, imagem,          // o que aparece no modal
           cor: "#0284c7",               // cor do tipo de item
           max: 99,                      // limite do seletor
           origem: elemento,             // de onde o modal "nasce" (o card)
           destino: [el1, el2],          // para onde a foto voa (o 1º visível)
           aoConfirmar: qtd => {...},    // na hora do clique: põe no carrinho
           aoChegar: () => {...}         // quando a foto chega: destaca o item
       });

       Quantidade.voar({ de, imagem, icone, rotulo: "+1", cor, destino, aoChegar });

   O item entra no carrinho NA HORA do clique; a foto voando é só o
   efeito. Assim quem finaliza o pedido logo em seguida não perde nada.

   Teclado no modal: números digitam a quantidade, + e − (ou setas) mudam,
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
                <div class="qtd-foto"><img alt="" decoding="async"><i class="fas fa-bottle-water qtd-icone"></i></div>
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
            cor: opcoes.cor || "",
            icone: opcoes.icone || "fa-bottle-water",
            destino: [].concat(opcoes.destino || []),
            aoConfirmar: opcoes.aoConfirmar || function () {},
            aoChegar: opcoes.aoChegar || function () {},
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
        foto.querySelector(".qtd-icone").className = `fas ${estado.icone} qtd-icone`;
        // a cor do tipo de item tinge o modal (botão, atalho escolhido, +N)
        if (estado.cor) raiz.style.setProperty("--qtd-tom", estado.cor);
        else raiz.style.removeProperty("--qtd-tom");
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

    function pular(destino) {
        if (!destino) return;
        destino.classList.remove("qtd-chegou");
        void destino.offsetWidth;
        destino.classList.add("qtd-chegou");
        setTimeout(() => destino.classList.remove("qtd-chegou"), 700);
    }

    /* A foto (com o "+N") voa num arco até o carrinho, que dá um pulo quando
       ela chega. `de` é o elemento de onde ela sai (a foto do card ou do
       modal). Sem destino visível ou com "menos movimento" ligado no
       sistema, só chama aoChegar. */
    function voar(opcoes) {
        const destino = alvoVisivel([].concat(opcoes.destino || []));
        const aoChegar = () => {
            pular(destino);
            try { (opcoes.aoChegar || function () {})(); } catch (erro) { console.error(erro); }
        };
        const origem = opcoes.de && opcoes.de.getBoundingClientRect ? opcoes.de : null;
        const de = origem ? origem.getBoundingClientRect() : null;
        if (semAnimacao() || !destino || !de || !de.width || !document.body.animate) {
            aoChegar();
            return;
        }
        // no máximo 150 px de lado: a foto de um card grande não cobre a tela
        const lado = Math.min(150, Math.max(de.width, de.height));
        const largura = de.width >= de.height ? lado : lado * de.width / de.height;
        const altura = de.height > de.width ? lado : lado * de.height / de.width;
        const x0 = de.left + de.width / 2 - largura / 2;
        const y0 = de.top + de.height / 2 - altura / 2;

        const voo = document.createElement("div");
        voo.className = "qtd-voo";
        if (opcoes.cor) voo.style.setProperty("--qtd-tom", opcoes.cor);
        Object.assign(voo.style, { left: x0 + "px", top: y0 + "px", width: largura + "px", height: altura + "px" });
        const foto = document.createElement(opcoes.imagem ? "img" : "i");
        if (opcoes.imagem) { foto.src = opcoes.imagem; foto.alt = ""; }
        else foto.className = `fas ${opcoes.icone || "fa-utensils"}`;
        const rotulo = document.createElement("b");
        rotulo.textContent = opcoes.rotulo || "+1";
        voo.append(foto, rotulo);
        document.body.appendChild(voo);

        const para = destino.getBoundingClientRect();
        const dx = para.left + para.width / 2 - (x0 + largura / 2);
        const dy = para.top + para.height / 2 - (y0 + altura / 2);
        // o arco passa acima dos dois pontos, mas nunca sai pelo topo da tela
        const alto = Math.max(Math.min(dy, 0) - 90, 16 - y0);
        voo.animate([
            { transform: "translate(0, 0) scale(.9) rotate(0deg)", opacity: 0 },
            { transform: "translate(0, -10px) scale(1.05) rotate(-2deg)", opacity: 1, offset: 0.12 },
            { transform: `translate(${dx * 0.45}px, ${alto}px) scale(.62) rotate(-8deg)`, opacity: 1, offset: 0.5 },
            { transform: `translate(${dx}px, ${dy}px) scale(.12) rotate(6deg)`, opacity: .35 }
        ], { duration: opcoes.duracao || 820, delay: opcoes.atraso || 0, easing: "cubic-bezier(.45, 0, .25, 1)", fill: "both" })
            .finished.then(() => { voo.remove(); aoChegar(); }, () => { voo.remove(); aoChegar(); });
    }

    function confirmar() {
        if (!estado || estado.confirmando) return;
        estado.confirmando = true;
        const { qtd, aoChegar, cor, icone } = estado;
        raiz.classList.add("confirmado");
        try { estado.aoConfirmar(qtd); } catch (erro) { console.error(erro); }
        const foto = el(".qtd-foto");
        voar({
            de: foto,
            imagem: foto.classList.contains("sem-foto") ? "" : foto.querySelector("img").src,
            icone,
            rotulo: "+" + qtd,
            cor,
            destino: estado.destino,
            atraso: 240,   // primeiro o botão vira "Adicionado!"
            aoChegar,
        });
        setTimeout(fechar, semAnimacao() ? 400 : 420);
    }

    window.Quantidade = { abrir, fechar, voar };
})();
