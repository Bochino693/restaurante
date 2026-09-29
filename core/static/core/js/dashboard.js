/* =========================================================================
   ESTATÍSTICAS — gráficos da tela de Estatísticas.

   POR QUE OS GRÁFICOS NÃO APARECIAM NA PRIMEIRA VEZ: a página era trocada
   pelo htmx (hx-boost) sem recarregar; o script dos gráficos rodava antes de
   o Chart.js terminar de baixar ("Chart is not defined") e nada era
   desenhado — só o F5 resolvia. Agora a navegação é normal, o Chart.js é
   carregado por uma promessa que espera ele chegar, e uma tela de
   carregamento cobre a página até o último gráfico estar pronto.
   ========================================================================= */
(function () {
    "use strict";

    const CHART_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js";
    const tela = document.getElementById("telaCarregando");
    const textoTela = document.getElementById("telaCarregandoTexto");
    const graficos = [];

    const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
    const brlCurto = v => v >= 1000 ? "R$ " + (v / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " mil" : brl.format(v);

    function ler(id, padrao) {
        const el = document.getElementById(id);
        if (!el) return padrao;
        try { return JSON.parse(el.textContent); } catch (_) { return padrao; }
    }

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
        return {
            marca: v("--cf-marca") || "#e3353d",
            texto: v("--cf-texto-suave") || "#64748b",
            grade: v("--cf-grafico-grade") || "rgba(148,163,184,.18)",
            superficie: v("--cf-superficie") || "#fff",
            tooltip: document.documentElement.classList.contains("cf-escuro") ? "#000" : "#1f1b18",
            serie: ["#e3353d", "#f59e0b", "#10b981", "#3b82f6", "#8b5cf6", "#ec4899", "#14b8a6", "#64748b"]
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
            animation: { duration: 500 },
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

    function vazio(canvas, dados) {
        const temDado = (dados || []).some(v => Number(v) > 0);
        if (!temDado) {
            const aviso = document.createElement("div");
            aviso.className = "chart-vazio";
            aviso.innerHTML = '<div><i class="fas fa-chart-simple" style="font-size:28px;opacity:.35"></i><br>Sem dados neste período</div>';
            canvas.parentElement.appendChild(aviso);
        }
        return !temDado;
    }

    function criar(id, config, dados) {
        const canvas = document.getElementById(id);
        if (!canvas) return;
        if (vazio(canvas, dados)) return;
        graficos.push({ chart: new Chart(canvas, config), id });
    }

    /* ---------------------------------------------------- os gráficos */
    function desenhar() {
        const c = paleta();
        Chart.defaults.font.family = "Inter, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif";

        const vendas = ler("graficoVendasDiariasJson", {});
        const opVendas = base(c);
        opVendas.scales = {
            x: opVendas.scales.x,
            yReceita: { beginAtZero: true, position: "left", grid: { color: c.grade }, ticks: { color: c.texto, callback: v => brlCurto(v), font: { size: 10 } } },
            yPedidos: { beginAtZero: true, position: "right", grid: { drawOnChartArea: false }, ticks: { color: c.texto, precision: 0, font: { size: 10 } } }
        };
        opVendas.plugins.tooltip.callbacks = {
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
            options: opVendas
        }, vendas.receita);

        const opAcum = base(c);
        opAcum.plugins.legend.display = false;
        opAcum.scales.y.ticks.callback = v => brlCurto(v);
        opAcum.plugins.tooltip.callbacks = { label: ctx => ` Acumulado: ${brl.format(ctx.parsed.y)}` };
        criar("chartAcumulado", {
            type: "line",
            data: {
                labels: vendas.labels || [],
                datasets: [{
                    label: "Acumulado", data: vendas.acumulado || [], borderColor: "#10b981",
                    backgroundColor: transparente("#10b981", .15), fill: true, tension: .3, pointRadius: 0
                }]
            },
            options: opAcum
        }, vendas.acumulado);

        const opTicket = base(c);
        opTicket.plugins.legend.display = false;
        opTicket.scales.y.ticks.callback = v => brl.format(v);
        opTicket.plugins.tooltip.callbacks = { label: ctx => ` Ticket médio: ${brl.format(ctx.parsed.y)}` };
        criar("chartTicket", {
            type: "line",
            data: {
                labels: vendas.labels || [],
                datasets: [{
                    label: "Ticket médio", data: vendas.ticket || [], borderColor: "#8b5cf6",
                    backgroundColor: transparente("#8b5cf6", .12), fill: true, tension: .35, pointRadius: 2
                }]
            },
            options: opTicket
        }, vendas.ticket);

        const semana = ler("graficoDiaSemanaJson", {});
        const opSemana = base(c);
        opSemana.plugins.legend.display = false;
        opSemana.scales.y.ticks.callback = v => brlCurto(v);
        opSemana.plugins.tooltip.callbacks = { label: ctx => ` Média: ${brl.format(ctx.parsed.y)}` };
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
            options: opSemana
        }, semana.valores);

        const horarios = ler("graficoHorariosJson", {});
        const opHora = base(c);
        opHora.plugins.legend.display = false;
        criar("chartHorarios", {
            type: "bar",
            data: {
                labels: horarios.labels || [],
                datasets: [{ label: "Pedidos", data: horarios.valores || [], backgroundColor: transparente("#3b82f6", .7), borderRadius: 8 }]
            },
            options: opHora
        }, horarios.valores);

        function rosca(id, dadosId, dinheiro) {
            const d = ler(dadosId, {});
            const cores = c.serie;
            criar(id, {
                type: "doughnut",
                data: {
                    labels: d.labels || [],
                    datasets: [{ data: d.valores || [], backgroundColor: cores, borderColor: c.superficie, borderWidth: 3, hoverOffset: 8 }]
                },
                options: {
                    ...base(c),
                    cutout: "62%",
                    scales: {},
                    plugins: {
                        ...base(c).plugins,
                        tooltip: {
                            ...base(c).plugins.tooltip,
                            callbacks: {
                                label: ctx => {
                                    const total = ctx.dataset.data.reduce((a, b) => a + b, 0) || 1;
                                    const pct = (ctx.parsed / total * 100).toFixed(1).replace(".", ",");
                                    const valor = dinheiro ? brl.format(ctx.parsed) : ctx.parsed;
                                    const extra = d.pedidos ? ` · ${d.pedidos[ctx.dataIndex]} pedidos` : "";
                                    return ` ${ctx.label}: ${valor} (${pct}%)${extra}`;
                                }
                            }
                        },
                        legend: {
                            ...base(c).plugins.legend,
                            labels: {
                                ...base(c).plugins.legend.labels,
                                generateLabels(chart) {
                                    const ds = chart.data.datasets[0];
                                    const total = ds.data.reduce((a, b) => a + b, 0) || 1;
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

        rosca("chartPagamentos", "graficoPagamentosJson", true);
        rosca("chartCategorias", "graficoCategoriasJson", true);
        rosca("chartEntrega", "graficoEntregaJson", false);
        rosca("chartStatus", "graficoStatusJson", false);

        /* RANKINGS EM BARRAS DEITADAS (campeões, faturamento, cliques).
           - Passar o mouse EM CIMA da barra (ou do nome) mostra o valor: a
             interação segue o eixo vertical; antes seguia o horizontal e só
             andava com o mouse de lado.
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

        function barrasDeitadas(id, dadosId, dinheiro, cor) {
            const d = ler(dadosId, {});
            // do maior para o menor: o campeão em cima
            const pares = (d.labels || []).map((l, i) => [l, Number((d.valores || [])[i] || 0)])
                .sort((a, b) => b[1] - a[1]);
            const labels = pares.map(p => p[0]);
            const valores = pares.map(p => p[1]);
            const formatar = v => dinheiro ? brl.format(v) : `${v} un.`;
            const estreita = window.matchMedia("(max-width: 560px)").matches;
            const limite = estreita ? 14 : 26;

            const caixa = document.getElementById(id)?.parentElement;
            if (caixa) caixa.style.height = Math.max(240, labels.length * (estreita ? 34 : 40) + 40) + "px";

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
                        maxBarThickness: 28,
                        categoryPercentage: .78,
                        barPercentage: .9
                    }]
                },
                options: op,
                plugins: [valorNaPonta]
            }, valores);
        }

        barrasDeitadas("chartTopProdutos", "graficoTopProdutosJson", false, c.marca);
        barrasDeitadas("chartTopReceita", "graficoTopReceitaJson", true, "#10b981");
        barrasDeitadas("chartCliques", "graficoCliquesJson", false, "#f59e0b");
    }

    function redesenhar() {
        graficos.splice(0).forEach(g => g.chart.destroy());
        document.querySelectorAll(".chart-vazio").forEach(v => v.remove());
        desenhar();
    }

    /* ------------------------------------------------------- início */
    carregarChart()
        .then(() => {
            desenhar();
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
