# Cantinho Família — sistema do restaurante

Django 5.2 · PDV (caixa), pedidos, estoque, cardápio do dia, estatísticas e
cardápio online para o cliente. Hospedado na Vercel, banco no Supabase e
imagens na Cloudinary.

## Funcionamento

Segunda a sexta, das 11h às 16h. A regra mora em `core/horario.py` e vale
para o servidor, o cardápio do cliente e o painel.

## Variáveis de ambiente

| Variável | Para quê | Padrão |
| --- | --- | --- |
| `DATABASE_URL` | banco (Supabase) | SQLite local |
| `SECRET_KEY` | segurança do Django | **defina em produção** |
| `DEBUG` | `False` em produção | `True` |
| `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` | fotos dos produtos | sem elas, as fotos ficam no disco (desenvolvimento) |
| `WHATSAPP_LOJA` | número que recebe os pedidos do cardápio (só dígitos, com 55 + DDD) | `5511999999999` (**troque**) |
| `IMPRESSAO_API_KEY` | chave do agente de impressão externo | a chave antiga, para não parar o agente |
| `LOJA_ABRE` / `LOJA_FECHA` | horário (hora cheia) | `11` / `16` |
| `DB_CONN_MAX_AGE` | segundos que a função reaproveita a conexão | `60` |
| `PALAVRAS_BEBIDAS` | palavras que fazem uma categoria contar como bebida nos relatórios (separadas por vírgula) | `bebida,refri,suco,cerveja,drink` |

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
(`core/qz_seguranca.py`) e o `impressao.js` usa isso sozinho.

**Uma vez só (no computador de desenvolvimento):**

1. `python qz\gerar_certificado.py` cria `qz/digital-certificate.txt`,
   `qz/private-key.pem` e `qz/COLAR-NA-VERCEL.txt`.
2. Na Vercel, crie as variáveis `QZ_CERTIFICADO` e `QZ_CHAVE_PRIVADA` com os
   valores de `COLAR-NA-VERCEL.txt` e faça um novo deploy (`git push`).
3. Guarde uma cópia de `private-key.pem` fora do GitHub. Se perder, gere de
   novo e repita os passos 2 e 4.

**No PC do caixa (sempre que instalar/reinstalar o QZ Tray):**

4. Dê dois cliques em `qz/configurar_caixa.bat` (instala o certificado no QZ e reinicia).
5. Abra o `/caixa/`; na janela do QZ marque **Remember this decision** e **Allow**.

Dica: no menu do ícone do QZ, ative *Launch at startup*.

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
