/* =========================================================================
   RESTAURAR FOTOS — Cantinho Família
   O ZIP da Cloudinary é aberto AQUI no navegador (sem carregar tudo na
   memória) e cada foto vai numa requisição própria: na Vercel cada envio
   aceita ~4,5 MB, então um ZIP grande nunca passaria de uma vez. Antes de
   enviar, o servidor diz quais arquivos servem para alguma foto; só esses
   sobem. Foto maior que o limite é reduzida aqui (preparar_foto.js).

   Foto cujo nome não bate com nenhuma foto antiga NÃO é descartada: ela
   fica na lista com a pergunta "de quem é esta foto?" e você escolhe o
   produto/categoria. E cada linha de "Ainda faltando" tem o botão
   "Escolher foto" (também aceita a foto arrastada em cima da linha).
   ========================================================================= */
(function () {
    "use strict";

    const raiz = document.getElementById("restaurar-fotos");
    if (!raiz) return;
    const URL_BASE = raiz.dataset.url;
    const LIMITE = Number(raiz.dataset.limite) || 4 * 1024 * 1024;
    const CSRF = (raiz.querySelector("[name=csrfmiddlewaretoken]") || {}).value || "";
    const $ = id => document.getElementById(id);
    const entrada = $("rf-arquivos");
    const solta = $("rf-solta");
    const SITUACOES = {
        restaurada: "Guardada", enviando: "Enviando", sem_uso: "Sem uso no site",
        ja_restaurada: "Já estava no banco", duvida: "Na dúvida", erro: "Erro", escolher: "De quem é?"
    };
    let ocupado = false;
    let faltando = [];
    try { faltando = JSON.parse($("rf-dados-faltando").textContent) || []; } catch (_) { /* lista vazia */ }
    const chaveDe = l => `${l.chave}|${l.pk}|${l.campo}`;

    const escapar = t => String(t ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

    /* ------------------------------------------------------------ ZIP */
    const podeAbrirZip = typeof DecompressionStream === "function" && typeof Blob.prototype.stream === "function";

    async function lerZip(arquivo) {
        const tam = arquivo.size;
        const fimTam = Math.min(tam, 65557);
        const fim = new DataView(await arquivo.slice(tam - fimTam).arrayBuffer());
        let eocd = -1;
        for (let i = fimTam - 22; i >= 0; i--) {
            if (fim.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
        }
        if (eocd < 0) throw new Error("ZIP inválido ou incompleto");
        let total = fim.getUint16(eocd + 10, true);
        let cdTam = fim.getUint32(eocd + 12, true);
        let cdIni = fim.getUint32(eocd + 16, true);
        if (total === 0xffff || cdTam === 0xffffffff || cdIni === 0xffffffff) {   // ZIP64
            const loc = eocd - 20;
            if (loc >= 0 && fim.getUint32(loc, true) === 0x07064b50) {
                const pos = Number(fim.getBigUint64(loc + 8, true));
                const reg = new DataView(await arquivo.slice(pos, pos + 56).arrayBuffer());
                total = Number(reg.getBigUint64(32, true));
                cdTam = Number(reg.getBigUint64(40, true));
                cdIni = Number(reg.getBigUint64(48, true));
            }
        }
        const cd = new DataView(await arquivo.slice(cdIni, cdIni + cdTam).arrayBuffer());
        const texto = new TextDecoder("utf-8");
        const entradas = [];
        let p = 0;
        for (let n = 0; n < total && p + 46 <= cd.byteLength; n++) {
            if (cd.getUint32(p, true) !== 0x02014b50) break;
            const flags = cd.getUint16(p + 8, true);
            const metodo = cd.getUint16(p + 10, true);
            let comprimido = cd.getUint32(p + 20, true);
            let tamanho = cd.getUint32(p + 24, true);
            const nTam = cd.getUint16(p + 28, true), eTam = cd.getUint16(p + 30, true), cTam = cd.getUint16(p + 32, true);
            let local = cd.getUint32(p + 42, true);
            const nome = texto.decode(new Uint8Array(cd.buffer, cd.byteOffset + p + 46, nTam));
            let q = p + 46 + nTam;
            const fimExtra = q + eTam;
            while (q + 4 <= fimExtra) {
                const id = cd.getUint16(q, true), len = cd.getUint16(q + 2, true);
                if (id === 1) {
                    let r = q + 4;
                    if (tamanho === 0xffffffff) { tamanho = Number(cd.getBigUint64(r, true)); r += 8; }
                    if (comprimido === 0xffffffff) { comprimido = Number(cd.getBigUint64(r, true)); r += 8; }
                    if (local === 0xffffffff) { local = Number(cd.getBigUint64(r, true)); }
                }
                q += 4 + len;
            }
            p = fimExtra + cTam;
            const partes = nome.split("/");
            if (nome.endsWith("/") || (flags & 1) || partes.some(x => x.startsWith(".") || x === "__MACOSX")) continue;
            entradas.push({ nome, metodo, comprimido, tamanho, local });
        }
        return entradas;
    }

    async function extrair(arquivo, e) {
        const cab = new DataView(await arquivo.slice(e.local, e.local + 30).arrayBuffer());
        if (cab.getUint32(0, true) !== 0x04034b50) throw new Error("entrada corrompida no ZIP");
        const ini = e.local + 30 + cab.getUint16(26, true) + cab.getUint16(28, true);
        const dados = arquivo.slice(ini, ini + e.comprimido);
        if (e.metodo === 0) return dados;
        if (e.metodo === 8) {
            return await new Response(dados.stream().pipeThrough(new DecompressionStream("deflate-raw"))).blob();
        }
        throw new Error("compressão do ZIP não suportada");
    }

    /* ------------------------------------------------- foto grande demais */
    async function caber(blob, nome) {
        const base = nome.split("/").pop();
        const arquivo = blob instanceof File && blob.name === base ? blob : new File([blob], base, { type: blob.type || "" });
        const pronto = await window.cfPrepararFoto(arquivo, LIMITE);
        return { blob: pronto.arquivo, nome: pronto.arquivo.name };
    }

    /* -------------------------------------------------------- servidor */
    async function pedir(url, opcoes) {
        const resposta = await fetch(url, {
            credentials: "same-origin",
            ...opcoes,
            headers: { "X-CSRFToken": CSRF, "X-Requested-With": "fetch", Accept: "application/json", ...(opcoes.headers || {}) }
        });
        let dados = {};
        try { dados = await resposta.json(); } catch (_) { /* página de erro */ }
        if (!resposta.ok || dados.ok === false) {
            throw new Error(dados.mensagem || (resposta.status === 413 ? "arquivo grande demais para um envio"
                : resposta.status === 403 ? "sua sessão expirou: recarregue a página e entre de novo" : `erro ${resposta.status}`));
        }
        return dados;
    }

    async function verificar(caminhos) {
        const situacoes = {};
        for (let i = 0; i < caminhos.length; i += 500) {
            const dados = await pedir(`${URL_BASE}?acao=verificar`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ caminhos: caminhos.slice(i, i + 500) })
            });
            Object.assign(situacoes, dados.situacoes);
        }
        return situacoes;
    }

    async function enviarUm(caminho, blob, nome) {
        const form = new FormData();
        form.append("arquivo", blob, nome.split("/").pop());
        form.append("caminho", caminho);
        return pedir(`${URL_BASE}?acao=enviar`, { method: "POST", body: form });
    }

    async function definir(alvo, blob, nome) {
        const pronto = await caber(blob, nome);
        const form = new FormData();
        form.append("arquivo", pronto.blob, pronto.nome);
        form.append("modelo", alvo.chave);
        form.append("pk", alvo.pk);
        form.append("campo", alvo.campo);
        return pedir(`${URL_BASE}?acao=definir`, { method: "POST", body: form });
    }

    /* ------------------------------------------------------------- tela */
    const linhas = new Map();

    function linha(caminho, situacao, detalhe) {
        let el = linhas.get(caminho);
        if (!el) {
            el = document.createElement("div");
            el.className = "rf-item";
            linhas.set(caminho, el);
            $("rf-lista").prepend(el);
        }
        el.innerHTML = `<span class="rf-nome">${escapar(caminho)}${detalhe ? `<br><span class="rf-suave">${escapar(detalhe)}</span>` : ""}</span>` +
            `<span class="rf-selo ${situacao}">${SITUACOES[situacao] || situacao}</span>`;
        return el;
    }

    /* Foto que o nome não resolveu: você diz de quem ela é. */
    const aguardando = new Map();   // caminho -> { obter }
    const simples = t => String(t || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "");

    function opcoes(caminho, escolhida) {
        const nome = simples(caminho.split("/").pop().replace(/\.[^.]+$/, ""));
        let palpite = escolhida || "";
        if (!palpite && nome.length >= 3) {
            const parecidas = faltando.filter(l => { const r = simples(l.rotulo); return r && (r.includes(nome) || nome.includes(r)); });
            if (parecidas.length === 1) palpite = chaveDe(parecidas[0]);
        }
        return `<option value="">De quem é esta foto?</option>` + faltando.map(l =>
            `<option value="${escapar(chaveDe(l))}"${chaveDe(l) === palpite ? " selected" : ""}>${escapar(l.rotulo || l.nome)} — ${escapar(l.modelo)}</option>`).join("");
    }

    function perguntar(item, motivo) {
        if (!faltando.length) return false;
        aguardando.set(item.caminho, item);
        const el = linha(item.caminho, "escolher", motivo || "O nome não bate com nenhuma foto antiga: escolha o produto ou a categoria.");
        el.classList.add("rf-escolha");
        el.dataset.caminho = item.caminho;
        const caixa = document.createElement("div");
        caixa.className = "rf-escolher";
        caixa.innerHTML = `<img class="rf-previa" alt="" hidden><select class="rf-select">${opcoes(item.caminho)}</select>` +
            `<button type="button" class="rf-botao pequeno" data-usar>Usar esta foto</button>`;
        el.appendChild(caixa);
        if (aguardando.size <= 60) {
            item.obter().then(blob => {
                const previa = caixa.querySelector(".rf-previa");
                previa.onload = () => { previa.hidden = false; };
                previa.onerror = () => previa.remove();   // ex.: HEIC, que o Chrome não mostra
                previa.src = URL.createObjectURL(blob);
            }).catch(() => { /* sem prévia */ });
        }
        return true;
    }

    function atualizarEscolhas() {
        document.querySelectorAll(".rf-escolha .rf-select").forEach(select => {
            const caminho = select.closest(".rf-escolha").dataset.caminho;
            select.innerHTML = opcoes(caminho, select.value);
        });
    }

    function linhaDaTabela(l) {
        return `<tr data-chave="${escapar(l.chave)}" data-pk="${escapar(l.pk)}" data-campo="${escapar(l.campo)}" data-rotulo="${escapar(l.rotulo || "")}">` +
            `<td><strong style="color: var(--cf-texto)">${escapar(l.rotulo || "—")}</strong><br><span class="rf-suave">${escapar(l.modelo)} #${escapar(l.pk)}</span></td>` +
            `<td>${escapar(l.campo)}</td><td><code>${escapar(l.nome)}</code></td>` +
            `<td class="rf-acao"><button type="button" class="rf-botao pequeno" data-definir><i class="fas fa-image"></i> Escolher foto</button></td></tr>`;
    }

    function etapa(texto, feitos, total) {
        $("rf-etapa").textContent = texto;
        $("rf-contagem").textContent = total ? `${feitos}/${total}` : "";
        $("rf-barra").style.width = total ? `${Math.round((feitos / total) * 100)}%` : "0";
    }

    function resumo(texto, tipo) {
        const el = $("rf-resumo");
        el.className = `rf-aviso ${tipo}`;
        el.textContent = texto;
    }

    async function atualizarFaltando() {
        try {
            const resposta = await fetch(`${URL_BASE}?formato=json`, { credentials: "same-origin", headers: { Accept: "application/json" } });
            const dados = await resposta.json();
            const lista = dados.faltando || [];
            faltando = lista;
            atualizarEscolhas();
            if (dados.guardadas && $("rf-guardadas")) $("rf-guardadas").textContent = dados.guardadas.quantas;
            const numero = $("rf-faltando");
            numero.textContent = lista.length;
            numero.classList.toggle("ok", !lista.length);
            $("rf-tabela").innerHTML = lista.length ? lista.map(linhaDaTabela).join("")
                : `<tr><td colspan="4" class="text-center py-6"><i class="fas fa-circle-check mr-1" style="color: #16a34a"></i> Todas as fotos estão no banco.</td></tr>`;
        } catch (_) { /* a tabela antiga continua */ }
    }

    /* ------------------------------------------------------------ fluxo */
    async function processar(arquivos) {
        if (ocupado || !arquivos.length) return;
        ocupado = true;
        entrada.disabled = true;
        $("rf-progresso").classList.remove("hidden");
        $("rf-resumo").classList.add("hidden");
        const itens = [];   // { caminho, obter: () => Promise<Blob> }
        try {
            etapa("Lendo os arquivos…", 0, 0);
            for (const arquivo of arquivos) {
                const caminho = arquivo.webkitRelativePath || arquivo.name;
                if (/\.zip$/i.test(arquivo.name) || arquivo.type === "application/zip") {
                    if (podeAbrirZip) {
                        try {
                            for (const e of await lerZip(arquivo)) {
                                itens.push({ caminho: e.nome, obter: () => extrair(arquivo, e) });
                            }
                        } catch (erro) {
                            linha(caminho, "erro", erro.message);
                        }
                    } else if (arquivo.size <= LIMITE) {
                        itens.push({ caminho, obter: async () => arquivo, zip: true });
                    } else {
                        linha(caminho, "erro", "Este navegador não abre ZIP: extraia as fotos e envie-as soltas.");
                    }
                } else {
                    itens.push({ caminho, obter: async () => arquivo });
                }
            }

            etapa("Conferindo quais fotos servem…", 0, 0);
            const situacoes = await verificar(itens.filter(i => !i.zip).map(i => i.caminho));
            const enviar = itens.filter(i => i.zip || (situacoes[i.caminho] || {}).situacao === "restaurar");
            let perguntas = 0;
            for (const item of itens) {
                const s = situacoes[item.caminho];
                if (item.zip || !s || s.situacao === "restaurar") continue;
                if (s.situacao !== "ja_restaurada" && perguntar(item, s.motivo ? `Na dúvida (${s.motivo}): escolha de quem é.` : "")) perguntas += 1;
                else linha(item.caminho, s.situacao, s.motivo || "");
            }

            let feitos = 0, restauradas = 0, erros = 0;
            for (const item of enviar) {
                etapa("Enviando, uma foto por vez…", feitos, enviar.length);
                linha(item.caminho, "enviando");
                try {
                    const { blob, nome } = await caber(await item.obter(), item.caminho);
                    const dados = await enviarUm(item.caminho, blob, nome);
                    const r = dados.relatorio;
                    if (r.restauradas.length) {
                        restauradas += r.restauradas.length;
                        linha(item.caminho, "restaurada", r.restauradas.map(x => x.nome_antigo).join(", "));
                    } else if (r.erros.length) {
                        erros += 1;
                        linha(item.caminho, "erro", r.erros[0].mensagem);
                    } else if (r.duvidas.length) {
                        linha(item.caminho, "duvida", r.duvidas[0].motivo);
                    } else {
                        linha(item.caminho, r.ja_restauradas.length ? "ja_restaurada" : "sem_uso");
                    }
                } catch (erro) {
                    erros += 1;
                    linha(item.caminho, "erro", erro.message);
                }
                feitos += 1;
            }
            etapa("Pronto", feitos, enviar.length || 1);
            const semUso = itens.length - enviar.length - perguntas;
            resumo(
                `${restauradas} foto${restauradas === 1 ? "" : "s"} guardada${restauradas === 1 ? "" : "s"} pelo nome` +
                (perguntas ? ` · ${perguntas} esperando você dizer de quem é (escolha na lista abaixo)` : "") +
                (semUso ? ` · ${semUso} arquivo${semUso === 1 ? "" : "s"} sem uso no site` : "") +
                (erros ? ` · ${erros} com erro (veja a lista)` : ""),
                erros || perguntas ? "atencao" : (restauradas ? "ok" : "info")
            );
        } catch (erro) {
            resumo(`Não deu para continuar: ${erro.message}. Nada se perdeu; tente de novo.`, "erro");
        } finally {
            $("rf-resumo").classList.remove("hidden");
            ocupado = false;
            entrada.disabled = false;
            entrada.value = "";
            atualizarFaltando();
        }
    }

    /* ----------------------------------------- "Usar esta foto" (lista) */
    $("rf-lista").addEventListener("click", async evento => {
        const botao = evento.target.closest("[data-usar]");
        if (!botao) return;
        const el = botao.closest(".rf-escolha");
        const caminho = el.dataset.caminho;
        const escolhida = el.querySelector(".rf-select").value;
        const alvo = faltando.find(l => chaveDe(l) === escolhida);
        const item = aguardando.get(caminho);
        if (!alvo || !item) { el.querySelector(".rf-select").focus(); return; }
        botao.disabled = true;
        botao.textContent = "Enviando…";
        try {
            await definir(alvo, await item.obter(), caminho);
            aguardando.delete(caminho);
            el.classList.remove("rf-escolha");
            linha(caminho, "restaurada", `Foto de ${alvo.rotulo || alvo.nome}`);
            await atualizarFaltando();
        } catch (erro) {
            botao.disabled = false;
            botao.textContent = "Usar esta foto";
            let aviso = el.querySelector(".rf-erro-linha");
            if (!aviso) { aviso = document.createElement("div"); aviso.className = "rf-erro-linha"; el.appendChild(aviso); }
            aviso.textContent = erro.message;
        }
    });

    /* --------------------------- "Escolher foto" (tabela Ainda faltando) */
    const umaFoto = document.createElement("input");
    umaFoto.type = "file";
    umaFoto.accept = "image/*,.heic,.heif";
    umaFoto.className = "rf-oculto";
    raiz.appendChild(umaFoto);
    let linhaAlvo = null;

    async function definirNaLinha(tr, arquivo) {
        const botao = tr.querySelector("[data-definir]");
        const celula = tr.querySelector(".rf-acao");
        const alvo = { chave: tr.dataset.chave, pk: tr.dataset.pk, campo: tr.dataset.campo };
        botao.disabled = true;
        botao.textContent = "Enviando…";
        try {
            const dados = await definir(alvo, arquivo, arquivo.name);
            celula.innerHTML = `<span class="rf-selo restaurada">Guardada</span>`;
            $("rf-progresso").classList.remove("hidden");
            $("rf-resumo").classList.remove("hidden");
            resumo(dados.mensagem || "Foto guardada.", "ok");
            await atualizarFaltando();
        } catch (erro) {
            botao.disabled = false;
            botao.innerHTML = `<i class="fas fa-image"></i> Escolher foto`;
            let aviso = celula.querySelector(".rf-erro-linha");
            if (!aviso) { aviso = document.createElement("div"); aviso.className = "rf-erro-linha"; celula.appendChild(aviso); }
            aviso.textContent = erro.message;
        }
    }

    $("rf-tabela").addEventListener("click", evento => {
        const botao = evento.target.closest("[data-definir]");
        if (!botao) return;
        linhaAlvo = botao.closest("tr");
        umaFoto.value = "";
        umaFoto.click();
    });
    umaFoto.addEventListener("change", () => {
        if (linhaAlvo && umaFoto.files.length) definirNaLinha(linhaAlvo, umaFoto.files[0]);
    });
    $("rf-tabela").addEventListener("dragover", evento => {
        const tr = evento.target.closest("tr[data-chave]");
        if (tr) { evento.preventDefault(); tr.classList.add("rf-sobre"); }
    });
    $("rf-tabela").addEventListener("dragleave", evento => {
        const tr = evento.target.closest("tr[data-chave]");
        if (tr) tr.classList.remove("rf-sobre");
    });
    $("rf-tabela").addEventListener("drop", evento => {
        const tr = evento.target.closest("tr[data-chave]");
        if (!tr) return;
        evento.preventDefault();
        tr.classList.remove("rf-sobre");
        const arquivo = (evento.dataTransfer.files || [])[0];
        if (arquivo) definirNaLinha(tr, arquivo);
    });

    entrada.addEventListener("change", () => processar(Array.from(entrada.files)));
    ["dragenter", "dragover"].forEach(ev => solta.addEventListener(ev, e => { e.preventDefault(); solta.classList.add("arrastando"); }));
    ["dragleave", "drop"].forEach(ev => solta.addEventListener(ev, e => { e.preventDefault(); solta.classList.remove("arrastando"); }));
    solta.addEventListener("drop", e => processar(Array.from(e.dataTransfer.files || [])));
})();
