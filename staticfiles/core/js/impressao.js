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
   • LAYOUT: o da versão eb7bb7f (VIA BALCAO / VIA COZINHA em letra
     grande, 32 colunas), com o telefone do cliente na via do balcão.
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
            telefone: String(p.telefone || end.telefone || "").trim(),
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
    /* LAYOUT DA VERSÃO eb7bb7f (o que a equipe já conhece), linha a linha:
       cabeçalho grande "VIA BALCAO / PEDIDO #", 32 colunas, itens iguais
       agrupados. Única diferença: o telefone do cliente na via do balcão. */
    const LINHA = "--------------------------------\n";
    const CORTE_PARCIAL = "\x1D\x56\x01";
    const CORTE_TOTAL = "\x1D\x56\x00";
    const valor = v => Number(v || 0).toFixed(2).replace(".", ",");
    const somaAdicionais = ads => (ads || []).reduce((s, a) => s + Number(a.preco || 0) * Number(a.qtd || 1), 0);

    /** Itens iguais (mesmo nome e mesmos adicionais) numa linha só. */
    function agrupar(itens) {
        const mapa = {};
        itens.forEach(item => {
            const chave = item.nome + "_" + item.adicionais
                .map(a => `${a.nome}:${a.preco}:${a.qtd || 1}`).sort().join("|");
            if (!mapa[chave]) {
                mapa[chave] = { nome: item.nome, qtd: item.qtd, precoBase: item.precoBase,
                    subtotal: item.subtotal, adicionais: item.adicionais };
            } else {
                mapa[chave].qtd += item.qtd;
                mapa[chave].subtotal += item.subtotal;
            }
        });
        return Object.values(mapa);
    }

    function subtotalDe(item) {
        if (Number(item.subtotal) > 0) return Number(item.subtotal);
        return (Number(item.precoBase || 0) + somaAdicionais(item.adicionais)) * Number(item.qtd || 0);
    }

    function gerarBalcao(pedido) {
        const p = validar(pedido);
        const itens = agrupar(p.itens);
        let t = "";
        t += ESC.INIT + ESC.CP850 + ESC.CENTRO + ESC.NEGRITO + ESC.GRANDE;
        t += "VIA BALCAO\n";
        t += `PEDIDO #${p.id}\n`;
        t += ESC.TAMANHO_NORMAL + ESC.NORMAL;
        t += LINHA;
        t += ESC.ESQUERDA;
        t += `Data: ${limpar(p.criadoEm)}\n`;
        t += `Cliente: ${limpar(p.nomeCliente)}\n`;
        if (p.telefone) t += `Tel: ${limpar(p.telefone)}\n`;
        if (p.descricao) t += `Obs: ${limpar(p.descricao)}\n`;
        t += `Pagamento: ${limpar(p.metodo).toUpperCase()}\n`;
        t += p.entrega ? "Tipo: ENTREGA\n" : "Tipo: RETIRADA BALCAO\n";
        if (p.entrega && p.endereco) {
            t += `End: ${limpar(p.endereco.rua)}, ${limpar(p.endereco.numero)}\n`;
            if (p.endereco.cep) t += `CEP: ${limpar(p.endereco.cep)}\n`;
        }
        t += LINHA;
        t += ESC.NEGRITO + "ITENS DO PEDIDO\n" + ESC.NORMAL;
        t += LINHA;

        let subtotalGeral = 0;
        itens.forEach(item => {
            const qtd = Number(item.qtd || 1);
            const sub = subtotalDe(item);
            subtotalGeral += sub;
            quebrar(`${qtd}x ${item.nome}`, 22).forEach((linha, i) => {
                t += i === 0 ? linha.padEnd(22).slice(0, 22) + ` R$ ${valor(sub)}\n` : `${linha}\n`;
            });
            t += `   Unit: R$ ${valor(qtd > 0 ? sub / qtd : 0)}\n`;
            item.adicionais.forEach(a => {
                const q = Number(a.qtd || 1);
                t += `   + ${q}x ${limpar(a.nome)} R$ ${valor(Number(a.preco || 0) * q)}\n`;
            });
            t += "\n";
        });

        const totalPedido = Number.isFinite(p.totalPedido) ? Math.max(0, p.totalPedido) : subtotalGeral;
        const desconto = Math.max(0, Number(p.desconto || 0) || (subtotalGeral - totalPedido));
        const taxa = p.entrega ? Number(p.taxaMotoca || 0) : 0;

        t += LINHA;
        t += `Subtotal:     R$ ${valor(subtotalGeral)}\n`;
        if (desconto > 0.009) t += `Desconto:     R$ ${valor(desconto)}\n`;
        if (taxa > 0) t += `Taxa Entrega: R$ ${valor(taxa)}\n`;
        t += ESC.NEGRITO + `TOTAL FINAL:  R$ ${valor(totalPedido + taxa)}\n` + ESC.NORMAL;
        t += LINHA;
        t += ESC.NEGRITO + `PAGAMENTO:    ${limpar(p.metodo).toUpperCase()}\n` + ESC.NORMAL;
        t += "\n\n\n\n\n\n";
        t += CORTE_PARCIAL;
        return t;
    }

    function gerarCozinha(pedido) {
        const p = validar(pedido);
        const itens = agrupar(p.itens);
        let t = "";
        t += ESC.INIT + ESC.CP850 + ESC.CENTRO + ESC.NEGRITO + ESC.GRANDE;
        t += "VIA COZINHA\n";
        t += `PEDIDO #${p.id}\n`;
        t += ESC.TAMANHO_NORMAL + ESC.NORMAL;
        t += LINHA;
        t += ESC.ESQUERDA;
        t += `Cliente: ${limpar(p.nomeCliente).toUpperCase()}\n`;
        t += p.entrega ? "ENTREGA\n" : "RETIRADA BALCAO\n";
        t += `PAGAMENTO: ${limpar(p.metodo).toUpperCase()}\n`;
        if (p.descricao) t += `OBS: ${limpar(p.descricao).toUpperCase()}\n`;
        t += LINHA;
        t += ESC.NEGRITO + "PREPARO\n" + ESC.NORMAL;
        t += LINHA;

        let totalProdutos = 0;
        itens.forEach(item => {
            const qtd = Number(item.qtd || 1);
            const unitCompleto = Number(item.precoBase || 0) + somaAdicionais(item.adicionais);
            const sub = subtotalDe(item);
            totalProdutos += sub;
            t += ESC.NEGRITO;
            t += `${qtd}x ${limpar(item.nome).toUpperCase()}  R$ ${valor(sub)}\n`;
            t += ESC.NORMAL;
            t += `   UNIT: R$ ${valor(qtd > 0 ? sub / qtd : unitCompleto)}\n`;
            item.adicionais.forEach(a => {
                const q = Number(a.qtd || 1);
                const total = Number(a.preco || 0) * q;
                t += `   + ${q}x ${limpar(a.nome).toUpperCase()}`;
                if (total > 0) t += ` R$ ${valor(total)}`;
                t += "\n";
            });
            t += "\n";
        });

        const totalPedido = Number(p.totalPedido || 0);
        const taxa = p.entrega ? Number(p.taxaMotoca || 0) : 0;
        const desconto = Math.max(0, Number(p.desconto || 0) || (totalProdutos - totalPedido));

        t += LINHA;
        t += ESC.NEGRITO + `TOTAL PRODUTOS: R$ ${valor(totalProdutos)}\n` + ESC.NORMAL;
        if (desconto > 0.009) {
            t += `DESCONTO:       R$ ${valor(desconto)}\n`;
            t += `APOS DESCONTO:  R$ ${valor(totalPedido)}\n`;
        }
        if (taxa > 0) t += `TAXA ENTREGA:   R$ ${valor(taxa)}\n`;
        t += ESC.NEGRITO + `TOTAL FINAL:    R$ ${valor(totalPedido + taxa)}\n` + ESC.NORMAL;
        t += LINHA;
        t += ESC.CENTRO;
        t += ESC.NEGRITO + "BOM TRABALHO!\n" + ESC.NORMAL;
        t += "\n\n\n\n\n\n";
        t += CORTE_TOTAL;
        return t;
    }

    /* ------------------------------------------------------- QZ Tray */
    /* SEGURANÇA (certificado próprio + assinatura feita no servidor): é o que
       permite ao QZ LEMBRAR a permissão do site. Mas ela NUNCA pode impedir a
       impressão: se o servidor não tem certificado, ou se uma assinatura
       falhar, a chamada segue sem assinatura (o QZ pergunta "Permitir?") e a
       notinha sai. Antes, uma assinatura recusada cancelava até a lista de
       impressoras ("Failed to sign request") e nada mais funcionava. */
    let seguranca = null;          // promessa de { modo, detalhe }
    let falhaAssinatura = "";      // última falha de assinatura (diagnóstico)
    let avisoImpressora = "";      // impressora salva que sumiu (diagnóstico)

    function configurarSeguranca() {
        if (seguranca) return seguranca;
        seguranca = fetch("/api/qz/certificado/", { credentials: "same-origin", cache: "no-store" })
            .then(r => r.ok ? r.text() : Promise.reject(new Error(`o servidor respondeu ${r.status}`)))
            .then(pem => {
                if (!/BEGIN CERTIFICATE/.test(pem)) throw new Error("a resposta não é um certificado");
                try { qz.security.setSignatureAlgorithm("SHA512"); } catch (_) { /* QZ antigo */ }
                qz.security.setCertificatePromise(resolve => resolve(pem));
                qz.security.setSignaturePromise(paraAssinar => resolve => {
                    fetch("/api/qz/assinar/", {
                        method: "POST",
                        credentials: "same-origin",
                        headers: { "X-CSRFToken": cookie("csrftoken"), "Content-Type": "text/plain" },
                        body: paraAssinar
                    })
                        .then(r => r.ok ? r.text() : Promise.reject(new Error(`assinatura recusada (${r.status})`)))
                        .then(assinatura => { falhaAssinatura = ""; resolve(assinatura); })
                        .catch(erro => {
                            falhaAssinatura = erro.message;
                            console.warn("QZ: seguindo sem assinatura:", erro.message);
                            resolve("");   // sem assinatura o QZ pergunta, mas imprime
                        });
                });
                return { modo: "assinado" };
            })
            .catch(erro => {
                console.warn("QZ: sem certificado do site, o QZ vai pedir permissão:", erro.message);
                return { modo: "sem-certificado", detalhe: erro.message };
            });
        return seguranca;
    }

    function carregarQZ() {
        if (window.qz) return configurarSeguranca().then(() => undefined);
        if (qzCarregando) return qzCarregando;
        qzCarregando = new Promise((resolve, reject) => {
            const s = document.createElement("script");
            s.src = QZ_URL;
            s.onload = () => {
                if (!window.qz) return reject(new Error("QZ Tray não disponível."));
                configurarSeguranca().then(() => resolve());
            };
            s.onerror = () => {
                qzCarregando = null;
                reject(new Error("Não foi possível baixar o componente do QZ Tray (sem internet?)."));
            };
            document.head.appendChild(s);
        });
        return qzCarregando;
    }

    /* Desde o Chrome 147 (abril de 2026), um site só fala com o QZ Tray
       (localhost) com a permissão "Acesso à rede local" / "Apps neste
       dispositivo". Ignorar o aviso 3 vezes conta como "Bloquear". */
    async function estadoRedeLocal() {
        if (!navigator.permissions || !navigator.permissions.query) return "sem-suporte";
        for (const nome of ["loopback-network", "local-network-access"]) {
            try { return (await navigator.permissions.query({ name: nome })).state; } catch (_) { /* nome não existe neste navegador */ }
        }
        return "sem-suporte";
    }

    const COMO_LIBERAR_CHROME =
        "No Chrome, clique no ícone à esquerda do endereço do site → Configurações do site → " +
        "\"Acesso à rede local\" (ou \"Apps neste dispositivo\") → Permitir. Depois recarregue a página.";
    const COMO_DESBLOQUEAR_QZ =
        "No ícone do QZ Tray (perto do relógio): Advanced → Site Manager → aba Blocked → " +
        "selecione o site → Remove. Depois recarregue a página e clique em Allow.";

    function erroCom(codigo, mensagem) {
        const e = new Error(mensagem);
        e.codigo = codigo;
        return e;
    }

    /* Transforma o erro do QZ/Chrome numa instrução do que fazer. */
    async function explicar(erro, conectando) {
        const texto = String((erro && erro.message) || erro || "");
        if (/block/i.test(texto)) {
            return erroCom("qz-bloqueado", "O QZ Tray bloqueou este site. " + COMO_DESBLOQUEAR_QZ);
        }
        if (/printer.*(not|could not).*found|cannot find printer|no printer/i.test(texto)) {
            return erroCom("impressora-sumiu",
                "A impressora escolhida não existe mais neste computador. Abra a engrenagem da impressora e escolha a certa.");
        }
        if (!conectando) return erro instanceof Error ? erro : new Error(texto || "Falha ao imprimir.");
        const permissao = await estadoRedeLocal();
        if (permissao === "denied") {
            return erroCom("chrome-bloqueou", "O Chrome bloqueou o acesso ao QZ Tray. " + COMO_LIBERAR_CHROME);
        }
        if (permissao === "prompt") {
            return erroCom("chrome-perguntando",
                "O Chrome está pedindo permissão para o site acessar este computador: clique em Permitir no aviso " +
                "perto da barra de endereço (se ele sumiu, recarregue a página).");
        }
        return erroCom("qz-fechado",
            "O QZ Tray não respondeu. Abra o QZ Tray no computador do caixa (ícone perto do relógio) e tente de novo. " +
            "Se ele já estiver aberto, confira a permissão de rede local do Chrome: " + COMO_LIBERAR_CHROME);
    }

    async function conectar() {
        await carregarQZ();
        if (qz.websocket.isActive()) return;
        if (conectando) return conectando;
        conectando = (async () => {
            try {
                // Se o certificado não veio antes (rede, deploy), tenta de novo.
                if ((await seguranca).modo !== "assinado") { seguranca = null; await configurarSeguranca(); }
                qz.websocket.setClosedCallbacks(() => {
                    impressoraCache = null;
                    avisar("desconectado");
                });
                await qz.websocket.connect({ retries: 3, delay: 1 });
            } catch (erro) {
                throw await explicar(erro, true);
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
        avisoImpressora = "";
        if (desejada && !achada) {
            // Ex.: depois de trocar a placa-mãe o Windows recria a impressora
            // como "MP-4200 TH (Cópia 1)" e a antiga fica offline.
            avisoImpressora = `A impressora salva "${desejada}" não existe mais neste computador.`;
        }
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
        if (avisoImpressora) avisoImpressora += ` Usando "${impressoraCache}". Escolha a certa na engrenagem.`;
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
        throw await explicar(ultimoErro || new Error("Falha ao imprimir."), false);
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

    /* ------------------------------------------------- diagnóstico
       Confere, na ordem, tudo o que precisa estar certo para a notinha sair
       e diz o que fazer em cada item que falhar. */
    const STATUS_IMPRESSORA = {
        OK: "pronta", OFFLINE: "OFFLINE (desligada ou desconectada)", NOT_AVAILABLE: "indisponível",
        PAPER_OUT: "sem papel", PAPER_JAM: "papel enroscado", PAUSED: "pausada no Windows",
        ERROR: "com erro", DOOR_OPEN: "tampa aberta", BUSY: "ocupada", PRINTING: "imprimindo",
        PENDING_DELETION: "sendo removida", USER_INTERVENTION: "precisa de atenção", TONER_LOW: "pouca tinta"
    };

    async function detalhesImpressoras() {
        await conectar();
        try {
            const d = await qz.printers.details();
            if (Array.isArray(d) && d.length) {
                return d.map(x => ({ nome: String(x.name), driver: x.driver || "", porta: x.connection || "", padrao: !!x.default }));
            }
        } catch (_) { /* QZ antigo: só os nomes */ }
        return (await listarImpressoras()).map(nome => ({ nome, driver: "", porta: "", padrao: false }));
    }

    async function statusImpressoras(nomes) {
        const status = {};
        if (!nomes.length || !qz.printers.startListening) return status;
        try {
            qz.printers.setPrinterCallbacks(evento => {
                [].concat(evento || []).forEach(e => {
                    if (!e || !e.printerName || (e.eventType && e.eventType !== "PRINTER")) return;
                    const codigo = String(e.statusText || e.statusCode || "").toUpperCase();
                    // guarda o pior estado de cada impressora
                    if (!status[e.printerName] || codigo !== "OK") {
                        status[e.printerName] = STATUS_IMPRESSORA[codigo] || codigo.toLowerCase();
                    }
                });
            });
            await qz.printers.startListening(nomes);
            await qz.printers.getStatus();
            await esperar(1500);
        } catch (_) {
            /* sem status neste QZ/sistema: segue só com a lista */
        } finally {
            try { await qz.printers.stopListening(); } catch (_) { /* ok */ }
            try { qz.printers.setPrinterCallbacks(() => {}); } catch (_) { /* ok */ }
        }
        return status;
    }

    async function diagnosticar() {
        const passos = [];
        const passo = (ok, titulo, detalhe, dica) => passos.push({ ok, titulo, detalhe: detalhe || "", dica: dica || "" });
        const data = iso => iso ? iso.split("-").reverse().join("/") : "";

        const rede = await estadoRedeLocal();
        let conectado = false;
        let erroConexao = null;
        try {
            await conectar();
            conectado = true;
        } catch (erro) {
            erroConexao = erro;
        }

        // 1. Chrome
        if (rede === "denied") passo(false, "Chrome: acesso à rede local", "Bloqueado para este site.", COMO_LIBERAR_CHROME);
        else if (rede === "granted") passo(true, "Chrome: acesso à rede local", "Permitido para este site.");
        else if (conectado) passo(true, "Chrome: acesso à rede local", "Liberado: o site conversa com o QZ Tray.");
        else if (rede === "prompt") passo(null, "Chrome: acesso à rede local", "Ainda não foi respondido.", "Quando o Chrome perguntar se o site pode acessar este computador, clique em Permitir.");
        else passo(true, "Chrome: acesso à rede local", "Este navegador não pede essa permissão.");

        // 2. Servidor
        try {
            const r = await fetch("/api/qz/diagnostico/", { credentials: "same-origin", cache: "no-store" });
            if (!r.ok) throw new Error(`o servidor respondeu ${r.status}`);
            const srv = await r.json();
            const certo = srv.certificado === "ok" && srv.chave === "ok" && srv.par_confere === true && !srv.vencido;
            passo(certo, "Servidor: certificado e chave do QZ",
                certo ? `Certificado "${srv.titular}", válido até ${data(srv.valido_ate)}. A chave confere com ele.`
                      : "Com problema: o QZ não vai lembrar a permissão.",
                certo ? `Impressão digital SHA-1: ${srv.sha1}` : (srv.problemas || []).join(" "));
        } catch (erro) {
            passo(false, "Servidor: certificado e chave do QZ", "Não consegui consultar (" + erro.message + ").",
                "Confira se está logado e recarregue a página.");
        }

        // 3. QZ Tray
        if (conectado) {
            let versao = "";
            try { versao = await qz.api.getVersion(); } catch (_) { /* ok */ }
            passo(true, "QZ Tray", `Conectado${versao ? " (versão " + versao + ")" : ""}.`);
        } else {
            passo(false, "QZ Tray", "Não conectou.", erroConexao.message);
        }

        // 4. Permissão lembrada
        const seg = seguranca ? await seguranca : null;
        if (seg && seg.modo === "assinado") {
            passo(!falhaAssinatura, "Permissão lembrada (certificado do site)",
                falhaAssinatura ? "A última assinatura falhou: " + falhaAssinatura + ". O QZ vai perguntar, mas imprime."
                                : "O site se identifica para o QZ. Marcando \"Remember this decision\" + Allow, ele não pergunta mais.",
                falhaAssinatura ? "Recarregue a página (o login pode ter expirado). Se continuar, veja o item do servidor."
                                : "Se o QZ continuar perguntando toda hora, o certificado instalado nele (override.crt) não é o deste site: baixe o certificado abaixo, instale e reinicie o QZ.");
        } else {
            passo(null, "Permissão lembrada (certificado do site)",
                "O site está sem certificado: o QZ pergunta \"Permitir?\" a cada conexão (mas imprime).",
                seg && seg.detalhe ? "Detalhe: " + seg.detalhe + "." : "");
        }

        // 5. Impressoras
        if (conectado) {
            try {
                const lista = await detalhesImpressoras();
                if (!lista.length) {
                    passo(false, "Impressoras", "Nenhuma impressora no Windows.",
                        "Instale o driver da Bematech MP-4200 TH e confira se ela aparece em Configurações → Bluetooth e dispositivos → Impressoras e scanners.");
                } else {
                    const status = await statusImpressoras(lista.map(d => d.nome));
                    const usada = await escolherImpressora(true).catch(() => "");
                    const copias = lista.filter(d => /\((c[oó]pia|copy)\b/i.test(d.nome));
                    const ruim = /offline|indispon|erro|sem papel|enroscado|pausada|tampa|aten/i.test(status[usada] || "");
                    passo(ruim || avisoImpressora ? false : (copias.length ? null : true), "Impressoras",
                        lista.map(d => [d.nome, d.driver, d.porta, status[d.nome], d.padrao ? "padrão do Windows" : "", d.nome === usada ? "← usada nas notinhas" : ""]
                            .filter(Boolean).join(" · ")).join("\n"),
                        [avisoImpressora,
                         ruim ? `A impressora usada está ${status[usada]}: ligue/reconecte ou escolha outra acima.` : "",
                         copias.length ? "Há impressoras \"(Cópia)\": depois de trocar a placa-mãe o Windows cria uma nova e a antiga para de funcionar. Imprima o teste em cada uma, fique com a que imprime e remova a outra no Windows." : ""]
                            .filter(Boolean).join(" "));
                }
            } catch (erro) {
                passo(false, "Impressoras", "Não consegui listar.", (await explicar(erro, false)).message);
            }
        }

        // 6. Fila
        const pendentes = fila().length;
        passo(pendentes ? null : true, "Fila de notinhas",
            pendentes ? `${pendentes} esperando a impressora.` : "Nenhuma pendente.",
            pendentes ? "Elas saem sozinhas assim que a impressora responder." : "");
        return passos;
    }

    function escaparHtml(t) {
        return String(t ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }

    async function mostrarDiagnostico(lista) {
        lista.innerHTML = '<li class="text-gray-400 font-semibold">Verificando...</li>';
        const passos = await diagnosticar();
        const icone = ok => ok === true
            ? '<span class="w-5 h-5 shrink-0 rounded-full grid place-items-center text-[10px] text-white" style="background:#16a34a"><i class="fas fa-check"></i></span>'
            : ok === false
                ? '<span class="w-5 h-5 shrink-0 rounded-full grid place-items-center text-[10px] text-white" style="background:#dc2626"><i class="fas fa-xmark"></i></span>'
                : '<span class="w-5 h-5 shrink-0 rounded-full grid place-items-center text-[10px] text-white" style="background:#d97706"><i class="fas fa-exclamation"></i></span>';
        lista.innerHTML = passos.map(p => `
            <li class="flex gap-2 items-start">
                ${icone(p.ok)}
                <div class="min-w-0">
                    <b class="block">${escaparHtml(p.titulo)}</b>
                    <span class="block whitespace-pre-line break-words">${escaparHtml(p.detalhe)}</span>
                    ${p.dica ? `<span class="block mt-0.5 text-gray-500 break-words">${escaparHtml(p.dica)}</span>` : ""}
                </div>
            </li>`).join("");
        return passos;
    }

    /* ------------------------------------- janela de configuração */
    async function abrirConfiguracao() {
        document.getElementById("cf-config-impressora")?.remove();
        const c = config();
        const fundo = document.createElement("div");
        fundo.id = "cf-config-impressora";
        fundo.className = "fixed inset-0 z-[200] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm";
        fundo.innerHTML = `
            <div class="w-full max-w-lg max-h-[92vh] overflow-y-auto rounded-[2rem] shadow-2xl p-6 space-y-4" style="background: var(--cf-superficie, #fff); color: var(--cf-texto, #111)">
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
                <div class="rounded-2xl border p-4 space-y-3">
                    <div class="flex items-center justify-between gap-2">
                        <span class="text-[11px] font-black uppercase tracking-wide text-gray-500">Diagnóstico da impressão</span>
                        <button type="button" data-diagnosticar class="text-xs font-black underline" style="color: var(--cf-marca, #e3353d)">Verificar de novo</button>
                    </div>
                    <ul data-passos class="space-y-3 text-xs"></ul>
                    <a href="/api/qz/certificado/?baixar=1" download="override.crt"
                       class="flex items-center justify-center gap-2 rounded-xl py-2.5 text-xs font-black bg-gray-100 text-gray-700">
                        <i class="fas fa-download"></i> Baixar certificado do site (override.crt)
                    </a>
                    <p class="text-[11px] text-gray-400">Instale-o no PC do caixa com <b>qz\\configurar_caixa.bat</b> (ou copie para C:\\Program Files\\QZ Tray\\override.crt e reinicie o QZ).</p>
                </div>
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
        const passos = fundo.querySelector("[data-passos]");
        fundo.querySelector("[data-diagnosticar]").addEventListener("click", () => mostrarDiagnostico(passos));
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
            // também avisa se a impressora salva sumiu (avisoImpressora)
            const escolhida = await escolherImpressora(true).catch(() => "");
            const atual = c.impressora || escolhida;
            sel.innerHTML = '<option value="">Automática (procura a térmica)</option>' +
                lista.map(n => `<option ${n === atual ? "selected" : ""}>${escaparHtml(n)}</option>`).join("");
            status.textContent = `${lista.length} impressora(s) encontrada(s).` + (avisoImpressora ? " " + avisoImpressora : "");
        } catch (erro) {
            sel.innerHTML = '<option value="">QZ Tray não conectado</option>';
            status.textContent = erro.message;
        }
        mostrarDiagnostico(passos);
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
        diagnosticar,
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
