# Cantinho Família — sistema do restaurante

Django 5.2 · PDV (caixa), pedidos, estoque, cardápio do dia, estatísticas e
cardápio online para o cliente. Hospedado na Vercel, banco no Supabase e
**fotos guardadas no próprio banco** (a Cloudinary virou só legado — ver
[Fotos no banco](#fotos-no-banco-sem-depender-da-cloudinary)).

## Funcionamento

Segunda a sexta, das 11h às 16h. A regra mora em `core/horario.py` e vale
para o servidor, o cardápio do cliente e o painel.

## Variáveis de ambiente

| Variável | Para quê | Padrão |
| --- | --- | --- |
| `DATABASE_URL` | banco (Supabase) | SQLite local |
| `SECRET_KEY` | segurança do Django | **defina em produção** |
| `DEBUG` | `False` em produção | `True` |
| `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` | **opcionais, só legado**: endereço das fotos antigas e cópia delas com `espelhar_midia` (para copiar basta o `CLOUDINARY_CLOUD_NAME`) | vazias: o site sobe e funciona igual |
| `MIDIA_NO_BANCO` | `1`/`0` força as fotos no banco ou no disco | liga sozinho com `DATABASE_URL` (produção); sem ele, disco (`media/`) |
| `WHATSAPP_LOJA` | número que recebe os pedidos do cardápio (só dígitos, com 55 + DDD) | `5511999999999` (**troque**) |
| `IMPRESSAO_API_KEY` | chave do agente de impressão externo | a chave antiga, para não parar o agente |
| `LOJA_ABRE` / `LOJA_FECHA` | horário (hora cheia) | `11` / `16` |
| `DB_CONN_MAX_AGE` | segundos que a função reaproveita a conexão | `60` |
| `PALAVRAS_BEBIDAS` | palavras que fazem uma categoria contar como bebida nos relatórios (separadas por vírgula) | `bebida,refri,suco,cerveja,drink` |
| `MARMITA_PRECO_COM_REFRI` | preço das marmitas que oferecem o refri como adicional | `10.00` |
| `REFRI_MARMITA_PRECO_MAX` | preço máximo do refri que vira adicional das marmitas | `2.00` |
| `QZ_CERTIFICADO`, `QZ_CHAVE_PRIVADA` | certificado e chave do QZ Tray (o QZ lembra a permissão do site); geradas por `qz/gerar_certificado.py` | vazias (o QZ pergunta a cada conexão, mas imprime) |

## Fotos no banco (sem depender da Cloudinary)

A Cloudinary desativou a conta e levou junto todas as fotos e os envios
novos. Agora **toda foto enviada fica numa tabela do próprio banco**
(`core.ArquivoNoBanco`, migração `0015_arquivonobanco`) e o próprio site a
entrega em `/midia/banco/<caminho>`. Nenhum serviço de fora participa: se o
site está no ar, a foto está no ar. Nenhuma variável nova é obrigatória.

- **Envio** (admin): foto com mais de 2000 px ou mais de ~600 KB é regravada
  em **WebP** de no máximo 2000 px, boa qualidade, já girada como o celular
  tirou e sem os dados de localização. O nome é `banco/<pasta>/<nome>-<10 do sha256>.<ext>`:
  a mesma foto enviada duas vezes não ocupa o dobro, e um endereço nunca
  muda de conteúdo. Se gravar falhar, o formulário volta com o aviso
  "Não foi possível guardar a foto agora..." e tudo o que foi digitado —
  nunca uma página de erro 500.
- **Entrega**: `Cache-Control: public, max-age=31536000, s-maxage=31536000, immutable`
  e `ETag` (o navegador revalida com 304). Na Vercel quem segura as fotos é
  a borda (CDN), então o banco quase não é consultado; num servidor comum
  as fotos já entregues também ficam numa pasta temporária.
- **Tamanhos**: `?w=` entrega uma versão menor em WebP, só nas larguras
  96, 160, 240, 320, 480, 640, 800, 960, 1280 e 1600 (outra largura
  redireciona para a mais próxima). O cardápio pede 320 (960 no Prato do
  dia), o caixa 480/640. Nos templates: `{% load midia %}` e
  `{{ produto.image_produto|foto:480 }}`.
- **Foto que não carrega** (ex.: as antigas da Cloudinary) vira um quadro
  limpo **"Foto em atualização"** no mesmo lugar — no cardápio, no painel e
  no admin — nunca o ícone de imagem quebrada (`core/static/core/js/fotos.js`).
- **Nomes antigos** (sem `banco/`) continuam apontando para o endereço da
  Cloudinary, montado sem rede e sem banco. No desenvolvimento (sem
  `DATABASE_URL`) as fotos ficam no disco, como antes.
- Código: `core/midia.py` (armazenamento), `core/views_midia.py` (rota e
  tela), `core/restauracao.py` (restauração), testes em `core/tests_midia.py`.

**Espaço no Supabase**: cada foto fica com ~50–400 KB depois da conversão;
100 produtos dão poucos MB a algumas dezenas de MB. O plano grátis tem
500 MB de banco — vale olhar o uso de vez em quando (Supabase → Reports).

### Trazer as fotos antigas de volta

Enquanto uma foto antiga não volta, o site mostra "Foto em atualização".
Cada foto restaurada vira `banco/<nome antigo>` e **todo** campo que usava
o nome antigo passa a apontar para ela. Regra de ouro: **na dúvida, não
associa** (foto errada num produto é pior que a foto em atualização).

1. **Sozinho, a cada `migrate`**: depois do `migrate` (rode com o
   `DATABASE_URL` do Supabase para criar a tabela nova) o sistema procura
   cópias confiáveis dentro do projeto (`produtos/`, `imagens_categorias/`,
   `media/`, `staticfiles/`) e restaura o que bater pelo nome — inclusive o
   nome original sem o sufixo de 6 letras que a Cloudinary acrescentava
   (`coca.jpg` → `produtos/coca_ab12cd`), só quando não há outra
   possibilidade. Nunca derruba o migrate. À mão: `python manage.py restaurar_midia`.
2. **O que ainda falta**: `python manage.py restaurar_midia --relatorio`
   (produto/categoria, campo e nome antigo) ou a tela **Restaurar fotos**
   (menu lateral → Gestão e relatórios, só equipe/superusuário).
3. **ZIP da Cloudinary ou fotos que você tem**: na tela **Restaurar fotos**,
   arraste o ZIP baixado da Media Library (Download) ou as fotos soltas.
   Cada arquivo é ligado pela pasta + nome (o id da Cloudinary); a tela
   mostra o que foi restaurado, o que não é usado no site e o que ainda
   falta. Dica: renomeie a foto com o nome antigo mostrado na tabela
   (ex.: `coca_ab12cd.jpg`) e ela entra com certeza. **Na Vercel cada
   requisição aceita ~4,5 MB**: por isso a tela abre o ZIP no próprio
   navegador e envia **uma foto por vez** (ZIP de até 500 MB; foto maior que
   4 MB é reduzida no navegador antes). Pelo terminal (sem esse limite):
   `python manage.py restaurar_midia --zip export.zip` (aceita também fotos
   soltas e pastas; `--simular` só mostra).
4. **Quando a Cloudinary voltar** (só o dono reativa a conta):
   `python manage.py espelhar_midia` (com `DATABASE_URL` e
   `CLOUDINARY_CLOUD_NAME`) baixa cada foto antiga pelo endereço público e
   grava no banco. Pode rodar quantas vezes quiser (o que já foi copiado é
   pulado; `--limite N` copia aos poucos). Se a Cloudinary responder
   401/"disabled", para na hora e avisa. O projeto não usa crons da Vercel
   (não há `vercel.json`), então este passo é manual.

## Relatórios

- **Pedidos**: no topo, Total, Dinheiro, Pix, Cartão, Misto, Taxa do motoca,
  Bebidas e a conferência do caixa em dinheiro, obedecendo aos filtros de
  período e de pagamento. **Cada cartão abre um relatório** (valor,
  destaques, divisão em barras e a lista dos pedidos que formam o número).
  **Clicar num pedido** abre tudo dele: andamento, cliente, WhatsApp, mapa,
  itens com adicionais, desconto, taxa e total, com os botões de avançar o
  status e reimprimir. Dados: `pedidos/relatorio/<tipo>/` e
  `pedido/<id>/detalhes/` (JSON, com login). Código: `core/static/core/js/pedidos.js`.
- **Estatísticas**: os indicadores no topo e os gráficos em **abas** (Vendas,
  Pagamentos, Produtos, Bebidas, Horários, Cardápio online), cada uma com
  destaques calculados do período. A aba fica no endereço (`#bebidas`,
  `#pagamentos`...), e trocar o período mantém a aba aberta.

Bebida é todo produto cuja categoria tem no nome uma das `PALAVRAS_BEBIDAS`
("Bebidas", "Refrigerantes", "Sucos"...). O valor das bebidas é preço ×
quantidade dos itens; o desconto do pedido inteiro não é rateado. No
cardápio e no caixa a foto da bebida aparece inteira (sem corte), em fundo
branco.

## Adicionais

**Refri da marmita.** Toda marmita de R$ 10 (`MARMITA_PRECO_COM_REFRI`)
oferece, como **primeiro adicional**, o refri já cadastrado nas bebidas (o
**REFRI-MINI 200ml** de R$ 2,00): produto ativo de categoria de bebidas, com
nome de refri/suco e preço até `REFRI_MARMITA_PRECO_MAX`. Não precisa
cadastrar adicional nenhum, e a marmita de R$ 10 sem adicionais também abre
o modal. No Caixa e no cardápio ele aparece em destaque azul, com o selo
"Bebida" e a foto do produto. Escolhido, entra no carrinho **como o próprio
produto**, numa linha só dele (1 por marmita): conta nas bebidas dos
relatórios e sai separado na notinha. Desativar o produto tira o refri das
marmitas.

O adicional cadastrado que é bebida também vem antes dos outros, em
destaque. Os outros vêm depois, na ordem em que foram cadastrados. Conta
como bebida o adicional com uma das `PALAVRAS_BEBIDAS` no nome ou o nome de
um refri (Coca, Guaraná, Fanta, Sprite, Pepsi...).

A foto é a do **produto de mesmo nome** (ex.: o adicional "Coca-Cola Lata"
usa a foto do produto "Coca-Cola Lata", mesmo desativado no cardápio). Sem
produto de mesmo nome, a bebida usa o copo de refri do sistema
(`core/static/core/img/refri.svg`). Sem migração. Dados:
`produto/<id>/adicionais/`.

## Impressão das notinhas

Um motor só para o Caixa e a tela de Pedidos: `core/static/core/js/impressao.js`
(QZ Tray). Se a impressora ou o QZ Tray estiverem fora do ar, a notinha fica
numa **fila guardada no navegador** e sai sozinha quando voltarem — mesmo
depois de recarregar a página. No caixa, a engrenagem ao lado do status da
impressora escolhe a impressora, o papel (80 mm ou 58 mm) e as vias, e faz
uma impressão de teste.

## QZ Tray sem pedir permissão toda hora (grátis)

O QZ Tray só lembra a permissão de um site que se identifica com certificado.
Usamos um certificado **próprio e gratuito**; o servidor assina as requisições
(`core/qz_seguranca.py`) e o `impressao.js` usa isso sozinho. Se faltar o
certificado ou uma assinatura falhar, a impressão **continua funcionando**
(o QZ só volta a perguntar "Permitir?").

**Uma vez só (no computador de desenvolvimento):**

1. `python qz\gerar_certificado.py` cria `qz/digital-certificate.txt`,
   `qz/private-key.pem` e `qz/COLAR-NA-VERCEL.txt`.
2. Na Vercel → Settings → Environment Variables, crie `QZ_CERTIFICADO` e
   `QZ_CHAVE_PRIVADA` (ambiente **Production**) com as duas linhas do
   **mesmo** `COLAR-NA-VERCEL.txt`, sem aspas, e faça **Redeploy**.
3. Guarde uma cópia de `private-key.pem` fora do GitHub. Gerar de novo cria
   um par novo: aí as duas variáveis e o PC do caixa precisam ser refeitos.

**No PC do caixa (sempre que formatar, trocar peça ou reinstalar o QZ Tray):**

4. Dê dois cliques em `qz/configurar_caixa.bat` e digite o endereço do
   sistema. Ele baixa o certificado **do próprio site**, instala no QZ como
   `override.crt`, limpa a lista de sites bloqueados do QZ e reinicia o QZ.
   (Sem o script: engrenagem da impressora → *Baixar certificado do site*,
   copie para `C:\Program Files\QZ Tray\override.crt` e reinicie o QZ.)
5. Abra o `/caixa/`. Se o Chrome perguntar se o site pode acessar este
   computador / a rede local: **Permitir**. Na janela do QZ: marque
   **Remember this decision** e clique **Allow**.

Dica: no menu do ícone do QZ, ative *Launch at startup*.

## Impressora parou depois de trocar o PC ou a placa-mãe

A engrenagem da impressora (no Caixa e em Pedidos) tem um **Diagnóstico da
impressão** que confere cada item abaixo e diz o que fazer. Na ordem:

1. **Windows enxerga a Bematech?** Instale o driver da MP-4200 TH, ligue o
   USB e imprima a página de teste pelo Windows (Impressoras e scanners).
   Se aparecer "MP-4200 TH (Cópia 1)", a antiga ficou presa na porta USB
   da placa velha: teste as duas, fique com a que imprime e remova a outra.
2. **QZ Tray instalado e aberto** (qz.io/download), com *Launch at startup*.
3. **Chrome liberado** (Chrome 147+ pede permissão para o site falar com o
   QZ): ícone à esquerda do endereço → Configurações do site →
   *Acesso à rede local* / *Apps neste dispositivo* → **Permitir**.
   Ignorar o aviso 3 vezes conta como bloquear.
4. **Site não bloqueado no QZ**: ícone do QZ → Advanced → Site Manager →
   aba *Blocked* → remova o site (o `configurar_caixa.bat` já faz isso).
5. **Certificado**: o diagnóstico mostra se `QZ_CERTIFICADO` e
   `QZ_CHAVE_PRIVADA` estão no deploy e se formam um par; depois rode o
   passo 4 acima no PC do caixa.
6. **Escolha a impressora** na engrenagem (a salva pode ter sumido) e use
   *Imprimir teste*.

## Temas

Sol (claro), Lua (noite), Eclipse (preto com a coroa vermelha da marca) e
Automático (Sol de dia, Lua à noite), no botão do topo de todas as telas.
Arquivos: `core/static/core/css/tema.css` e `core/static/core/js/tema.js`.

## CSS (Tailwind compilado)

O CSS é gerado uma vez — nada de Tailwind compilando no navegador. Depois de
mexer em classes dos templates:

```
npx tailwindcss@3.4.17 -c tailwind.config.js -i core/static_src/entrada.css -o core/static/core/css/app.css --minify
python manage.py collectstatic --noinput
```

A pasta `staticfiles/` vai versionada: é dela que o whitenoise serve os
arquivos na Vercel, com cache longo (os links levam `?v=<versão>`).

## Testes

```
python manage.py test core
```

As fotos no banco, a rota `/midia/banco/`, o espelho da Cloudinary (com a
Cloudinary simulada: cópia e conta desativada) e a restauração estão em
`core/tests_midia.py`.
