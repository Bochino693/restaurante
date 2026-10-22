/* =========================================================================
   ESTATÍSTICAS — abas e gráficos.

   A página é dividida em abas (Vendas, Pagamentos, Produtos, Bebidas,
   Horários, Cardápio online): cada assunto cabe numa tela, sem a rolagem
   sem fim de antes. Os gráficos de uma aba só são desenhados quando ela
   abre pela primeira vez (num painel escondido o Chart.js não tem tamanho
   para desenhar). Cada aba começa com destaques calculados dos números.

   O Chart.js é carregado por uma promessa que espera ele chegar, e uma tela
   de carregamento cobre a página até os primeiros gráficos estarem prontos.
   ========================================================================= */
(function () {
    "use strict";

    const CHART_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js";
    const ABAS = ["vendas", "pagamentos", "produtos", "bebidas", "horarios", "cardapio"];
    const tela = document.getElementById("telaCarregando");
    const textoTela = document.getElementById("telaCarregandoTexto");
    const barraAbas = document.getElementById("abas");
    const marcador = barraAbas ? barraAbas.querySelector(".aba-marcador") : null;
    const graficos = [];
    const desenhadas = new Set();
    let abaAtiva = "vendas";

    const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
    const brlCurto = v => v >= 1000 ? "R$ " + (v / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " mil" : brl.format(v);
    const pct = v => (Math.round(v * 10) / 10).toLocaleString("pt-BR") + "%";

    function ler(id, padrao) {
        const el = document.getElementById(id);
        if (!el) return padrao;
        try { return JSON.parse(el.textContent); } catch (_) { return padrao; }
    }

    const dados = {
        vendas: ler("graficoVendasDiariasJson", {}),
        semana: ler("graficoDiaSemanaJson", {}),
        horarios: ler("graficoHorariosJson", {}),
        pagamentos: ler("graficoPagamentosJson", {}),
        categorias: ler("graficoCategoriasJson", {}),
        entrega: ler("graficoEntregaJson", {}),
        status: ler("graficoStatusJson", {}),
        topProdutos: ler("graficoTopProdutosJson", {}),
        topReceita: ler("graficoTopReceitaJson", {}),
        cliques: ler("graficoCliquesJson", {}),
        topBebidas: ler("graficoTopBebidasJson", {}),
    };

    function mostrarTela(texto) {
        if (!tela) return;
        if (texto) textoTela.textContent = texto;
        tela.classList.remove("sumiu");
    }

    function esconderTela() {
        if (tela) tela.classList.add("sumiu");
    }

    function carregarChart() {
        if (window.Chart) return Promise.resolve();
        return new Promise((resolve, reject) => {
            const s = document.createElement("script");
            s.src = CHART_URL;
            s.onload = () => (window.Chart ? resolve() : reject(new Error("Chart.js indisponível")));
            s.onerror = () => reject(new Error("Não foi possível baixar a biblioteca de gráficos."));
            document.head.appendChild(s);
        });
    }

    /* ------------------------------------------------------ cores */
    function paleta() {
        const css = getComputedStyle(document.documentElement);
        const v = n => css.getPropertyValue(n).trim();
        const escuro = document.documentElement.classList.contains("cf-escuro");
        return {
            marca: v("--cf-marca") || "#e3353d",
            texto: v("--cf-texto-suave") || "#64748b",
            grade: v("--cf-grafico-grade") || "rgba(148,163,184,.18)",
            superficie: v("--cf-superficie") || "#fff",
            tooltip: escuro ? "#000" : "#1f1b18",
            serie: ["#e3353d", "#f59e0b", "#10b981", "#3b82f6", "#8b5cf6", "#ec4899", "#14b8a6", "#64748b"],
            bebida: escuro ? "#38bdf8" : "#0284c7",
            // Cada forma de pagamento tem a SUA cor (a mesma do cartão da tela
            // de Pedidos), nesta ordem na pilha. Conferidas para daltonismo
            // no fundo claro e no escuro.
            pagamentos: [
                { chave: "DINHEIRO", nome: "Dinheiro", cor: escuro ? "#059669" : "#10b981" },
                { chave: "CARTAO", nome: "Cartão", cor: "#3b82f6" },
                { chave: "PIX", nome: "Pix", cor: escuro ? "#0d9488" : "#14b8a6" },
                { chave: "MISTO", nome: "Misto", cor: escuro ? "#d97706" : "#f59e0b" },
                { chave: "TAXA", nome: "Taxa do motoca", cor: "#8b5cf6" }
            ]
        };
    }

    function transparente(hex, alfa) {
        const h = hex.replace("#", "");
        const n = parseInt(h.length === 3 ? h.split("").map(c => c + c).join("") : h, 16);
        return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alfa})`;
    }

    function base(c) {
        return {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 600 },
            interaction: { mode: "index", intersect: false },
            plugins: {
                legend: {
                    position: "bottom",
                    labels: { usePointStyle: true, boxWidth: 8, padding: 16, color: c.texto, font: { size: 11, weight: "700" } }
                },
                tooltip: { backgroundColor: c.tooltip, padding: 12, titleFont: { weight: "800" }, bodyFont: { weight: "700" } }
            },
            scales: {
                x: { grid: { display: false }, ticks: { color: c.texto, maxRotation: 0, autoSkip: true, font: { size: 10 } } },
                y: { beginAtZero: true, grid: { color: c.grade }, ticks: { color: c.texto, precision: 0, font: { size: 10 } } }
            }
        };
    }

    function vazio(canvas, valores) {
        const temDado = (valores || []).some(v => Number(v) > 0);
        if (!temDado) {
            const aviso = document.createElement("div");
            aviso.className = "chart-vazio";
            aviso.innerHTML = '<div><i class="fas fa-chart-simple" style="font-size:28px;opacity:.35"></i><br>Sem dados neste período</div>';
            canvas.parentElement.appendChild(aviso);
        }
        return !temDado;
    }

    function criar(id, config, valores) {
        const canvas = document.getElementById(id);
        if (!canvas) return;
        if (vazio(canvas, valores)) return;
        graficos.push({ chart: new Chart(canvas, config), id });
    }

    /* ---------------------------------------------------- os gráficos */
    function vendasDiarias(c) {
        const vendas = dados.vendas;
        const op = base(c);
        op.scales = {
            x: op.scales.x,
            yReceita: { beginAtZero: true, position: "left", grid: { color: c.grade }, ticks: { color: c.texto, callback: v => brlCurto(v), font: { size: 10 } } },
            yPedidos: { beginAtZero: true, position: "right", grid: { drawOnChartArea: false }, ticks: { color: c.texto, precision: 0, font: { size: 10 } } }
        };
        op.plugins.tooltip.callbacks = {
            label: ctx => ctx.dataset.yAxisID === "yReceita" ? ` Faturamento: ${brl.format(ctx.parsed.y)}` : ` Pedidos: ${ctx.parsed.y}`
        };
        criar("chartVendasDiarias", {
            type: "bar",
            data: {
                labels: vendas.labels || [],
                datasets: [
                    {
                        type: "line", label: "Faturamento", data: vendas.receita || [], yAxisID: "yReceita",
                        borderColor: c.marca, backgroundColor: transparente(c.marca, .12), fill: true,
                        pointBackgroundColor: c.marca, pointRadius: 2.5, pointHoverRadius: 5, tension: .35
                    },
                    {
                        label: "Pedidos", data: vendas.pedidos || [], yAxisID: "yPedidos",
                        backgroundColor: transparente("#f59e0b", .55), borderRadius: 6
                    }
                ]
            },
            options: op
        }, vendas.receita);
    }

    function linhaSimples(c, id, valores, rotulo, cor, formato) {
        const op = base(c);
        op.plugins.legend.display = false;
        op.scales.y.ticks.callback = v => formato === "curto" ? brlCurto(v) : brl.format(v);
        op.plugins.tooltip.callbacks = { label: ctx => ` ${rotulo}: ${brl.format(ctx.parsed.y)}` };
        criar(id, {
            type: "line",
            data: {
                labels: dados.vendas.labels || [],
                datasets: [{
                    label: rotulo, data: valores || [], borderColor: cor,
                    backgroundColor: transparente(cor, .14), fill: true, tension: .32,
                    pointRadius: formato === "curto" ? 0 : 2
                }]
            },
            options: op
        }, valores);
    }

    /* RECEBIDO POR DIA: o relatório da tela de Pedidos (Dinheiro, Pix,
       Cartão, taxa do motoca), dia a dia, em barras empilhadas — a altura
       da barra é tudo o que entrou no dia. */
    function recebimentos(c) {
        const vendas = dados.vendas;
        const porPagamento = vendas.por_pagamento || {};
        const camadas = c.pagamentos
            .map(p => ({ ...p, valores: p.chave === "TAXA" ? (vendas.taxas || []) : (porPagamento[p.chave] || []) }))
            .filter(p => p.valores.some(v => Number(v) > 0));
        const op = base(c);
        op.scales.x.stacked = true;
        op.scales.y.stacked = true;
        op.scales.y.ticks.callback = v => brlCurto(v);
        op.plugins.tooltip.callbacks = {
            label: ctx => ` ${ctx.dataset.label}: ${brl.format(ctx.parsed.y)}`,
            footer: itens => `Total do dia: ${brl.format(itens.reduce((soma, i) => soma + i.parsed.y, 0))}`
        };
        op.plugins.tooltip.itemSort = (a, b) => b.datasetIndex - a.datasetIndex;
        op.plugins.tooltip.filter = item => item.parsed.y > 0;
        criar("chartRecebimentos", {
            type: "bar",
            data: {
                labels: vendas.labels || [],
                datasets: camadas.map(p => ({
                    label: p.nome,
                    data: p.valores,
                    backgroundColor: p.cor,
                    borderColor: c.superficie,
                    borderWidth: { top: 2 },
                    borderSkipped: false,
                    borderRadius: 4,
                    maxBarThickness: 46
                }))
            },
            options: op
        }, camadas.flatMap(p => p.valores));
    }

    function diaSemana(c) {
        const semana = dados.semana;
        const op = base(c);
        op.plugins.legend.display = false;
        op.scales.y.ticks.callback = v => brlCurto(v);
        op.plugins.tooltip.callbacks = { label: ctx => ` Média: ${brl.format(ctx.parsed.y)}` };
        const maior = Math.max(...(semana.valores || [0]));
        criar("chartDiaSemana", {
            type: "bar",
            data: {
                labels: semana.labels || [],
                datasets: [{
                    label: "Faturamento médio", data: semana.valores || [], borderRadius: 10,
                    backgroundColor: (semana.valores || []).map(v => v === maior ? c.marca : transparente(c.marca, .35))
                }]
            },
            options: op
        }, semana.valores);
    }

    function horarios(c) {
        const d = dados.horarios;
        const op = base(c);
        op.plugins.legend.display = false;
        const maior = Math.max(...(d.valores || [0]));
        criar("chartHorarios", {
            type: "bar",
            data: {
                labels: d.labels || [],
                datasets: [{
                    label: "Pedidos", data: d.valores || [], borderRadius: 8,
                    backgroundColor: (d.valores || []).map(v => v === maior ? "#3b82f6" : transparente("#3b82f6", .45))
                }]
            },
            options: op
        }, d.valores);
    }

    function rosca(c, id, d, dinheiro) {
        const cores = c.serie;
        const b = base(c);
        criar(id, {
            type: "doughnut",
            data: {
                labels: d.labels || [],
                datasets: [{ data: d.valores || [], backgroundColor: cores, borderColor: c.superficie, borderWidth: 3, hoverOffset: 8 }]
            },
            options: {
                ...b,
                cutout: "62%",
                scales: {},
                plugins: {
                    ...b.plugins,
                    tooltip: {
                        ...b.plugins.tooltip,
                        callbacks: {
                            label: ctx => {
                                const total = ctx.dataset.data.reduce((a, x) => a + x, 0) || 1;
                                const parte = (ctx.parsed / total * 100).toFixed(1).replace(".", ",");
                                const valor = dinheiro ? brl.format(ctx.parsed) : ctx.parsed;
                                const extra = d.pedidos ? ` · ${d.pedidos[ctx.dataIndex]} pedidos` : "";
                                return ` ${ctx.label}: ${valor} (${parte}%)${extra}`;
                            }
                        }
                    },
                    legend: {
                        ...b.plugins.legend,
                        labels: {
                            ...b.plugins.legend.labels,
                            generateLabels(chart) {
                                const ds = chart.data.datasets[0];
                                const total = ds.data.reduce((a, x) => a + x, 0) || 1;
                                return chart.data.labels.map((label, i) => ({
                                    text: `${label}: ${Math.round(ds.data[i] / total * 100)}%`,
                                    fillStyle: cores[i % cores.length],
                                    strokeStyle: cores[i % cores.length],
                                    fontColor: c.texto,
                                    hidden: false,
                                    index: i
                                }));
                            }
                        }
                    }
                }
            }
        }, d.valores);
    }

    /* RANKINGS EM BARRAS DEITADAS (campeões, faturamento, cliques, bebidas).
       - Passar o mouse EM CIMA da barra (ou do nome) mostra o valor.
       - O 1º colocado fica em cima; o valor aparece na ponta da barra.
       - A altura cresce com o número de produtos (nada espremido) e os
         nomes longos são encurtados — o nome inteiro vai na dica. */
    const valorNaPonta = {
        id: "valorNaPonta",
        afterDatasetsDraw(chart, _args, opts) {
            const { ctx } = chart;
            const meta = chart.getDatasetMeta(0);
            if (!meta || meta.hidden) return;
            ctx.save();
            ctx.font = "800 11px Inter, ui-sans-serif, system-ui, sans-serif";
            ctx.textBaseline = "middle";
            const area = chart.chartArea;
            meta.data.forEach((barra, i) => {
                const v = chart.data.datasets[0].data[i];
                const texto = opts.formatar(v);
                const largura = ctx.measureText(texto).width;
                const cabeFora = barra.x + 8 + largura <= area.right;
                ctx.fillStyle = cabeFora ? opts.corTexto : "#fff";
                ctx.textAlign = cabeFora ? "left" : "right";
                ctx.fillText(texto, cabeFora ? barra.x + 8 : barra.x - 8, barra.y);
            });
            ctx.restore();
        }
    };

    function barrasDeitadas(c, id, d, dinheiro, cor) {
        const pares = (d.labels || []).map((l, i) => [l, Number((d.valores || [])[i] || 0)])
            .sort((a, b) => b[1] - a[1]);
        const labels = pares.map(p => p[0]);
        const valores = pares.map(p => p[1]);
        const formatar = v => dinheiro ? brl.format(v) : `${v} un.`;
        const estreita = window.matchMedia("(max-width: 560px)").matches;
        const limite = estreita ? 14 : 24;

        const caixa = document.getElementById(id)?.parentElement;
        if (caixa) caixa.style.height = Math.max(240, labels.length * (estreita ? 32 : 36) + 40) + "px";

        const op = base(c);
        op.indexAxis = "y";
        op.interaction = { mode: "index", axis: "y", intersect: false };
        op.onHover = (evt, ativos) => {
            const alvo = evt?.native?.target;
            if (alvo) alvo.style.cursor = ativos.length ? "pointer" : "default";
        };
        op.layout = { padding: { right: estreita ? 64 : 84 } };
        op.plugins.legend.display = false;
        op.plugins.valorNaPonta = { formatar, corTexto: c.texto };
        op.scales = {
            x: { beginAtZero: true, grace: "5%", grid: { color: c.grade }, ticks: { color: c.texto, precision: 0, maxTicksLimit: estreita ? 4 : 6, callback: v => dinheiro ? brlCurto(v) : v, font: { size: 10 } } },
            y: {
                grid: { display: false },
                ticks: {
                    color: c.texto,
                    font: { size: estreita ? 10 : 11, weight: "700" },
                    callback(valor) {
                        const nome = String(this.getLabelForValue(valor) || "");
                        return nome.length > limite ? nome.slice(0, limite - 1) + "…" : nome;
                    }
                }
            }
        };
        op.plugins.tooltip.callbacks = {
            title: itens => itens.length ? `${itens[0].dataIndex + 1}º  ${itens[0].label}` : "",
            label: ctx => dinheiro ? ` ${brl.format(ctx.parsed.x)}` : ` ${ctx.parsed.x} unidades`
        };
        criar(id, {
            type: "bar",
            data: {
                labels,
                datasets: [{
                    label: d.titulo || "Total",
                    data: valores,
                    backgroundColor: transparente(cor, .78),
                    hoverBackgroundColor: cor,
                    borderRadius: 8,
                    borderSkipped: false,
                    maxBarThickness: 26,
                    categoryPercentage: .78,
                    barPercentage: .9
                }]
            },
            options: op,
            plugins: [valorNaPonta]
        }, valores);
    }

    function bebidasDiarias(c) {
        const vendas = dados.vendas;
        const unidades = vendas.bebidas_unidades || [];
        const op = base(c);
        op.plugins.legend.display = false;
        op.scales.y.ticks.callback = v => brlCurto(v);
        op.plugins.tooltip.callbacks = {
            label: ctx => ` Bebidas: ${brl.format(ctx.parsed.y)}`,
            afterLabel: ctx => {
                const un = unidades[ctx.dataIndex] || 0;
                return ` ${un} unidade${un === 1 ? "" : "s"}`;
            }
        };
        criar("chartBebidasDiarias", {
            type: "bar",
            data: {
                labels: vendas.labels || [],
                datasets: [{
                    label: "Bebidas", data: vendas.bebidas || [],
                    backgroundColor: transparente(c.bebida, .8), hoverBackgroundColor: c.bebida,
                    borderRadius: 4, borderSkipped: "start", maxBarThickness: 38
                }]
            },
            options: op
        }, vendas.bebidas);
    }

    const DESENHOS = {
        vendas: c => {
            vendasDiarias(c);
            linhaSimples(c, "chartAcumulado", dados.vendas.acumulado, "Acumulado", "#10b981", "curto");
            linhaSimples(c, "chartTicket", dados.vendas.ticket, "Ticket médio", "#8b5cf6", "cheio");
        },
        pagamentos: c => {
            recebimentos(c);
            rosca(c, "chartPagamentos", dados.pagamentos, true);
            rosca(c, "chartEntrega", dados.entrega, false);
        },
        produtos: c => {
            barrasDeitadas(c, "chartTopProdutos", dados.topProdutos, false, c.marca);
            barrasDeitadas(c, "chartTopReceita", dados.topReceita, true, "#10b981");
            rosca(c, "chartCategorias", dados.categorias, true);
        },
        bebidas: c => {
            bebidasDiarias(c);
            barrasDeitadas(c, "chartTopBebidas", dados.topBebidas, false, c.bebida);
        },
        horarios: c => {
            diaSemana(c);
            horarios(c);
            rosca(c, "chartStatus", dados.status, false);
        },
        cardapio: c => {
            barrasDeitadas(c, "chartCliques", dados.cliques, false, "#f59e0b");
        },
    };

    function desenharAba(aba) {
        if (!window.Chart || desenhadas.has(aba) || !DESENHOS[aba]) return;
        desenhadas.add(aba);
        Chart.defaults.font.family = "Inter, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif";
        DESENHOS[aba](paleta());
    }

    function redesenhar() {
        graficos.splice(0).forEach(g => g.chart.destroy());
        document.querySelectorAll(".chart-vazio").forEach(v => v.remove());
        desenhadas.clear();
        desenharAba(abaAtiva);
    }

    /* ------------------------------------------------------- destaques */
    // Nomes de produto vêm do banco: nunca entram crus no HTML.
    function esc(texto) {
        return String(texto == null ? "" : texto).replace(/[&<>"']/g, c => (
            { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
        ));
    }

    function soma(lista) { return (lista || []).reduce((a, v) => a + Number(v || 0), 0); }

    function maiorDe(d) {
        const valores = (d.valores || []).map(Number);
        if (!valores.length) return null;
        const i = valores.indexOf(Math.max(...valores));
        return valores[i] > 0 ? { rotulo: d.labels[i], valor: valores[i], parte: valores[i] / (soma(valores) || 1) * 100 } : null;
    }

    function destaques(aba) {
        const v = dados.vendas;
        const lista = [];
        if (aba === "vendas") {
            const receitas = (v.receita || []).map(Number);
            const comVenda = receitas.filter(x => x > 0);
            if (comVenda.length) {
                const i = receitas.indexOf(Math.max(...receitas));
                lista.push(["fa-trophy", `Melhor dia: <b>${esc(v.labels[i])}</b> com <b>${brl.format(receitas[i])}</b>`]);
                lista.push(["fa-calendar-day", `Média por dia aberto: <b>${brl.format(soma(comVenda) / comVenda.length)}</b>`]);
                const meio = Math.floor(receitas.length / 2);
                const antes = soma(receitas.slice(0, meio)) / (meio || 1);
                const depois = soma(receitas.slice(meio)) / ((receitas.length - meio) || 1);
                if (meio && antes > 0) {
                    const mudou = (depois - antes) / antes * 100;
                    lista.push([mudou >= 0 ? "fa-arrow-trend-up" : "fa-arrow-trend-down",
                        `2ª metade do período: <b>${mudou >= 0 ? "+" : ""}${pct(mudou)}</b> por dia`, mudou >= 0 ? "bom" : "ruim"]);
                }
            }
        }
        if (aba === "pagamentos") {
            const lider = maiorDe(dados.pagamentos);
            if (lider) lista.push(["fa-crown", `<b>${esc(lider.rotulo)}</b> lidera: <b>${pct(lider.parte)}</b> do faturamento`]);
            const entregas = (dados.entrega.valores || [])[0] || 0;
            const totalPedidos = soma(dados.entrega.valores);
            if (totalPedidos) lista.push(["fa-motorcycle", `<b>${pct(entregas / totalPedidos * 100)}</b> dos pedidos são entrega`]);
            const taxas = soma(v.taxas);
            if (taxas) lista.push(["fa-sack-dollar", `Taxas do motoca no período: <b>${brl.format(taxas)}</b>`]);
        }
        if (aba === "produtos") {
            const lider = maiorDe(dados.topReceita);
            if (lider) lista.push(["fa-crown", `Quem mais fatura: <b>${esc(lider.rotulo)}</b> (${brl.format(lider.valor)})`]);
            const itens = soma(dados.categorias.valores);
            const top3 = (dados.topReceita.valores || []).map(Number).sort((a, b) => b - a).slice(0, 3);
            if (itens && top3.length >= 3) lista.push(["fa-layer-group", `Os 3 campeões somam <b>${pct(soma(top3) / itens * 100)}</b> das vendas de itens`]);
            const cat = maiorDe(dados.categorias);
            if (cat) lista.push(["fa-tags", `Categoria que mais vende: <b>${esc(cat.rotulo)}</b> (${pct(cat.parte)})`]);
        }
        if (aba === "horarios") {
            const pico = maiorDe(dados.horarios);
            if (pico) lista.push(["fa-clock", `Pico às <b>${esc(pico.rotulo)}</b>: ${pico.valor} pedido${pico.valor === 1 ? "" : "s"}`]);
            const dia = maiorDe(dados.semana);
            if (dia) lista.push(["fa-calendar-check", `Melhor dia da semana: <b>${esc(dia.rotulo)}</b> (média ${brl.format(dia.valor)})`]);
            const status = dados.status;
            const i = (status.labels || []).indexOf("Cancelado");
            const total = soma(status.valores);
            if (total) {
                const cancelados = i >= 0 ? status.valores[i] : 0;
                lista.push(["fa-ban", `Cancelamentos: <b>${pct(cancelados / total * 100)}</b>`, cancelados ? "ruim" : "bom"]);
            }
        }
        if (aba === "cardapio") {
            const total = soma(dados.cliques.valores);
            const lider = maiorDe(dados.cliques);
            if (total) lista.push(["fa-hand-pointer", `<b>${total}</b> escolhas no cardápio online`]);
            if (lider) lista.push(["fa-heart", `Mais escolhido: <b>${esc(lider.rotulo)}</b>`]);
        }
        return lista;
    }

    function preencherDestaques() {
        document.querySelectorAll("[data-insights]").forEach(caixa => {
            caixa.innerHTML = destaques(caixa.dataset.insights).map(([icone, texto, tom], i) =>
                `<span class="insight ${tom || ""}" style="--i:${i}"><i class="fas ${icone}"></i><span>${texto}</span></span>`
            ).join("");
        });
    }

    /* ------------------------------------------------------------ abas */
    function moverMarcador() {
        const botao = barraAbas && barraAbas.querySelector(`[data-aba="${abaAtiva}"]`);
        if (!botao || !marcador) return;
        marcador.style.width = botao.offsetWidth + "px";
        marcador.style.transform = `translateX(${botao.offsetLeft}px)`;
        barraAbas.dataset.ativa = abaAtiva;
    }

    function ativar(aba, { rolar } = {}) {
        if (!ABAS.includes(aba)) aba = "vendas";
        abaAtiva = aba;
        document.querySelectorAll("[data-aba]").forEach(b => {
            const ativa = b.dataset.aba === aba;
            b.classList.toggle("ativa", ativa);
            b.setAttribute("aria-selected", ativa ? "true" : "false");
            b.tabIndex = ativa ? 0 : -1;
        });
        document.querySelectorAll(".painel-aba").forEach(p => { p.hidden = p.id !== `painel-${aba}`; });
        moverMarcador();
        const botao = barraAbas && barraAbas.querySelector(`[data-aba="${aba}"]`);
        if (botao) botao.scrollIntoView({ block: "nearest", inline: "nearest" });
        // a aba fica no endereço: trocar o período (atalhos e datas) mantém a aba
        if (window.location.hash !== "#" + aba) history.replaceState(null, "", "#" + aba);
        document.querySelectorAll("[data-carregar]").forEach(a => { a.hash = aba; });
        const form = document.getElementById("formFiltros");
        if (form) form.action = "#" + aba;
        desenharAba(aba);
        if (rolar && barraAbas) {
            const topo = barraAbas.getBoundingClientRect().top + window.scrollY - alturaTopo() - 8;
            if (window.scrollY > topo) window.scrollTo({ top: topo, behavior: "smooth" });
        }
    }

    function alturaTopo() {
        const topo = document.querySelector(".cf-topo");
        return topo ? topo.offsetHeight : 0;
    }

    if (barraAbas) {
        document.documentElement.style.setProperty("--topo-altura", alturaTopo() + "px");
        barraAbas.addEventListener("click", e => {
            const botao = e.target.closest("[data-aba]");
            if (botao) ativar(botao.dataset.aba, { rolar: true });
        });
        // setas do teclado trocam de aba (padrão de abas acessíveis)
        barraAbas.addEventListener("keydown", e => {
            if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(e.key)) return;
            e.preventDefault();
            const i = ABAS.indexOf(abaAtiva);
            const prox = e.key === "Home" ? 0 : e.key === "End" ? ABAS.length - 1
                : (i + (e.key === "ArrowRight" ? 1 : -1) + ABAS.length) % ABAS.length;
            ativar(ABAS[prox]);
            barraAbas.querySelector(`[data-aba="${ABAS[prox]}"]`).focus();
        });
        window.addEventListener("hashchange", () => ativar(window.location.hash.slice(1), { rolar: true }));
        window.addEventListener("resize", moverMarcador);
    }

    /* ------------------------------------------------------- início */
    preencherDestaques();
    ativar(window.location.hash.slice(1) || "vendas");
    // a fonte pode chegar depois e mudar a largura dos botões das abas
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(moverMarcador);

    carregarChart()
        .then(() => {
            desenharAba(abaAtiva);
            // Espera o navegador pintar os gráficos antes de tirar a tela.
            requestAnimationFrame(() => requestAnimationFrame(esconderTela));
        })
        .catch(erro => {
            console.error(erro);
            textoTela.innerHTML = 'Não foi possível carregar os gráficos.<br><a href="" style="color:var(--cf-marca);text-decoration:underline">Tentar de novo</a>';
            setTimeout(esconderTela, 4000);
        });

    // Trocou o tema: os gráficos repintam com as cores novas.
    document.addEventListener("cf:tema", () => { if (window.Chart) redesenhar(); });
    // Celular <-> tela larga: os rankings mudam de altura e de tamanho de nome.
    const telaEstreita = window.matchMedia("(max-width: 560px)");
    const aoMudarLargura = () => { if (window.Chart) redesenhar(); };
    if (telaEstreita.addEventListener) telaEstreita.addEventListener("change", aoMudarLargura);
    else if (telaEstreita.addListener) telaEstreita.addListener(aoMudarLargura);

    // Filtros e atalhos: a tela de carregamento aparece na hora do clique.
    document.querySelectorAll("[data-carregar]").forEach(a => a.addEventListener("click", () => mostrarTela("Calculando o período…")));
    const form = document.getElementById("formFiltros");
    const inicio = document.getElementById("dataInicio");
    const fim = document.getElementById("dataFim");
    if (form) {
        form.addEventListener("submit", e => {
            if (inicio.value && fim.value && inicio.value > fim.value) {
                e.preventDefault();
                alert("A data inicial não pode ser maior que a data final.");
                return;
            }
            mostrarTela("Calculando o período…");
        });
    }
    if (!ler("datasDisponiveisJson", []).length) {
        [inicio, fim, document.getElementById("btnAplicarFiltro")].forEach(el => { if (el) el.disabled = true; });
    }
    // Voltar pelo navegador (cache de página) não deixa a tela presa.
    window.addEventListener("pageshow", e => { if (e.persisted) esconderTela(); });
})();
