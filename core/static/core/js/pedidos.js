/* =========================================================================
   PEDIDOS — modal de detalhes.

   - Cada cartão do topo (Total, Dinheiro, Pix, Cartão, Misto, Taxa motoca,
     Bebidas e a conferência do caixa) abre o relatório dele: o valor, os
     destaques, a divisão em barras e a lista dos pedidos que formam o número.
   - Clicar num pedido (na grade ou numa lista de relatório) abre o pedido
     completo. Os pedidos da página já vêm prontos no HTML (abre na hora);
     os outros são buscados no servidor.
   ========================================================================= */
(function () {
    "use strict";

    const raiz = document.getElementById("painelModal");
    if (!raiz) return;

    const conteudo = document.getElementById("pmConteudo");
    const rodape = document.getElementById("pmRodape");
    const botaoFechar = raiz.querySelector(".pm-fechar");
    const URL_RELATORIO = raiz.dataset.urlRelatorio;
    const URL_DETALHE = raiz.dataset.urlDetalhe;
    const URL_ESTATISTICAS = raiz.dataset.urlEstatisticas || "";   // só para quem vê as Estatísticas
    const ABA_DO_RELATORIO = { total: "vendas", bebidas: "bebidas" };   // os demais: "pagamentos"
    const semAnimacao = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

    const detalhesDaPagina = lerJson("pedidosPaginaJson", {});
    const relatorios = {};
    const detalhes = {};

    let pilha = [];          // telas abertas, para o botão "Voltar"
    let quemAbriu = null;    // devolve o foco ao fechar
    let timerFechar = null;

    function lerJson(id, padrao) {
        const el = document.getElementById(id);
        if (!el) return padrao;
        try { return JSON.parse(el.textContent); } catch (_) { return padrao; }
    }

    // Tudo o que vem do banco (nome do cliente, observação...) passa por aqui.
    function esc(texto) {
        return String(texto == null ? "" : texto).replace(/[&<>"']/g, c => (
            { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
        ));
    }

    function icone(classe) {
        return /\bfa-(brands|regular|solid)\b|\bfa[brs]\b/.test(classe) ? classe : "fas " + classe;
    }

    function numero(texto) {
        const n = Number(String(texto).replace(/[^\d,.-]/g, "").replace(/\./g, "").replace(",", "."));
        return Number.isFinite(n) ? n : 0;
    }

    /* ------------------------------------------------- abrir e fechar */
    function abrirModal() {
        clearTimeout(timerFechar);
        if (!raiz.hidden && raiz.classList.contains("aberto")) return;
        quemAbriu = document.activeElement;
        raiz.hidden = false;
        document.documentElement.classList.add("pm-travado");
        // um quadro para o navegador pintar o estado inicial e animar a entrada
        requestAnimationFrame(() => requestAnimationFrame(() => raiz.classList.add("aberto")));
        setTimeout(() => botaoFechar.focus({ preventScroll: true }), 60);
    }

    function fecharModal() {
        if (raiz.hidden) return;
        raiz.classList.remove("aberto");
        document.documentElement.classList.remove("pm-travado");
        pilha = [];
        timerFechar = setTimeout(() => {
            raiz.hidden = true;
            conteudo.innerHTML = "";
            rodape.innerHTML = "";
        }, semAnimacao ? 0 : 320);
        if (quemAbriu && document.contains(quemAbriu)) quemAbriu.focus({ preventScroll: true });
    }

    function mostrar(html, htmlRodape) {
        const voltar = pilha.length > 1
            ? '<button type="button" class="pm-voltar" data-acao="voltar"><i class="fas fa-arrow-left"></i> Voltar</button>'
            : "";
        conteudo.innerHTML = voltar + html;
        rodape.innerHTML = htmlRodape || "";
        conteudo.scrollTop = 0;
        conteudo.classList.remove("pm-anim");
        void conteudo.offsetWidth;   // reinicia a animação de entrada
        conteudo.classList.add("pm-anim");
        animar(conteudo);
    }

    function carregando() {
        mostrar(
            '<div class="pm-esqueleto" aria-label="Carregando"><i></i><i style="width:60%"></i>' +
            '<i style="width:40%"></i><i class="alto"></i><i></i><i style="width:85%"></i><i style="width:70%"></i></div>'
        );
    }

    function erro(mensagem, tentarDeNovo) {
        mostrar(
            `<div class="pm-erro"><i class="fas fa-triangle-exclamation"></i>${esc(mensagem)}` +
            '<br><button type="button" class="pm-btn" data-acao="tentar"><i class="fas fa-rotate-right"></i> Tentar de novo</button></div>'
        );
        conteudo.querySelector("[data-acao=tentar]").addEventListener("click", tentarDeNovo);
    }

    /* Números sobem de zero até o valor; barras crescem até a porcentagem. */
    function animar(el) {
        el.querySelectorAll("[data-largura]").forEach(barra => {
            requestAnimationFrame(() => requestAnimationFrame(() => { barra.style.width = barra.dataset.largura + "%"; }));
        });
        if (semAnimacao) return;
        el.querySelectorAll("[data-contar]").forEach(alvo => contar(alvo, numero(alvo.dataset.contar), 750));
    }

    function contar(el, valor, duracao) {
        if (!valor) return;
        const inicio = performance.now();
        const passo = agora => {
            const t = Math.min(1, (agora - inicio) / duracao);
            const suave = 1 - Math.pow(1 - t, 3);
            el.textContent = brl.format(valor * suave);
            if (t < 1) requestAnimationFrame(passo);
        };
        requestAnimationFrame(passo);
    }

    async function buscar(url) {
        const resposta = await fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" }, credentials: "same-origin" });
        const tipo = resposta.headers.get("content-type") || "";
        if (!resposta.ok || !tipo.includes("json")) {
            throw new Error(resposta.status === 404 ? "Não encontrado." : "Não foi possível carregar. A sessão pode ter expirado: recarregue a página.");
        }
        return resposta.json();
    }

    /* --------------------------------------------------------- relatório */
    function filtrosDaTela() {
        const params = new URLSearchParams(window.location.search);
        params.delete("page");
        const texto = params.toString();
        return texto ? "?" + texto : "";
    }

    async function abrirRelatorio(tipo) {
        abrirModal();
        pilha = [{ tipo: "relatorio", chave: tipo }];
        if (relatorios[tipo]) return mostrarRelatorio(relatorios[tipo]);
        carregando();
        try {
            const dados = await buscar(URL_RELATORIO.replace("TIPO", encodeURIComponent(tipo)) + filtrosDaTela());
            relatorios[tipo] = dados;
            const topo = pilha[pilha.length - 1];
            if (topo && topo.tipo === "relatorio" && topo.chave === tipo) mostrarRelatorio(dados);
        } catch (e) {
            erro(e.message, () => abrirRelatorio(tipo));
        }
    }

    function mostrarRelatorio(d) {
        const rodapeHtml = URL_ESTATISTICAS
            ? `<a class="pm-btn" href="${esc(URL_ESTATISTICAS)}#${ABA_DO_RELATORIO[d.tipo] || "pagamentos"}">` +
              '<i class="fas fa-chart-column"></i> Ver nas Estatísticas</a>'
            : "";
        mostrar(htmlRelatorio(d), rodapeHtml);
    }

    function htmlRelatorio(d) {
        const destaques = (d.destaques || []).map(x =>
            `<div class="pm-destaque"><span>${esc(x.rotulo)}</span><strong>${esc(x.valor)}</strong></div>`
        ).join("");

        const quebra = (d.quebra || []).length ? `
            <h3 class="pm-secao">Divisão</h3>
            <div class="pm-quebra">${d.quebra.map((q, i) => `
                <div class="pm-barra-linha tom-${esc(q.tom)}" style="--i:${i}">
                    <div class="pm-barra-topo">
                        <span><i class="pm-ponto"></i>${esc(q.rotulo)}</span>
                        <strong>R$ ${esc(q.valor)}<em>${String(q.pct).replace(".", ",")}%</em></strong>
                    </div>
                    <div class="pm-barra"><i data-largura="${Number(q.pct) || 0}"></i></div>
                    ${q.qtd ? `<small>${esc(q.qtd)}</small>` : ""}
                </div>`).join("")}
            </div>` : "";

        const itens = d.itens ? `
            <h3 class="pm-secao">Bebidas vendidas <span>${d.itens.length}</span></h3>
            ${d.itens.length ? `<div class="pm-quebra">${d.itens.map((b, i) => `
                <div class="pm-barra-linha tom-bebidas" style="--i:${i}">
                    <div class="pm-barra-topo">
                        <span><i class="pm-ponto"></i>${esc(b.nome)}</span>
                        <strong>R$ ${esc(b.valor)}<em>${b.unidades} un.</em></strong>
                    </div>
                    <div class="pm-barra"><i data-largura="${Number(b.pct) || 0}"></i></div>
                </div>`).join("")}</div>`
            : '<div class="pm-vazio"><i class="fas fa-bottle-water"></i>Nenhuma bebida vendida neste período.</div>'}` : "";

        const pedidos = d.pedidos || [];
        const lista = pedidos.length ? `<ul class="pm-lista">${pedidos.map((p, i) => `
            <li style="--i:${i}">
                <button type="button" class="pm-linha" data-abrir-pedido="${p.id}">
                    <span class="pm-linha-num">#${p.id}</span>
                    <span class="pm-linha-meio">
                        <strong>${esc(p.cliente)}</strong>
                        <small>${esc(p.data)} às ${esc(p.hora)} · ${esc(p.detalhe)}</small>
                    </span>
                    <span class="pm-linha-fim">
                        <strong>R$ ${esc(p.valor)}</strong>
                        <span class="pc-status st-${esc(String(p.status).toLowerCase())}">${esc(p.status_label)}</span>
                    </span>
                    <i class="fas fa-chevron-right" aria-hidden="true"></i>
                </button>
            </li>`).join("")}</ul>`
            : '<div class="pm-vazio"><i class="fas fa-receipt"></i>Nenhum pedido neste período.</div>';

        const cortado = d.pedidos_total > pedidos.length
            ? `<p class="pm-nota">Mostrando os ${pedidos.length} pedidos mais recentes de ${d.pedidos_total}.</p>` : "";

        return `
            <header class="pm-cabeca tom-${esc(d.tom)}">
                <span class="pm-icone"><i class="${esc(icone(d.icone))}"></i></span>
                <div>
                    <span class="pm-eyebrow">${esc(d.periodo)}</span>
                    <h2 id="pmTitulo">${esc(d.titulo)}</h2>
                    <p>${esc(d.descricao)}</p>
                </div>
            </header>
            <div class="pm-hero tom-${esc(d.tom)}">
                <span>Valor</span>
                <strong data-contar="${esc(d.valor)}">R$ ${esc(d.valor)}</strong>
            </div>
            <div class="pm-destaques">${destaques}</div>
            ${quebra}
            ${itens}
            <h3 class="pm-secao">Pedidos <span>${d.pedidos_total || 0}</span></h3>
            ${lista}
            ${cortado}
            ${d.nota ? `<p class="pm-nota"><i class="fas fa-circle-info"></i> ${esc(d.nota)}</p>` : ""}`;
    }

    /* ------------------------------------------------------------ pedido */
    async function abrirPedido(id, deUmRelatorio) {
        id = String(id);
        abrirModal();
        if (deUmRelatorio) pilha.push({ tipo: "pedido", id });
        else pilha = [{ tipo: "pedido", id }];

        const pronto = detalhes[id] || detalhesDaPagina[id];
        if (pronto) return mostrarPedido(pronto);
        carregando();
        try {
            const dados = await buscar(URL_DETALHE.replace(/\/0\/(detalhes\/)?$/, `/${encodeURIComponent(id)}/$1`));
            detalhes[id] = dados;
            const topo = pilha[pilha.length - 1];
            if (topo && topo.tipo === "pedido" && topo.id === id) mostrarPedido(dados);
        } catch (e) {
            erro(e.message, () => { pilha.pop(); abrirPedido(id, pilha.length > 0); });
        }
    }

    const ETAPAS = [
        { rotulo: "Pago", icone: "fa-money-bill" },
        { rotulo: "Em preparo", icone: "fa-fire-burner" },
        { rotulo: "Em entrega", icone: "fa-motorcycle" },
        { rotulo: "Finalizado", icone: "fa-circle-check" },
    ];

    function haQuanto(iso) {
        const minutos = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
        if (!Number.isFinite(minutos) || minutos < 0) return "";
        if (minutos < 1) return "agora há pouco";
        if (minutos < 60) return `há ${minutos} min`;
        const horas = Math.floor(minutos / 60);
        if (horas < 24) return `há ${horas} h`;
        const dias = Math.floor(horas / 24);
        return dias === 1 ? "ontem" : `há ${dias} dias`;
    }

    function mostrarPedido(p) {
        const cancelado = p.status === "CANCELADO";
        const finalizado = p.etapa === ETAPAS.length - 1;
        const quando = haQuanto(p.criado_iso);
        const feita = i => i < p.etapa || finalizado;

        const linhaDoTempo = cancelado
            ? '<div class="pm-cancelado"><i class="fas fa-ban"></i> Pedido cancelado: não entra nos relatórios.</div>'
            : `<div class="pm-linha-tempo" aria-label="Andamento do pedido: ${esc(p.status_label)}">
                   <span class="pm-progresso" data-largura="${Math.max(0, p.etapa) / 3 * 75}"></span>
                   ${ETAPAS.map((e, i) => `
                       <div class="pm-etapa ${feita(i) ? "feita" : ""} ${i === p.etapa && !finalizado ? "atual" : ""}">
                           <b><i class="fas ${feita(i) ? "fa-check" : e.icone}"></i></b>${e.rotulo}
                       </div>`).join("")}
               </div>`;

        const cliente = `
            <section class="pm-bloco">
                <h3><i class="fas fa-user"></i> Cliente</h3>
                <p class="pm-forte">${esc(p.cliente || "Sem nome")}</p>
                ${p.telefone ? `<p><i class="fas fa-phone"></i> ${esc(p.telefone)}</p>` : ""}
            </section>`;

        const local = p.entrega ? `
            <section class="pm-bloco">
                <h3><i class="fas fa-location-dot"></i> Entrega</h3>
                <p class="pm-forte">${esc(p.endereco || "Sem endereço")}</p>
                ${p.cep ? `<p>CEP ${esc(p.cep)}</p>` : ""}
            </section>` : `
            <section class="pm-bloco">
                <h3><i class="fas fa-store"></i> Retirada</h3>
                <p class="pm-forte">No balcão</p>
            </section>`;

        const pagamento = `
            <section class="pm-bloco">
                <h3><i class="fas fa-wallet"></i> Pagamento</h3>
                <p class="pm-forte">${esc(p.pagamento_label)}</p>
                <p>${p.impresso ? "Notinha impressa" : "Notinha ainda não impressa"}</p>
            </section>`;

        const observacao = p.descricao ? `
            <section class="pm-bloco pm-obs">
                <h3><i class="fas fa-note-sticky"></i> Observação</h3>
                <p>${esc(p.descricao)}</p>
            </section>` : "";

        const itens = (p.itens || []).map((item, i) => `
            <li class="pm-item" style="--i:${i}">
                <span class="pm-item-qtd">${esc(item.qtd)}x</span>
                <span class="pm-item-nome">
                    <strong><i class="fas ${item.bebida ? "fa-bottle-water" : "fa-utensils"}"></i> ${esc(item.nome)}</strong>
                    ${item.adicionais && item.adicionais.length ? `<span class="pm-adicionais">${item.adicionais.map(a =>
                        `<span>+ ${Number(a.qtd) > 1 ? esc(a.qtd) + "x " : ""}${esc(a.nome)}${a.preco ? " (R$ " + esc(a.preco) + ")" : ""}</span>`
                    ).join("")}</span>` : ""}
                    <small>R$ ${esc(item.unitario)} cada</small>
                </span>
                <span class="pm-item-valor">R$ ${esc(item.subtotal)}</span>
            </li>`).join("");

        const conta = `
            <section class="pm-conta">
                <div><span>Itens</span><span>R$ ${esc(p.bruto)}</span></div>
                ${p.desconto ? `<div class="pm-desconto"><span>Desconto</span><span>− R$ ${esc(p.desconto)}</span></div>` : ""}
                <div><span>Total do pedido</span><span>R$ ${esc(p.total)}</span></div>
                ${p.taxa ? `<div><span><i class="fas fa-motorcycle"></i> Taxa do motoca</span><span>+ R$ ${esc(p.taxa)}</span></div>` : ""}
                <div class="pm-conta-final"><span>Total a receber</span><strong data-contar="${esc(p.total_final)}">R$ ${esc(p.total_final)}</strong></div>
            </section>`;

        const acoes = [
            p.proximo && !cancelado
                ? `<button type="button" class="pm-btn pm-btn-primario" data-acao="status"><i class="fas fa-arrow-right"></i> ${esc(p.proximo_label)}</button>` : "",
            '<button type="button" class="pm-btn" data-acao="imprimir"><i class="fas fa-print"></i> Reimprimir</button>',
            p.whatsapp ? `<a class="pm-btn pm-btn-whats" href="${esc(p.whatsapp)}" target="_blank" rel="noopener"><i class="fa-brands fa-whatsapp"></i> WhatsApp</a>` : "",
            p.mapa ? `<a class="pm-btn" href="${esc(p.mapa)}" target="_blank" rel="noopener"><i class="fas fa-map-location-dot"></i> Mapa</a>` : "",
        ].join("");

        mostrar(`
            <header class="pm-cabeca tom-pedido">
                <span class="pm-icone"><i class="fas fa-receipt"></i></span>
                <div>
                    <span class="pm-eyebrow">${esc(p.data)} às ${esc(p.hora)}${quando ? " · " + esc(quando) : ""}</span>
                    <h2 id="pmTitulo">Pedido #${esc(p.id)}</h2>
                    <div class="pm-tags">
                        <span class="pc-status st-${esc(String(p.status).toLowerCase())}">${esc(p.status_label)}</span>
                        <span class="pm-tag"><i class="fas ${p.entrega ? "fa-motorcycle" : "fa-store"}"></i> ${p.entrega ? "Entrega" : "Retirada"}</span>
                        <span class="pm-tag">${esc(p.pagamento_label)}</span>
                        ${p.impresso ? "" : '<span class="pm-tag pm-tag-alerta"><i class="fas fa-print"></i> Não impresso</span>'}
                    </div>
                </div>
            </header>
            ${linhaDoTempo}
            <div class="pm-blocos">${cliente}${local}${pagamento}${observacao}</div>
            <section class="pm-itens-pedido">
                <h3 class="pm-secao">Itens <span>${esc(p.unidades)} un.</span></h3>
                <ul>${itens}</ul>
            </section>
            ${conta}`, acoes);

        rodape.dataset.pedido = p.id;
        rodape.dataset.status = p.status;
        rodape.dataset.pagamento = p.pagamento;
    }

    /* ------------------------------------------------------------ eventos */
    function voltar() {
        pilha.pop();
        const topo = pilha[pilha.length - 1];
        if (!topo) return fecharModal();
        if (topo.tipo === "relatorio" && relatorios[topo.chave]) mostrarRelatorio(relatorios[topo.chave]);
        else if (topo.tipo === "relatorio") abrirRelatorio(topo.chave);
    }

    raiz.addEventListener("click", e => {
        if (e.target === raiz) return fecharModal();
        if (e.target.closest(".pm-fechar")) return fecharModal();
        const linha = e.target.closest("[data-abrir-pedido]");
        if (linha) return abrirPedido(linha.dataset.abrirPedido, true);
        const acao = e.target.closest("[data-acao]");
        if (!acao) return;
        if (acao.dataset.acao === "voltar") voltar();
        // Atualizar status e reimprimir usam os modais que a tela já tinha
        // (eles abrem por cima deste).
        if (acao.dataset.acao === "status" && window.abrirModalStatus) {
            window.abrirModalStatus(rodape.dataset.pedido, rodape.dataset.status, rodape.dataset.pagamento);
        }
        if (acao.dataset.acao === "imprimir" && window.abrirModalImpressao) {
            window.abrirModalImpressao(rodape.dataset.pedido);
        }
    });

    document.addEventListener("click", e => {
        const cartao = e.target.closest("[data-relatorio]");
        if (cartao) {
            e.preventDefault();
            abrirRelatorio(cartao.dataset.relatorio);
            return;
        }
        const pedido = e.target.closest(".pedido-card");
        // Os botões do card (Atualizar, Reimprimir) continuam com a função deles.
        if (pedido && !e.target.closest("button, a")) abrirPedido(pedido.dataset.pedidoId, false);
    });

    document.addEventListener("keydown", e => {
        const pedido = e.target.closest && e.target.closest(".pedido-card");
        if (pedido && e.target === pedido && (e.key === "Enter" || e.key === " ")) {
            e.preventDefault();
            abrirPedido(pedido.dataset.pedidoId, false);
            return;
        }
        if (raiz.hidden) return;
        // Com o modal de impressão ou de status aberto por cima, o Esc é dele.
        const outroAberto = document.querySelector(".modal-pedido:not(.hidden)");
        if (e.key === "Escape" && !outroAberto) {
            e.preventDefault();
            pilha.length > 1 ? voltar() : fecharModal();
        }
        if (e.key === "Tab" && !outroAberto) {
            const focaveis = [...raiz.querySelectorAll("button, a[href]")].filter(el => el.offsetParent !== null);
            if (!focaveis.length) return;
            const primeiro = focaveis[0];
            const ultimo = focaveis[focaveis.length - 1];
            if (e.shiftKey && document.activeElement === primeiro) { e.preventDefault(); ultimo.focus(); }
            else if (!e.shiftKey && document.activeElement === ultimo) { e.preventDefault(); primeiro.focus(); }
        }
    });

    // Os valores do topo sobem de zero quando a tela abre.
    if (!semAnimacao) {
        document.querySelectorAll(".rel-valor").forEach((el, i) => {
            const valor = numero(el.textContent);
            if (valor) setTimeout(() => contar(el, valor, 900), 80 + i * 45);
        });
    }
})();
