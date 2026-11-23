/* =========================================================================
   RESTAURAR FOTOS — Cantinho Família
   O ZIP da Cloudinary é aberto AQUI no navegador (sem carregar tudo na
   memória) e cada foto vai numa requisição própria: na Vercel cada envio
   aceita ~4,5 MB, então um ZIP grande nunca passaria de uma vez. Antes de
   enviar, o servidor diz quais arquivos servem para alguma foto; só esses
   sobem. Foto maior que o limite é reduzida aqui (WebP, até 2000 px).
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
    const EH_IMAGEM = /\.(jpe?g|jfif|png|webp|gif|bmp|tiff?|avif|heic)$/i;
    const SITUACOES = {
        restaurada: "Restaurada", enviando: "Enviando", sem_uso: "Sem uso no site",
        ja_restaurada: "Já estava no banco", duvida: "Na dúvida", erro: "Erro"
    };
    let ocupado = false;

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
        if (blob.size <= LIMITE) return { blob, nome };
        if (!EH_IMAGEM.test(nome) || typeof createImageBitmap !== "function") throw new Error("maior que o limite de envio");
        const bmp = await createImageBitmap(blob);
        const escala = Math.min(1, 2000 / Math.max(bmp.width, bmp.height));
        const tela = document.createElement("canvas");
        tela.width = Math.round(bmp.width * escala);
        tela.height = Math.round(bmp.height * escala);
        tela.getContext("2d").drawImage(bmp, 0, 0, tela.width, tela.height);
        const menor = await new Promise(ok => tela.toBlob(ok, "image/webp", 0.86));
        if (!menor || menor.size > LIMITE) throw new Error("maior que o limite de envio");
        return { blob: menor, nome: nome.replace(/\.[^./]+$/, "") + ".webp" };
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
            throw new Error(dados.mensagem || (resposta.status === 413 ? "arquivo grande demais para um envio" : `erro ${resposta.status}`));
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
            const numero = $("rf-faltando");
            numero.textContent = lista.length;
            numero.classList.toggle("ok", !lista.length);
            $("rf-tabela").innerHTML = lista.length ? lista.map(l =>
                `<tr><td><strong style="color: var(--cf-texto)">${escapar(l.rotulo || "—")}</strong><br><span class="rf-suave">${escapar(l.modelo)} #${escapar(l.pk)}</span></td>` +
                `<td>${escapar(l.campo)}</td><td><code>${escapar(l.nome)}</code></td></tr>`).join("")
                : `<tr><td colspan="3" class="text-center py-6"><i class="fas fa-circle-check mr-1" style="color: #16a34a"></i> Todas as fotos estão no banco.</td></tr>`;
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
            for (const item of itens) {
                const s = situacoes[item.caminho];
                if (!item.zip && s && s.situacao !== "restaurar") linha(item.caminho, s.situacao, s.motivo || "");
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
            const semUso = itens.length - enviar.length;
            resumo(
                `${restauradas} foto${restauradas === 1 ? "" : "s"} restaurada${restauradas === 1 ? "" : "s"}` +
                (semUso ? ` · ${semUso} arquivo${semUso === 1 ? "" : "s"} sem uso no site` : "") +
                (erros ? ` · ${erros} com erro (veja a lista)` : ""),
                erros ? "atencao" : (restauradas ? "ok" : "info")
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

    entrada.addEventListener("change", () => processar(Array.from(entrada.files)));
    ["dragenter", "dragover"].forEach(ev => solta.addEventListener(ev, e => { e.preventDefault(); solta.classList.add("arrastando"); }));
    ["dragleave", "drop"].forEach(ev => solta.addEventListener(ev, e => { e.preventDefault(); solta.classList.remove("arrastando"); }));
    solta.addEventListener("drop", e => processar(Array.from(e.dataTransfer.files || [])));
})();
