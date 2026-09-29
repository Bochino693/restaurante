/* =========================================================================
   IMPRESSÃO DAS NOTINHAS — Cantinho Família
   -------------------------------------------------------------------------
   Um motor só para o Caixa e para a tela de Pedidos (antes eram duas cópias
   diferentes, com layouts e regras diferentes).

   O que ele garante:
   • FILA PERSISTENTE: o pedido entra numa fila guardada no navegador. Se o
     QZ Tray estiver fechado ou a impressora desligada, a notinha NÃO se
     perde: fica na fila e sai sozinha quando a conexão voltar — mesmo que
     a página seja recarregada.
   • RECONEXÃO: se o QZ Tray reiniciar, a próxima impressão reconecta.
   • IMPRESSORA ESCOLHIDA: nome e largura do papel (80 mm = 48 colunas,
     58 mm = 32 colunas) ficam salvos; sem escolha, procura a térmica.
   • LAYOUT: cabeçalho da casa, colunas alinhadas, total em destaque, via
     da cozinha com itens grandes e a observação chamando atenção.
   ========================================================================= */
(function () {
    "use strict";

    if (window.Impressora) return;

    const QZ_URL = "https://cdn.jsdelivr.net/npm/qz-tray@2.2.4/qz-tray.js";
    const CHAVE_CONFIG = "cf_impressora_config";
    const CHAVE_FILA = "cf_fila_impressao";
    const CHAVE_ULTIMO = "pdv_ultimo_pedido_impressao";
    const PREFERIDAS = ["MP-4200", "BEMATECH", "ELGIN", "EPSON", "DARUMA", "POS", "THERMAL", "TERMICA"];
    const TENTATIVAS = 3;
    const INTERVALO_VIAS_MS = 700;
    const REPROCESSAR_MS = 15000;

    const ESC = {
        INIT: "\x1B\x40",
        CP850: "\x1B\x74\x02",
        CENTRO: "\x1B\x61\x01",
        ESQUERDA: "\x1B\x61\x00",
        NEGRITO: "\x1B\x45\x01",
        NORMAL: "\x1B\x45\x00",
        GRANDE: "\x1D\x21\x11",        // largura e altura dobradas
        ALTO: "\x1D\x21\x01",          // só a altura dobrada (mantém as colunas)
        TAMANHO_NORMAL: "\x1D\x21\x00",
        CORTE: "\x1D\x56\x42\x03",     // avança 3 linhas e corta
        AVANCO: "\n\n\n"
    };

    /* ------------------------------------------------------------ estado */
    let qzCarregando = null;
    let conectando = null;
    let impressoraCache = null;
    let imprimindo = false;
    const ouvintes = new Set();

    function avisar(estado, detalhe) {
        ouvintes.forEach(fn => {
            try { fn(estado, detalhe); } catch (_) { /* ouvinte não derruba a fila */ }
        });
    }

    function lerJSON(chave, padrao) {
        try {
            const bruto = localStorage.getItem(chave);
            return bruto ? JSON.parse(bruto) : padrao;
        } catch (_) {
            return padrao;
        }
    }

    function gravarJSON(chave, valor) {
        try { localStorage.setItem(chave, JSON.stringify(valor)); } catch (_) { /* cheio/privado */ }
    }

    function config() {
        const c = lerJSON(CHAVE_CONFIG, {});
        return {
            impressora: c.impressora || "",
            colunas: Number(c.colunas) === 32 ? 32 : 48,
            vias: c.vias === "balcao" || c.vias === "cozinha" ? c.vias : "ambas"
        };
    }

    function salvarConfig(novo) {
        gravarJSON(CHAVE_CONFIG, { ...config(), ...novo });
        impressoraCache = null;
    }

    const esperar = ms => new Promise(r => setTimeout(r, ms));

    /* --------------------------------------------------------- texto */
    function limpar(txt) {
        return String(txt ?? "")
            .normalize("NFD")
            .replace(/[̀-ͯ]/g, "")
            .replace(/[^\x20-\x7E]/g, "")
            .replace(/\s+/g, " ")
            .trim();
    }

    function moeda(v) {
        return "R$ " + Number(v || 0).toFixed(2).replace(".", ",");
    }

    function quebrar(texto, largura) {
        const palavras = limpar(texto).split(" ").filter(Boolean);
        const linhas = [];
        let linha = "";
        palavras.forEach(p => {
            while (p.length > largura) {          // palavra maior que a linha
                if (linha) { linhas.push(linha); linha = ""; }
                linhas.push(p.slice(0, largura));
                p = p.slice(largura);
            }
            const teste = linha ? linha + " " + p : p;
            if (teste.length > largura && linha) {
                linhas.push(linha);
                linha = p;
            } else {
                linha = teste;
            }
        });
        if (linha) linhas.push(linha);
        return linhas;
    }

    /** "esquerda ........ direita" ocupando a linha inteira. */
    function linhaValor(esq, dir, largura) {
        // O recuo do começo (adicionais "   + 1x ...") é mantido.
        const recuo = (String(esq).match(/^ */) || [""])[0];
        esq = recuo + limpar(esq);
        dir = limpar(dir);
        const espaco = largura - dir.length - 1;
        if (esq.length > espaco) {
            const partes = quebrar(esq, espaco);
            const ultima = partes.pop();
            return partes.map(p => p + "\n").join("") +
                ultima.padEnd(espaco) + " " + dir + "\n";
        }
        return esq.padEnd(espaco) + " " + dir + "\n";
    }

    function centro(txt, largura) {
        return quebrar(txt, largura).map(l => {
            const sobra = Math.max(0, largura - l.length);
            return " ".repeat(Math.floor(sobra / 2)) + l;
        }).join("\n") + "\n";
    }

    const traco = (c, n) => c.repeat(n) + "\n";

    /* ---------------------------------------------------- normalização */
    function normalizarItens(itens) {
        if (!Array.isArray(itens)) return [];
        return itens.map((item, i) => {
            const adicionais = Array.isArray(item?.adicionais)
                ? item.adicionais.map(a => ({
                    nome: String(a?.nome || "").trim(),
                    qtd: Number(a?.qtd ?? a?.quantidade ?? 1),
                    preco: Number(a?.preco ?? 0)
                })).filter(a => a.nome && a.qtd > 0 && Number.isFinite(a.preco))
                : [];
            const qtd = Number(item?.qtd ?? item?.quantidade ?? 0);
            const somaAdd = adicionais.reduce((s, a) => s + a.preco * a.qtd, 0);
            let base = Number(item?.precoBase ?? item?.preco_base ?? NaN);
            const unit = Number(item?.precoUnitario ?? item?.preco_unitario ?? NaN);
            if (!Number.isFinite(base)) {
                base = Number.isFinite(unit) ? Math.max(0, unit - somaAdd) : Number(item?.preco ?? 0);
            }
            const unitario = Number.isFinite(unit) && unit > 0 ? unit : base + somaAdd;
            const subtotal = Number(item?.subtotal ?? 0) > 0 ? Number(item.subtotal) : unitario * qtd;
            return {
                id: item?.id ?? i,
                nome: String(item?.nome || item?.nome_produto || "").trim(),
                qtd,
                precoBase: base,
                precoUnitario: unitario,
                subtotal,
                adicionais
            };
        }).filter(i => i.nome && Number.isFinite(i.qtd) && i.qtd > 0);
    }

    function normalizarPedido(p = {}) {
        const end = p.endereco || {};
        const itens = normalizarItens(p.itens);
        const somaItens = itens.reduce((s, i) => s + i.subtotal, 0);
        const totalPedido = Number(p.totalPedido ?? p.total ?? somaItens);
        const taxa = Number(p.taxaMotoca ?? p.taxa_motoca ?? 0);
        const entrega = p.entrega === true || p.entrega === 1 || p.entrega === "true";
        return {
            id: p.id,
            criadoEm: p.criadoEm || p.criado_em || new Date().toLocaleString("pt-BR"),
            nomeCliente: p.nomeCliente || p.nome_cliente || "SEM NOME",
            metodo: String(p.metodo || p.forma_pagamento || ""),
            entrega,
            descricao: p.descricao || "",
            endereco: {
                rua: end.rua || p.rua || "",
                numero: end.numero || p.numero || "",
                cep: end.cep || p.cep || ""
            },
            itens,
            somaItens,
            desconto: Math.max(0, Number(p.desconto || 0) || somaItens - totalPedido),
            totalPedido,
            taxaMotoca: entrega ? taxa : 0,
            totalFinal: totalPedido + (entrega ? taxa : 0)
        };
    }

    function validar(pedido) {
        const p = normalizarPedido(pedido);
        if (!p.id) throw new Error("Pedido sem número para impressão.");
        if (!p.itens.length) throw new Error("A impressão foi bloqueada: o pedido chegou sem itens.");
        return p;
    }

    const NOMES_PAGAMENTO = { PIX: "PIX", DINHEIRO: "DINHEIRO", CARTAO: "CARTAO", MISTO: "MISTO" };
    const pagamento = m => NOMES_PAGAMENTO[limpar(m).toUpperCase()] || limpar(m).toUpperCase() || "-";

    /* ------------------------------------------------------ as notinhas */
    function cabecalho(p, via, L) {
        let t = ESC.INIT + ESC.CP850 + ESC.CENTRO;
        t += ESC.NEGRITO + ESC.GRANDE + "CANTINHO FAMILIA\n" + ESC.TAMANHO_NORMAL + ESC.NORMAL;
        t += "comida caseira com carinho\n";
        t += traco("=", L);
        t += ESC.NEGRITO + ESC.GRANDE + `PEDIDO #${p.id}\n` + ESC.TAMANHO_NORMAL + ESC.NORMAL;
        t += `${via}  |  ${limpar(p.criadoEm)}\n`;
        t += ESC.ESQUERDA + traco("-", L);
        return t;
    }

    function blocoCliente(p, L, cozinha) {
        let t = "";
        t += linhaValor("Cliente:", limpar(p.nomeCliente).toUpperCase(), L);
        t += linhaValor("Tipo:", p.entrega ? "ENTREGA" : "RETIRADA NO BALCAO", L);
        if (p.entrega && (p.endereco.rua || p.endereco.numero)) {
            quebrar(`Endereco: ${p.endereco.rua}, ${p.endereco.numero}` +
                (p.endereco.cep ? ` - CEP ${p.endereco.cep}` : ""), L)
                .forEach(l => { t += l + "\n"; });
        }
        t += linhaValor("Pagamento:", pagamento(p.metodo), L);
        if (p.descricao) {
            t += traco("-", L);
            t += ESC.NEGRITO + (cozinha ? ESC.ALTO : "") + ">> OBSERVACAO\n";
            quebrar(p.descricao.toUpperCase(), L).forEach(l => { t += l + "\n"; });
            t += ESC.TAMANHO_NORMAL + ESC.NORMAL;
        }
        return t;
    }

    function blocoTotais(p, L) {
        let t = traco("-", L);
        t += linhaValor("Subtotal", moeda(p.somaItens), L);
        if (p.desconto > 0.009) t += linhaValor("Desconto", "-" + moeda(p.desconto), L);
        if (p.taxaMotoca > 0) t += linhaValor("Taxa de entrega", moeda(p.taxaMotoca), L);
        t += ESC.NEGRITO + ESC.ALTO + linhaValor("TOTAL", moeda(p.totalFinal), L) + ESC.TAMANHO_NORMAL + ESC.NORMAL;
        t += linhaValor("Forma de pagamento", pagamento(p.metodo), L);
        return t;
    }

    function gerarBalcao(pedido) {
        const p = validar(pedido);
        const L = config().colunas;
        let t = cabecalho(p, "VIA BALCAO", L);
        t += blocoCliente(p, L, false);
        t += traco("-", L);
        t += ESC.NEGRITO + linhaValor("ITEM", "VALOR", L) + ESC.NORMAL;
        p.itens.forEach(item => {
            t += ESC.NEGRITO + linhaValor(`${item.qtd}x ${item.nome}`, moeda(item.subtotal), L) + ESC.NORMAL;
            if (item.qtd > 1 || item.adicionais.length) {
                t += `   un. ${moeda(item.precoBase)}\n`;
            }
            item.adicionais.forEach(a => {
                t += linhaValor(`   + ${a.qtd}x ${a.nome}`, moeda(a.preco * a.qtd), L);
            });
        });
        t += blocoTotais(p, L);
        t += traco("=", L);
        t += ESC.CENTRO + "Obrigado pela preferencia!\n";
        t += "Volte sempre - Cantinho Familia\n";
        t += ESC.ESQUERDA + ESC.AVANCO + ESC.CORTE;
        return t;
    }

    function gerarCozinha(pedido) {
        const p = validar(pedido);
        const L = config().colunas;
        let t = cabecalho(p, "VIA COZINHA", L);
        t += blocoCliente(p, L, true);
        t += traco("=", L);
        t += ESC.NEGRITO + "PREPARO\n" + ESC.NORMAL;
        t += traco("-", L);
        p.itens.forEach(item => {
            // item em letra alta: a cozinha lê de longe
            t += ESC.NEGRITO + ESC.ALTO;
            quebrar(`${item.qtd}x ${item.nome.toUpperCase()}`, L).forEach(l => { t += l + "\n"; });
            t += ESC.TAMANHO_NORMAL + ESC.NORMAL;
            item.adicionais.forEach(a => {
                t += `     + ${a.qtd}x ${limpar(a.nome).toUpperCase()}\n`;
            });
            t += "\n";
        });
        t += blocoTotais(p, L);
        t += traco("=", L);
        t += ESC.CENTRO + ESC.NEGRITO + "BOM TRABALHO!\n" + ESC.NORMAL;
        t += ESC.ESQUERDA + ESC.AVANCO + ESC.CORTE;
        return t;
    }

    /* ------------------------------------------------------- QZ Tray */
    function carregarQZ() {
        if (window.qz) return Promise.resolve();
        if (qzCarregando) return qzCarregando;
        qzCarregando = new Promise((resolve, reject) => {
            const s = document.createElement("script");
            s.src = QZ_URL;
            s.onload = () => (window.qz ? resolve() : reject(new Error("QZ Tray não disponível.")));
            s.onerror = () => {
                qzCarregando = null;
                reject(new Error("Não foi possível baixar o componente do QZ Tray (sem internet?)."));
            };
            document.head.appendChild(s);
        });
        return qzCarregando;
    }

    async function conectar() {
        await carregarQZ();
        if (qz.websocket.isActive()) return;
        if (conectando) return conectando;
        conectando = (async () => {
            try {
                qz.websocket.setClosedCallbacks(() => {
                    impressoraCache = null;
                    avisar("desconectado");
                });
                await qz.websocket.connect({ retries: 3, delay: 1 });
            } catch (erro) {
                throw new Error(
                    "O QZ Tray não respondeu. Abra o QZ Tray no computador do caixa " +
                    "(ícone perto do relógio) e tente de novo."
                );
            } finally {
                conectando = null;
            }
        })();
        return conectando;
    }

    async function listarImpressoras() {
        await conectar();
        let lista = await qz.printers.find();
        if (!Array.isArray(lista)) lista = lista ? [lista] : [];
        return lista.map(String);
    }

    async function escolherImpressora(forcar) {
        if (impressoraCache && !forcar) return impressoraCache;
        const lista = await listarImpressoras();
        if (!lista.length) {
            throw new Error("Nenhuma impressora encontrada. Confira se ela está ligada e conectada.");
        }
        const desejada = config().impressora;
        let achada = desejada && lista.find(n => n.toUpperCase() === desejada.toUpperCase());
        if (!achada) {
            for (const termo of PREFERIDAS) {
                achada = lista.find(n => n.toUpperCase().includes(termo));
                if (achada) break;
            }
        }
        if (!achada) {
            try {
                const padrao = await qz.printers.getDefault();
                achada = lista.find(n => n === String(padrao));
            } catch (_) { /* segue para a primeira */ }
        }
        impressoraCache = achada || lista[0];
        return impressoraCache;
    }

    async function imprimirTexto(texto) {
        let ultimoErro = null;
        for (let tentativa = 1; tentativa <= TENTATIVAS; tentativa++) {
            try {
                await conectar();
                const nome = await escolherImpressora(tentativa > 1);
                const cfg = qz.configs.create(nome, { encoding: "CP850", copies: 1, jobName: "Cantinho Familia" });
                await qz.print(cfg, [{ type: "raw", format: "plain", data: texto }]);
                return nome;
            } catch (erro) {
                ultimoErro = erro;
                impressoraCache = null;
                if (tentativa < TENTATIVAS) {
                    try {
                        if (window.qz && qz.websocket.isActive()) await qz.websocket.disconnect();
                    } catch (_) { /* já caiu */ }
                    await esperar(800 * tentativa);
                }
            }
        }
        throw ultimoErro || new Error("Falha ao imprimir.");
    }

    async function imprimirAgora(pedido, vias) {
        const p = validar(pedido);
        const escolha = vias || config().vias;
        const textos = [];
        if (escolha !== "cozinha") textos.push(gerarBalcao(p));
        if (escolha !== "balcao") textos.push(gerarCozinha(p));
        for (let i = 0; i < textos.length; i++) {
            await imprimirTexto(textos[i]);
            if (i < textos.length - 1) await esperar(INTERVALO_VIAS_MS);
        }
    }

    /* ---------------------------------------------- confirmação no servidor */
    function cookie(nome) {
        const achado = document.cookie.split(";").map(c => c.trim()).find(c => c.startsWith(nome + "="));
        return achado ? decodeURIComponent(achado.slice(nome.length + 1)) : "";
    }

    async function confirmarNoServidor(id) {
        try {
            const r = await fetch(`/api/confirmar-impressao/${encodeURIComponent(id)}/`, {
                method: "POST",
                credentials: "same-origin",
                headers: { "X-CSRFToken": cookie("csrftoken"), "X-Requested-With": "XMLHttpRequest" }
            });
            return r.ok;
        } catch (_) {
            return false;
        }
    }

    /* ---------------------------------------------------------- a fila */
    function fila() { return lerJSON(CHAVE_FILA, []); }
    function gravarFila(f) { gravarJSON(CHAVE_FILA, f); }

    /**
     * Enfileira e imprime. `opcoes`: { vias, confirmar (padrão true),
     * onOk(), onErro(erro) }. O pedido fica guardado até sair na impressora.
     */
    function imprimir(pedido, opcoes = {}) {
        let p;
        try {
            p = validar(JSON.parse(JSON.stringify(pedido)));
        } catch (erro) {
            avisar("erro", erro.message);
            opcoes.onErro && opcoes.onErro(erro);
            return false;
        }
        try { sessionStorage.setItem(CHAVE_ULTIMO, JSON.stringify(p)); } catch (_) { /* ok */ }
        const f = fila().filter(j => j.pedido.id !== p.id || j.vias !== (opcoes.vias || null));
        const job = { chave: `${p.id}-${Date.now()}`, pedido: p, vias: opcoes.vias || null,
                      confirmar: opcoes.confirmar !== false, tentativas: 0 };
        f.push(job);
        gravarFila(f);
        callbacks.set(job.chave, opcoes);
        processar();
        return true;
    }

    const callbacks = new Map();

    async function processar() {
        if (imprimindo) return;
        let f = fila();
        if (!f.length) return;
        imprimindo = true;
        avisar("imprimindo", f.length);
        let erroFinal = null;
        try {
            while (f.length) {
                const job = f[0];
                const cb = callbacks.get(job.chave) || {};
                try {
                    await imprimirAgora(job.pedido, job.vias);
                    f = fila().filter(j => j.chave !== job.chave);
                    gravarFila(f);
                    callbacks.delete(job.chave);
                    if (job.confirmar) confirmarNoServidor(job.pedido.id);
                    cb.onOk && cb.onOk();
                } catch (erro) {
                    erroFinal = erro;
                    job.tentativas = (job.tentativas || 0) + 1;
                    const atual = fila();
                    const idx = atual.findIndex(j => j.chave === job.chave);
                    if (idx >= 0) { atual[idx].tentativas = job.tentativas; gravarFila(atual); }
                    cb.onErro && cb.onErro(erro);
                    callbacks.delete(job.chave);
                    break;   // a impressora não está respondendo: tenta a fila de novo depois
                }
            }
        } finally {
            imprimindo = false;
            const restantes = fila().length;
            if (erroFinal) avisar("erro", erroFinal.message, restantes);
            else avisar(restantes ? "imprimindo" : "ok", restantes);
        }
    }

    // Quem ficou na fila (QZ fechado, sem papel...) tenta de novo sozinho.
    setInterval(() => { if (fila().length) processar(); }, REPROCESSAR_MS);
    window.addEventListener("online", () => processar());
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible" && fila().length) processar();
    });

    function ultimoPedido() {
        try {
            const bruto = sessionStorage.getItem(CHAVE_ULTIMO);
            return bruto ? validar(JSON.parse(bruto)) : null;
        } catch (_) {
            return null;
        }
    }

    async function testar() {
        const L = config().colunas;
        let t = ESC.INIT + ESC.CP850 + ESC.CENTRO + ESC.NEGRITO + ESC.GRANDE + "CANTINHO FAMILIA\n";
        t += ESC.TAMANHO_NORMAL + ESC.NORMAL + "Teste de impressao\n" + traco("=", L) + ESC.ESQUERDA;
        t += linhaValor("Largura", `${L} colunas`, L);
        t += linhaValor("Data", new Date().toLocaleString("pt-BR"), L);
        t += "1234567890".repeat(Math.ceil(L / 10)).slice(0, L) + "\n";
        t += traco("-", L) + ESC.CENTRO + "Se a linha acima coube inteira,\na largura esta certa.\n";
        t += ESC.ESQUERDA + ESC.AVANCO + ESC.CORTE;
        return imprimirTexto(t);
    }

    /* ------------------------------------- janela de configuração */
    async function abrirConfiguracao() {
        document.getElementById("cf-config-impressora")?.remove();
        const c = config();
        const fundo = document.createElement("div");
        fundo.id = "cf-config-impressora";
        fundo.className = "fixed inset-0 z-[200] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm";
        fundo.innerHTML = `
            <div class="w-full max-w-md rounded-[2rem] shadow-2xl p-6 space-y-4" style="background: var(--cf-superficie, #fff); color: var(--cf-texto, #111)">
                <div class="flex items-center justify-between">
                    <h3 class="text-lg font-black flex items-center gap-2"><i class="fas fa-print" style="color: var(--cf-marca, #e3353d)"></i> Impressora</h3>
                    <button type="button" data-fechar class="w-9 h-9 rounded-full bg-gray-100 text-gray-500" aria-label="Fechar"><i class="fas fa-times"></i></button>
                </div>
                <label class="block">
                    <span class="block text-[11px] font-black uppercase tracking-wide text-gray-500 mb-1">Impressora</span>
                    <select data-campo="impressora" class="w-full border rounded-xl px-3 py-2.5 text-sm font-semibold">
                        <option value="">Procurando impressoras...</option>
                    </select>
                </label>
                <div class="grid grid-cols-2 gap-3">
                    <label class="block">
                        <span class="block text-[11px] font-black uppercase tracking-wide text-gray-500 mb-1">Papel</span>
                        <select data-campo="colunas" class="w-full border rounded-xl px-3 py-2.5 text-sm font-semibold">
                            <option value="48" ${c.colunas === 48 ? "selected" : ""}>80 mm (48 colunas)</option>
                            <option value="32" ${c.colunas === 32 ? "selected" : ""}>58 mm (32 colunas)</option>
                        </select>
                    </label>
                    <label class="block">
                        <span class="block text-[11px] font-black uppercase tracking-wide text-gray-500 mb-1">Vias</span>
                        <select data-campo="vias" class="w-full border rounded-xl px-3 py-2.5 text-sm font-semibold">
                            <option value="ambas" ${c.vias === "ambas" ? "selected" : ""}>Balcão + cozinha</option>
                            <option value="balcao" ${c.vias === "balcao" ? "selected" : ""}>Só balcão</option>
                            <option value="cozinha" ${c.vias === "cozinha" ? "selected" : ""}>Só cozinha</option>
                        </select>
                    </label>
                </div>
                <p data-status class="text-xs font-semibold text-gray-500 min-h-[1rem]"></p>
                <div class="grid grid-cols-2 gap-2">
                    <button type="button" data-teste class="rounded-xl py-3 font-black text-sm bg-gray-100 text-gray-700">Imprimir teste</button>
                    <button type="button" data-salvar class="rounded-xl py-3 font-black text-sm text-white" style="background: var(--cf-marca, #e3353d)">Salvar</button>
                </div>
                <p class="text-[11px] text-gray-400">Pendentes na fila: <b>${fila().length}</b>. As notinhas pendentes saem sozinhas quando a impressora responde.</p>
            </div>`;
        document.body.appendChild(fundo);
        const sel = fundo.querySelector('[data-campo="impressora"]');
        const status = fundo.querySelector("[data-status]");
        const fechar = () => fundo.remove();
        fundo.addEventListener("click", e => { if (e.target === fundo || e.target.closest("[data-fechar]")) fechar(); });
        const valores = () => ({
            impressora: sel.value,
            colunas: Number(fundo.querySelector('[data-campo="colunas"]').value),
            vias: fundo.querySelector('[data-campo="vias"]').value
        });
        fundo.querySelector("[data-salvar]").addEventListener("click", () => {
            salvarConfig(valores());
            fechar();
            processar();
        });
        fundo.querySelector("[data-teste]").addEventListener("click", async () => {
            salvarConfig(valores());
            status.textContent = "Imprimindo teste...";
            try {
                const nome = await testar();
                status.textContent = `Teste enviado para ${nome}.`;
            } catch (erro) {
                status.textContent = erro.message;
            }
        });
        try {
            const lista = await listarImpressoras();
            const atual = c.impressora || (await escolherImpressora().catch(() => ""));
            sel.innerHTML = '<option value="">Automática (procura a térmica)</option>' +
                lista.map(n => `<option ${n === atual ? "selected" : ""}>${n.replace(/</g, "&lt;")}</option>`).join("");
            status.textContent = `${lista.length} impressora(s) encontrada(s).`;
        } catch (erro) {
            sel.innerHTML = '<option value="">QZ Tray não conectado</option>';
            status.textContent = erro.message;
        }
    }

    window.Impressora = {
        abrirConfiguracao,
        imprimir,
        imprimirAgora,
        processar,
        conectar,
        listarImpressoras,
        escolherImpressora,
        testar,
        config,
        salvarConfig,
        ultimoPedido,
        pendentes: () => fila().length,
        gerarBalcao,
        gerarCozinha,
        normalizarItens,
        normalizarPedido,
        validar,
        aoMudar: fn => { ouvintes.add(fn); return () => ouvintes.delete(fn); }
    };

    // Ao abrir a página, o que ficou pendente de antes sai primeiro.
    if (fila().length) setTimeout(processar, 1500);
})();
