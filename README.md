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

## Impressão das notinhas

Um motor só para o Caixa e a tela de Pedidos: `core/static/core/js/impressao.js`
(QZ Tray). Se a impressora ou o QZ Tray estiverem fora do ar, a notinha fica
numa **fila guardada no navegador** e sai sozinha quando voltarem — mesmo
depois de recarregar a página. No caixa, a engrenagem ao lado do status da
impressora escolhe a impressora, o papel (80 mm ou 58 mm) e as vias, e faz
uma impressão de teste.

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
