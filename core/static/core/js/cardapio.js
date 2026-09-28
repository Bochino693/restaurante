/* =========================================================================
   CARDÁPIO DO CLIENTE — Cantinho Família
   Um script só (antes eram dois declarando as mesmas variáveis com `let`,
   e o navegador recusava o segundo inteiro — por isso o botão de adicionar
   ao carrinho não fazia nada).
   ========================================================================= */
(function () {
    "use strict";

    const CHAVE = "cf_carrinho_v2";
    const CHAVE_CLIENTE = "cf_cliente";
    const body = document.body;
    const LOJA_ABERTA = body.dataset.lojaAberta === "1";
    const WHATSAPP = (body.dataset.whatsapp || "").replace(/\D/g, "");

    let catalogo = {};
    try {
        catalogo = JSON.parse(document.getElementById("catalogo-json").textContent || "{}");
    } catch (_) {
        catalogo = {};
    }

    const $ = id => document.getElementById(id);
    const reais = v => Number(v || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
    const escapar = t => String(t ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

    /* ---------------------------------------------------------- carrinho */
    function ler() {
        try {
            const c = JSON.parse(localStorage.getItem(CHAVE) || "[]");
            // Só mantém o que ainda existe no cardápio, com o preço de hoje.
            return Array.isArray(c) ? c.filter(i => catalogo[i.id]).map(i => ({
                ...i,
                nome: catalogo[i.id].nome,
                precoBase: Number(catalogo[i.id].preco)
            })) : [];
        } catch (_) {
            return [];
        }
    }

    let carrinho = ler();

    function salvar() {
        try { localStorage.setItem(CHAVE, JSON.stringify(carrinho)); } catch (_) { /* privado */ }
        desenhar();
    }

    const precoItem = i => i.precoBase + i.adicionais.reduce((s, a) => s + Number(a.preco), 0);
    const totalItens = () => carrinho.reduce((s, i) => s + precoItem(i) * i.qtd, 0);
    const unidades = () => carrinho.reduce((s, i) => s + i.qtd, 0);

    function adicionar(id, adicionais = [], qtd = 1) {
        const p = catalogo[id];
        if (!p) return;
        const extras = adicionais.map(a => ({ nome: a.nome, preco: Number(a.preco) }))
            .sort((a, b) => a.nome.localeCompare(b.nome));
        const chave = id + "|" + extras.map(a => a.nome).join("+");
        const existente = carrinho.find(i => i.chave === chave);
        if (existente) {
            existente.qtd += qtd;
        } else {
            carrinho.push({ chave, id: String(id), nome: p.nome, precoBase: Number(p.preco), adicionais: extras, qtd });
        }
        salvar();
        toast(`${p.nome} no carrinho!`);
        const contador = $("cart-count");
        contador.classList.remove("cf-pulo");
        void contador.offsetWidth;
        contador.classList.add("cf-pulo");
        registrarClique(id);
    }

    function mudarQtd(chave, delta) {
        const item = carrinho.find(i => i.chave === chave);
        if (!item) return;
        item.qtd += delta;
        if (item.qtd <= 0) carrinho = carrinho.filter(i => i.chave !== chave);
        salvar();
    }

    function desenhar() {
        const n = unidades();
        const total = totalItens();
        const contador = $("cart-count");
        contador.textContent = n;
        contador.classList.toggle("hidden", !n);
        contador.classList.toggle("flex", !!n);
        const barra = $("barra-carrinho");
        barra.classList.toggle("hidden", !n);
        barra.classList.toggle("flex", !!n);
        $("barra-qtd").textContent = n;
        $("barra-total").textContent = reais(total);
        $("cart-total").textContent = reais(total);

        const lista = $("cart-items");
        if (!carrinho.length) {
            lista.innerHTML = `
                <div class="text-center py-10 text-gray-400">
                    <i class="fas fa-basket-shopping text-4xl mb-3 opacity-40"></i>
                    <p class="font-bold">Seu carrinho está vazio</p>
                    <p class="text-xs mt-1">Toque no <b>+</b> dos pratos para adicionar.</p>
                </div>`;
        } else {
            lista.innerHTML = carrinho.map(i => `
                <div class="flex items-center gap-3 rounded-2xl p-3 bg-gray-50 border border-gray-100">
                    <div class="flex-1 min-w-0">
                        <p class="font-bold text-gray-800 text-sm leading-tight">${escapar(i.nome)}</p>
                        ${i.adicionais.length ? `<p class="text-[11px] text-gray-500 font-semibold mt-0.5">+ ${i.adicionais.map(a => escapar(a.nome)).join(", ")}</p>` : ""}
                        <p class="cf-preco font-black text-sm mt-1">${reais(precoItem(i) * i.qtd)}</p>
                    </div>
                    <div class="flex items-center gap-1 rounded-xl px-1 py-1 bg-white border border-gray-100">
                        <button type="button" class="w-8 h-8 rounded-lg font-black text-gray-600" data-qtd="-1" data-chave="${escapar(i.chave)}" aria-label="Menos">${i.qtd === 1 ? '<i class="fas fa-trash text-xs text-red-500"></i>' : "−"}</button>
                        <span class="w-6 text-center font-black text-gray-800 text-sm">${i.qtd}</span>
                        <button type="button" class="w-8 h-8 rounded-lg font-black text-gray-600" data-qtd="1" data-chave="${escapar(i.chave)}" aria-label="Mais">+</button>
                    </div>
                </div>`).join("");
        }
        $("cart-form").classList.toggle("hidden", !carrinho.length);
        const btn = $("btn-enviar");
        btn.disabled = !carrinho.length || !LOJA_ABERTA;
        btn.style.opacity = btn.disabled ? ".55" : "1";
        $("btn-enviar-texto").textContent = !LOJA_ABERTA
            ? `Fechado agora · ${body.dataset.lojaDetalhe}`
            : "Enviar pedido pelo WhatsApp";
    }

    /* ---------------------------------------------------- abrir/fechar */
    function abrirCarrinho() {
        $("cart-fundo").classList.remove("hidden");
        $("cart-drawer").classList.remove("translate-y-full");
        document.documentElement.style.overflow = "hidden";
    }

    function fecharCarrinho() {
        $("cart-fundo").classList.add("hidden");
        $("cart-drawer").classList.add("translate-y-full");
        document.documentElement.style.overflow = "";
    }

    /* ------------------------------------------------------ adicionais */
    let modal = { id: null, lista: [], qtd: 1 };

    async function abrirAdicionais(id) {
        const p = catalogo[id];
        modal = { id, lista: [], qtd: 1 };
        $("modal-produto-nome").textContent = p.nome;
        $("modal-qtd").textContent = "1";
        const lista = $("lista-adicionais");
        lista.innerHTML = '<p class="text-sm text-gray-400 py-4"><i class="fas fa-spinner fa-spin mr-2"></i>Carregando opções...</p>';
        const m = $("modal-adicionais");
        m.classList.remove("hidden");
        m.classList.add("flex");
        atualizarTotalModal();
        try {
            const r = await fetch(`/produto/${encodeURIComponent(id)}/adicionais/`, { headers: { Accept: "application/json" } });
            if (!r.ok) throw new Error();
            modal.lista = await r.json();
            lista.innerHTML = modal.lista.length ? modal.lista.map((a, i) => `
                <label class="flex justify-between items-center gap-3 p-3 rounded-xl cursor-pointer bg-gray-50 border border-gray-100">
                    <span class="text-sm font-bold text-gray-700">${escapar(a.nome)}
                        <span class="cf-preco text-xs font-black ml-1">+ ${reais(a.preco)}</span></span>
                    <input type="checkbox" value="${i}" class="w-5 h-5 accent-red-500">
                </label>`).join("") : '<p class="text-sm text-gray-400 py-4">Este item não tem adicionais.</p>';
        } catch (_) {
            lista.innerHTML = '<p class="text-sm text-red-500 py-4">Não foi possível carregar os adicionais. Você pode adicionar o item sem eles.</p>';
        }
    }

    function selecionados() {
        return Array.from(document.querySelectorAll("#lista-adicionais input:checked"))
            .map(c => modal.lista[Number(c.value)]).filter(Boolean);
    }

    function atualizarTotalModal() {
        const p = catalogo[modal.id];
        if (!p) return;
        const extras = selecionados().reduce((s, a) => s + Number(a.preco), 0);
        $("modal-total").textContent = reais((Number(p.preco) + extras) * modal.qtd);
    }

    function fecharAdicionais() {
        const m = $("modal-adicionais");
        m.classList.add("hidden");
        m.classList.remove("flex");
    }

    /* ---------------------------------------------------------- envio */
    function escolha(grupo) {
        const ativo = document.querySelector(`[data-grupo="${grupo}"] .cf-escolha.ativo`);
        return ativo ? ativo.dataset.valor : "";
    }

    function enviar() {
        if (!LOJA_ABERTA) {
            toast(`Estamos fechados · ${body.dataset.lojaDetalhe}`, true);
            return;
        }
        if (!carrinho.length) return;
        const nome = $("cf-nome").value.trim();
        const tipo = escolha("tipo");
        const rua = $("cf-rua").value.trim();
        const numero = $("cf-numero").value.trim();
        if (!nome) { $("cf-nome").focus(); toast("Informe seu nome.", true); return; }
        if (tipo === "entrega" && (!rua || !numero)) {
            (rua ? $("cf-numero") : $("cf-rua")).focus();
            toast("Informe rua e número para a entrega.", true);
            return;
        }
        const pagamento = escolha("pagamento");
        const troco = $("cf-troco").value.trim();
        const obs = $("cf-obs").value.trim();
        const complemento = $("cf-complemento").value.trim();

        try {
            localStorage.setItem(CHAVE_CLIENTE, JSON.stringify({ nome, rua, numero, complemento }));
        } catch (_) { /* ok */ }

        const linhas = ["*Novo pedido — Cantinho Família* ❤️", ""];
        carrinho.forEach(i => {
            linhas.push(`• *${i.qtd}x ${i.nome}* — ${reais(precoItem(i) * i.qtd)}`);
            i.adicionais.forEach(a => linhas.push(`   + ${a.nome} (${reais(a.preco)})`));
        });
        linhas.push("", `*Total dos itens: ${reais(totalItens())}*`);
        if (tipo === "entrega") linhas.push("_(+ taxa de entrega, a confirmar)_");
        linhas.push("", `👤 *Nome:* ${nome}`);
        if (tipo === "entrega") {
            linhas.push(`🛵 *Entrega:* ${rua}, ${numero}${complemento ? " — " + complemento : ""}`);
        } else {
            linhas.push("🏪 *Retirada no balcão*");
        }
        linhas.push(`💳 *Pagamento:* ${pagamento}${pagamento === "Dinheiro" && troco ? " (troco para R$ " + troco + ")" : ""}`);
        if (obs) linhas.push(`📝 *Obs.:* ${obs}`);

        const url = `https://wa.me/${WHATSAPP}?text=${encodeURIComponent(linhas.join("\n"))}`;
        const janela = window.open(url, "_blank", "noopener");
        if (!janela) window.location.href = url;
    }

    /* ----------------------------------------------------------- extras */
    let toastTimer = null;
    function toast(msg, erro) {
        const t = $("toast");
        $("toast-texto").textContent = msg;
        t.style.background = erro ? "#dc2626" : "#16a34a";
        t.querySelector("i").className = erro ? "fas fa-circle-exclamation" : "fas fa-check-circle";
        t.classList.remove("escondido");
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => t.classList.add("escondido"), 2200);
    }

    // O interesse do cliente vira o gráfico "mais clicados" das estatísticas.
    const clicados = new Set();
    function registrarClique(id) {
        if (clicados.has(id)) return;
        clicados.add(id);
        const url = `/api/clique/${encodeURIComponent(id)}/`;
        if (navigator.sendBeacon) navigator.sendBeacon(url);
        else fetch(url, { method: "POST", keepalive: true }).catch(() => {});
    }

    function filtrar(categoria) {
        document.querySelectorAll(".categoria-btn").forEach(b => b.classList.toggle("ativo", b.dataset.categoria === categoria));
        document.querySelectorAll(".cardapio-secao").forEach(s => {
            s.style.display = categoria === "all" || s.dataset.secao === categoria ? "" : "none";
        });
        window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function buscar(termo) {
        termo = termo.trim().toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
        let achou = 0;
        document.querySelectorAll(".produto-card").forEach(card => {
            const nome = (card.dataset.nome || "").normalize("NFD").replace(/[̀-ͯ]/g, "");
            const ok = !termo || nome.includes(termo) || (card.dataset.codigo || "").includes(termo);
            card.style.display = ok ? "" : "none";
            if (ok) achou++;
        });
        document.querySelectorAll(".cardapio-secao").forEach(s => {
            const visivel = Array.from(s.querySelectorAll(".produto-card")).some(c => c.style.display !== "none");
            s.style.display = visivel ? "" : "none";
        });
        $("busca-vazia").classList.toggle("hidden", !!achou || !termo);
    }

    /* ----------------------------------------------------------- eventos */
    document.addEventListener("click", e => {
        const add = e.target.closest(".btn-add");
        if (add) {
            const id = add.closest(".produto-card").dataset.id;
            if (catalogo[id] && catalogo[id].tem_adicionais) abrirAdicionais(id);
            else adicionar(id);
            return;
        }
        const qtd = e.target.closest("[data-qtd]");
        if (qtd) { mudarQtd(qtd.dataset.chave, Number(qtd.dataset.qtd)); return; }
        const cat = e.target.closest(".categoria-btn");
        if (cat) { filtrar(cat.dataset.categoria); return; }
        const op = e.target.closest(".cf-escolha");
        if (op) {
            op.parentElement.querySelectorAll(".cf-escolha").forEach(b => b.classList.toggle("ativo", b === op));
            if (op.parentElement.dataset.grupo === "tipo") $("cf-endereco").classList.toggle("hidden", op.dataset.valor !== "entrega");
            if (op.parentElement.dataset.grupo === "pagamento") $("cf-troco").classList.toggle("hidden", op.dataset.valor !== "Dinheiro");
            return;
        }
        if (e.target.id === "modal-adicionais") fecharAdicionais();
    });
    document.addEventListener("change", e => { if (e.target.closest("#lista-adicionais")) atualizarTotalModal(); });
    document.addEventListener("keydown", e => {
        if (e.key === "Escape") { fecharAdicionais(); fecharCarrinho(); }
    });
    $("busca").addEventListener("input", e => buscar(e.target.value));

    // Os dados do cliente voltam preenchidos na próxima visita.
    try {
        const c = JSON.parse(localStorage.getItem(CHAVE_CLIENTE) || "{}");
        if (c.nome) $("cf-nome").value = c.nome;
        if (c.rua) $("cf-rua").value = c.rua;
        if (c.numero) $("cf-numero").value = c.numero;
        if (c.complemento) $("cf-complemento").value = c.complemento;
    } catch (_) { /* ok */ }

    window.Cardapio = {
        abrirCarrinho,
        fecharCarrinho,
        fecharAdicionais,
        enviar,
        qtdModal(d) {
            modal.qtd = Math.max(1, Math.min(20, modal.qtd + d));
            $("modal-qtd").textContent = modal.qtd;
            atualizarTotalModal();
        },
        confirmarAdicionais() {
            adicionar(modal.id, selecionados(), modal.qtd);
            fecharAdicionais();
        }
    };

    desenhar();
})();
