# Crawler do portal Detran-SP → fonte única para NotebookLM

Extrai o conteúdo público do portal do Detran-SP e gera **um arquivo Markdown consolidado**, pronto para subir como uma única fonte no NotebookLM (Gemini Notebook) e alimentar o Sentinela SP. Quando o portal muda, avisa no Telegram e manda o arquivo junto.

> **Não é técnico?** Não leia este arquivo — abra o **`PASSO-A-PASSO.md`**. Ele leva do zero até receber a base no celular, sem pressupor conhecimento prévio. Este README aqui é a referência técnica.

## Por que não dá para usar `requests` + BeautifulSoup

O portal novo (`www.detran.sp.gov.br/detransp`) é uma aplicação JavaScript sobre o ServiceNow Service Portal. O HTML bruto que o servidor devolve contém só um `<div>Carregando...</div>` — o conteúdo real só existe depois que o navegador executa o JS. Por isso o crawler usa **Playwright com Chromium headless**: ele abre a página de verdade, espera a renderização, abre acordeões e abas fechadas, e só então extrai o texto.

## Instalação

```bash
cd detran-sp-crawler
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium   # Linux; pede sudo
```

Requer Python 3.10+. Em Windows, use `.venv\Scripts\activate` e ignore o `install-deps`.

## Uso

**1. Primeiro, mapeie sem gravar nada.** Este passo é importante: as URLs iniciais no script são as prováveis, mas o Detran muda a estrutura do portal de tempos em tempos.

```bash
python detran_crawler.py --descobrir --max-paginas 60 -v
```

Você verá a árvore do que foi encontrado, por categoria, com contagem de palavras. Se vier vazio ou pobre, ajuste as sementes:

```bash
python detran_crawler.py --descobrir --sementes \
  "https://www.detran.sp.gov.br/detransp?id=detran_sp_home" \
  "https://www.detran.sp.gov.br/detransp/pb/servicos/veiculos?id=veiculos"
```

**2. Rode a captura completa.**

```bash
cp config.exemplo.json config.json   # opcional, para fixar suas preferências
python detran_crawler.py --config config.json
```

**3. Suba no NotebookLM.** O arquivo `saida/detran-sp-base-conhecimento-AAAA-MM-DD.md` é a fonte. No NotebookLM: *Adicionar fonte → Fazer upload*.

### Execuções seguintes

```bash
python detran_crawler.py --config config.json --incremental
```

Com `--incremental`, se nada mudou no portal o script não gera arquivo novo. Se mudou, ele grava a base atualizada **e** um `mudancas-AAAA-MM-DD.md` listando o que entrou, o que saiu e o que foi alterado — esse changelog é útil por si só para o Sentinela.

## O que sai na pasta `saida/`

| Arquivo | Para que serve |
|---|---|
| `detran-sp-base-conhecimento-DATA.md` | **A fonte do NotebookLM.** Índice no topo, conteúdo agrupado por seção, cada bloco com a URL de origem |
| `documentos.jsonl` | Mesmo conteúdo em JSON, uma linha por documento — para pipeline/RAG do Sentinela |
| `mudancas-DATA.md` | Diff em relação à captura anterior |
| `estado.json` | Hashes da última captura (é o que permite o modo incremental) |
| `falhas.json` | URLs que não puderam ser lidas, com o motivo |
| `crawler.log` | Log completo |

O arquivo `.md` traz a URL de origem em cada bloco, então o NotebookLM consegue citar de onde veio cada resposta — que é o ponto principal para uso jurídico.

### Limite do NotebookLM

Cada fonte aceita até **500.000 palavras** (e 200 MB). O script corta automaticamente em `-parte1`, `-parte2`… se passar de 450 mil palavras, sempre em fronteira de documento — nunca no meio de um texto, e cada parte sai com cabeçalho próprio. Se isso acontecer, suba cada parte como uma fonte separada.

### Se o crawl for interrompido

Se o script bater no teto de `max_paginas` ou a rede cair no meio, ele avisa no log e **não** sobrescreve o `estado.json` inteiro: mescla com a captura anterior e suprime as "remoções" do changelog. Sem isso, a execução seguinte reportaria centenas de páginas como novas e removidas ao mesmo tempo. O aviso `crawl interrompido pelo teto de N páginas` é o sinal de que você precisa aumentar `max_paginas`.

## Escopo capturado

Por padrão entram: **serviços** (habilitação/CNH, veículos, infrações e multas, taxas), **legislação e atos normativos** (inclusive PDFs de portarias, com o texto extraído) e **unidades e canais de atendimento**.

Notícias e sala de imprensa ficam **de fora** por padrão — envelhecem rápido e poluem a base. Para incluir: `--incluir-noticias`.

## Agendamento

### systemd (recomendado em servidor Linux)

```bash
chmod +x rodar.sh
sudo cp agendamento/detran-crawler.service /etc/systemd/system/detran-crawler@.service
sudo cp agendamento/detran-crawler.timer   /etc/systemd/system/detran-crawler@.timer
sudo systemctl daemon-reload
sudo systemctl enable --now detran-crawler@$USER.timer

systemctl list-timers detran-crawler*     # confere o próximo disparo
sudo systemctl start detran-crawler@$USER # dispara agora, para testar
journalctl -u detran-crawler@$USER -f     # acompanha
```

O timer vem configurado para **segunda-feira às 4h**. Para diário, edite `OnCalendar` no `.timer`.

### cron (mais simples)

```bash
crontab -e
```

```cron
# toda segunda às 4h
0 4 * * 1 /home/SEU_USUARIO/detran-sp-crawler/rodar.sh
```

O `rodar.sh` já ativa o venv, escreve em `logs/execucoes.log` e mantém só as 6 capturas mais recentes.

### GitHub Actions (sem servidor, sem custo)

O repositório traz `.github/workflows/captura.yml`: roda semanalmente em runner do GitHub, notifica no Telegram e commita `estado.json` de volta para manter o incremental entre execuções. Configure `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID` em *Settings → Secrets and variables → Actions*. Repositório privado tem 2.000 minutos/mês no plano Free — folgado para uma captura semanal.

### Aviso no Telegram

`notificar_telegram.py` usa só a biblioteca padrão (sem `requests`). Lê `notificacao.json` ou as variáveis `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID`, que têm precedência.

```bash
python notificar_telegram.py --teste
python notificar_telegram.py --mensagem "texto" --arquivo saida/base.md
```

O `rodar.sh` decide sozinho o que enviar: base + changelog quando há mudança, aviso único na primeira captura (controlado pelo marcador `saida/.primeiro-aviso-enviado`), últimas linhas do log quando o crawl falha, e silêncio quando nada mudou. Mensagens acima de 4.000 caracteres são fatiadas; arquivos acima de 45 MB não são anexados — o bot informa o caminho no servidor.

### Fluxo com o NotebookLM

O NotebookLM não tem API pública de upload, então o último passo é manual: baixar o `.md` que chegou no Telegram, remover a fonte antiga do notebook e subir a nova.

## Ajustes finos

Tudo no `config.json` (ou nas flags):

- `max_paginas` — teto de segurança. 1500 é folgado para o portal; comece com 100 para testar
- `pausa_entre_requisicoes_s` — 1 segundo por padrão. **Não reduza muito**: o objetivo é não pesar no servidor de um órgão público
- `concorrencia` — 3 abas em paralelo; cada aba consome ~200 MB de RAM
- `max_profundidade` — 5 níveis a partir das sementes
- `categorias` — as regras regex que decidem em qual seção do arquivo final cada página cai. Ajuste se quiser um recorte diferente
- `bloqueios` — padrões de URL que nunca entram

## Cuidados

O crawler respeita `robots.txt` por padrão (`respeitar_robots`), se identifica com um User-Agent próprio e espera 1 segundo entre requisições. Ele coleta **apenas conteúdo público**, sem login e sem consultar dados de terceiros — nada de CPF, placa ou CNH.

Sobre o uso do material: as páginas do Detran-SP são informativas e mudam sem aviso. Para o Sentinela SP, a URL de origem gravada em cada bloco é o que permite conferir a informação antes de usar. Legislação vale pelo texto oficial publicado no Diário Oficial, não pelo resumo do portal.

## Se der errado

**Nenhum documento extraído** — o portal provavelmente mudou as URLs. Rode `--descobrir -v` e veja `falhas.json`.

**Timeout em muitas páginas** — aumente `timeout_pagina_ms` para 60000 e baixe a `concorrencia` para 1.

**Páginas vindo vazias ou só com menu** — o seletor de conteúdo não pegou. Rode com `-v`, abra uma dessas URLs no navegador, veja qual container tem o texto e acrescente o seletor na lista `CANDIDATOS` dentro de `JS_EXTRAIR` no script.

**PDF sem texto** — portarias antigas às vezes são imagem escaneada. O script pula esses (registra em `falhas.json`); extrair exigiria OCR (Tesseract).
