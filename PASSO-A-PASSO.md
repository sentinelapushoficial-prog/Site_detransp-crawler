# Passo a passo — do zero até receber a base no Telegram

Escrito para quem nunca mexeu com isso. Não pule etapas, e não tem problema ir devagar.

## O que você vai montar

Um robô que, uma vez por semana, entra no portal do Detran-SP, lê todas as páginas públicas, monta um arquivo único com tudo, e **te manda esse arquivo no Telegram** — mas só quando alguma coisa mudou. Você baixa o arquivo no celular e sobe no NotebookLM.

São duas partes: primeiro o Telegram (10 minutos), depois o lugar onde o robô mora (30 minutos).

Sobre onde o robô mora, você tem dois caminhos:

- **Caminho A — GitHub.** É de graça, funciona no navegador, você não instala nada e não mantém máquina nenhuma. **Recomendo este.**
- **Caminho B — servidor alugado (VPS).** Custa uns 20 a 40 reais por mês e exige digitar comandos numa tela preta. Só vale se você já tem um servidor ou quer controle total.

Faça a Parte 1 e depois escolha A **ou** B. Não precisa dos dois.

---

# PARTE 1 — Criar o bot do Telegram

O bot é uma conta de robô que te manda mensagem. Criar é de graça e leva 5 minutos.

### 1.1 — Criar o bot

1. Abra o Telegram no celular.
2. Na busca, procure por **@BotFather** (com a marca de verificado azul) e abra a conversa.
3. Toque em **Iniciar** (ou digite `/start`).
4. Digite `/newbot` e envie.
5. Ele pergunta o nome do bot. Digite algo como `Sentinela SP` e envie.
6. Ele pergunta o nome de usuário, que **precisa terminar em `bot`**. Tente `sentinela_sp_ffd_bot`. Se disser que já existe, invente outro (acrescente números).
7. Ele responde com uma mensagem contendo uma linha parecida com esta:

   ```
   7712345678:AAHk3x9dQwErTyUiOpAsDfGhJkLzXcVbNm0
   ```

   **Esse é o seu token.** Copie e guarde num bloco de notas.

> **Cuidado:** o token é a senha do robô. Quem tem ele pode mandar mensagem no seu lugar. Nunca cole em grupo, print ou site público. Se vazar, volte no BotFather e use `/revoke`.

### 1.2 — Falar com o bot uma vez

O Telegram não deixa um bot mandar mensagem para quem nunca falou com ele.

1. No BotFather, a mesma mensagem tem um link tipo `t.me/sentinela_sp_ffd_bot`. Toque nele.
2. Abre a conversa com o **seu** bot. Toque em **Iniciar**.
3. Mande qualquer coisa, um `oi` serve.

### 1.3 — Descobrir o seu número de chat

Esse número diz ao robô para quem mandar as mensagens.

1. No navegador (celular ou computador), abra este endereço, **trocando `SEU_TOKEN` pelo token que você copiou**:

   ```
   https://api.telegram.org/botSEU_TOKEN/getUpdates
   ```

   Fica parecido com:
   `https://api.telegram.org/bot7712345678:AAHk3x.../getUpdates`

   Repare que é `bot` colado no token, sem barra e sem espaço.

2. Vai aparecer um monte de texto. Procure o trecho `"chat":{"id":` — o número logo depois é o seu.

   ```
   "chat":{"id":123456789,"first_name":"Felipe",...
                 ^^^^^^^^^
   ```

3. Anote esse número junto com o token.

**Se aparecer `{"ok":true,"result":[]}` (vazio):** você não mandou mensagem para o bot ainda. Volte ao passo 1.2, mande um `oi`, e recarregue a página.

**Atalho:** se preferir, procure **@userinfobot** no Telegram, mande `/start`, e ele responde com o seu Id direto. Dá no mesmo.

Ao fim da Parte 1 você tem dois valores anotados:

| O quê | Se parece com |
|---|---|
| Token | `7712345678:AAHk3x9dQwErTyUiOpAsDfGhJkLzXcVbNm0` |
| Chat ID | `123456789` |

---

# CAMINHO A — Rodar no GitHub (de graça, sem servidor)

O GitHub é um site onde se guarda código. Ele tem um recurso que executa tarefas em horário marcado, numa máquina deles, sem custo. É onde o seu robô vai morar.

### A.1 — Criar a conta

1. Acesse **github.com** e clique em **Sign up**.
2. Cadastre com e-mail, senha e um nome de usuário.
3. Confirme o e-mail que eles enviam.
4. Quando perguntarem o plano, escolha **Free**.

### A.2 — Criar o repositório

Repositório é só uma pasta na nuvem.

1. Clique no **+** no canto superior direito → **New repository**.
2. Em *Repository name*, escreva `detran-sp-crawler`.
3. Marque **Private**.
4. Clique em **Create repository**.

> **Público ou privado?** O conteúdo é público do Detran, então nenhum dos dois expõe segredo (o token fica guardado à parte, protegido, nos dois casos). A diferença é cota: repositório privado tem **2.000 minutos de execução por mês** no plano Free, e público é ilimitado. Uma captura semanal consome bem menos que isso, então **privado atende**. Se algum dia estourar, é só trocar para público em *Settings → General → Change visibility*.

### A.3 — Subir os arquivos

1. Descompacte o `detran-sp-crawler.zip` que eu te mandei, no seu computador.
2. Na página do repositório recém-criado, clique em **uploading an existing file** (o link no meio da tela).
3. Abra a pasta descompactada, selecione **todos** os arquivos e arraste para a área indicada no navegador.
4. Espere terminar de subir e clique no botão verde **Commit changes**.

**Confira se o arquivo do agendamento subiu.** Ele fica numa pasta escondida e às vezes o navegador não a envia. Clique na aba **Actions** lá em cima:

- Se aparecer **"Captura do Detran-SP"**, deu certo, pule para A.4.
- Se aparecer uma tela oferecendo modelos de workflow, faça assim:
  1. Clique em **set up a workflow yourself**.
  2. No campo do nome do arquivo, no topo, apague o que estiver escrito e digite exatamente: `captura.yml` (o `.github/workflows/` já vem preenchido antes dele).
  3. Apague todo o conteúdo da caixa de texto.
  4. Abra o arquivo `.github/workflows/captura.yml` da pasta descompactada num editor de texto simples (Bloco de Notas no Windows, TextEdit no Mac), copie tudo e cole na caixa.
  5. Clique em **Commit changes** duas vezes.

### A.4 — Guardar o token com segurança

O token **não** vai junto com os arquivos. Ele fica num cofre do GitHub.

1. No repositório, clique em **Settings** (a engrenagem, na barra de cima).
2. No menu da esquerda, desça até **Secrets and variables** e clique em **Actions**.
3. Clique em **New repository secret**.
4. Em *Name* escreva exatamente `TELEGRAM_TOKEN` (maiúsculas e sublinhado, sem espaço).
5. Em *Secret*, cole o token do BotFather.
6. Clique em **Add secret**.
7. Repita para o segundo: **New repository secret**, nome `TELEGRAM_CHAT_ID`, valor o número do chat, **Add secret**.

Você deve terminar com dois segredos listados. Depois de salvos, nem você consegue vê-los de novo — se errar, apague e crie outra vez.

### A.5 — Primeiro teste

1. Clique na aba **Actions**.
2. Na coluna da esquerda, clique em **Captura do Detran-SP**.
3. À direita aparece **Run workflow** — clique e depois no botão verde **Run workflow**.
4. Recarregue a página. Vai aparecer uma linha com uma bolinha amarela (rodando).
5. **Vá tomar um café.** A primeira captura demora bastante, entre 20 minutos e 2 horas, porque o robô visita o portal inteiro devagar para não sobrecarregar o servidor do Detran.
6. Quando terminar, a bolinha fica verde e **você recebe a mensagem no Telegram com o arquivo anexado**.

A partir daqui está tudo no automático: toda segunda-feira às 4h da manhã ele repete, e só te manda mensagem quando o portal mudar.

### A.6 — Se der errado

Bolinha vermelha significa erro. Clique nela, depois em **capturar**, e procure a etapa marcada com X.

| O que aparece no log | O que fazer |
|---|---|
| `falta configurar o Telegram` | Os segredos não foram criados ou o nome está errado. Refaça A.4 conferindo letra por letra. |
| `Nenhum documento extraido` | O Detran mudou os endereços das páginas. Me avise que eu ajusto os endereços iniciais. |
| `chat nao encontrado` | Você não mandou mensagem para o bot. Faça o passo 1.2. |
| `o Telegram recusou o token` | O token foi copiado pela metade. Refaça A.4. |
| Muitos `timeout` no log | O portal estava lento. Rode de novo mais tarde; se insistir, me avise. |

Quer trocar o horário? Abra o arquivo `.github/workflows/captura.yml` pelo próprio GitHub, clique no lápis, e mude a linha do `cron`. Lembre que o GitHub trabalha em UTC: **some 3 horas** ao horário de Brasília. Toda segunda às 4h da manhã daqui é `0 7 * * 1`. Para rodar todo dia às 5h, use `0 8 * * *`.

---

# CAMINHO B — Rodar num servidor alugado (VPS)

Só siga isto se **não** fez o Caminho A.

### B.1 — Contratar

Você precisa de uma máquina Linux com **Ubuntu 24.04**, pelo menos **2 GB de memória** (o robô abre um navegador de verdade, e navegador come memória) e uns 20 GB de disco. Provedores conhecidos: Hostinger, Contabo, Hetzner, DigitalOcean, Vultr, Magalu Cloud. Preços mudam com frequência — confira no site na hora, mas a faixa dessa configuração costuma ficar entre 20 e 60 reais por mês.

Prefira um servidor **no Brasil** se o provedor oferecer. Sites do governo às vezes tratam conexões vindas de fora com mais desconfiança.

Ao contratar, anote: o **endereço IP** da máquina, o **usuário** (quase sempre `root`) e a **senha**.

### B.2 — Conectar

- **Windows:** abra o menu Iniciar, digite `powershell`, e na tela azul digite `ssh root@SEU_IP` e Enter.
- **Mac:** abra o **Terminal** (Cmd+Espaço, digite "terminal") e o mesmo comando.

Na primeira vez ele pergunta se você confia — digite `yes`. Depois pede a senha: **digite normalmente, a tela não mostra nada enquanto você digita**, isso é proposital. Enter.

### B.3 — Instalar

Cole os comandos abaixo **um bloco de cada vez**, esperando cada um terminar. Para colar no PowerShell, use o botão direito do mouse.

```bash
apt update && apt install -y python3 python3-pip python3-venv git unzip
```

```bash
mkdir -p ~/detran-sp-crawler && cd ~/detran-sp-crawler
```

Agora suba os arquivos do zip para essa pasta. O jeito mais fácil é instalar o programa **FileZilla** no seu computador, conectar com Host `sftp://SEU_IP`, seu usuário e senha, porta 22, e arrastar os arquivos para `/root/detran-sp-crawler`.

Com os arquivos lá, volte ao terminal:

```bash
cd ~/detran-sp-crawler
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium
```

Esse último passo demora uns minutos — ele baixa um navegador inteiro.

### B.4 — Configurar o Telegram

```bash
cd ~/detran-sp-crawler
cp notificacao.exemplo.json notificacao.json
nano notificacao.json
```

Abre um editor simples. Troque `COLE_AQUI_O_TOKEN...` pelo seu token e `COLE_AQUI_O_SEU_NUMERO...` pelo chat ID, **mantendo as aspas**. Para sair: `Ctrl+O`, Enter, `Ctrl+X`.

Teste:

```bash
python notificar_telegram.py --teste
```

Deve chegar uma mensagem no seu Telegram. Se não chegar, a mensagem de erro na tela diz o motivo.

### B.5 — Primeira captura

```bash
cp config.exemplo.json config.json
chmod +x rodar.sh
./rodar.sh
```

Demora de 20 minutos a 2 horas. Se você fechar o terminal no meio, o processo morre — para evitar, rode assim: `nohup ./rodar.sh &` e pode fechar tranquilo.

### B.6 — Agendar

```bash
crontab -e
```

Se perguntar qual editor, escolha o número do `nano`. Vá até o fim do arquivo e cole esta linha:

```
0 4 * * 1 /root/detran-sp-crawler/rodar.sh
```

Salve com `Ctrl+O`, Enter, `Ctrl+X`. Pronto: toda segunda às 4h ele roda sozinho.

Para conferir depois se rodou: `tail -n 40 ~/detran-sp-crawler/logs/execucoes.log`

---

# PARTE 3 — Usar no NotebookLM

Quando o arquivo chegar no Telegram:

1. Toque no arquivo na conversa e baixe.
2. Abra **notebooklm.google.com** e entre com sua conta Google.
3. Clique em **Criar novo** (ou abra o notebook do Sentinela SP, se já tiver um).
4. Clique em **Adicionar fonte** → **Fazer upload** → escolha o arquivo `.md`.
5. Espere processar. Pronto, pode perguntar.

**Nas próximas vezes**, quando chegar uma base nova: apague a fonte antiga (três pontinhos ao lado dela → Excluir) e suba a nova. Se deixar as duas, o NotebookLM vai misturar informação velha com nova.

Uma coisa que vale saber: cada bloco da base tem o endereço da página de origem. Então dá para pedir *"me diga onde no site do Detran está escrito isso"* e ele devolve o link — útil quando você precisa conferir antes de usar em peça ou parecer.

Se algum dia chegarem **dois ou três arquivos** com `-parte1`, `-parte2` no nome, é porque a base passou do limite de 500 mil palavras por fonte do NotebookLM. Suba cada parte como uma fonte separada.

---

# Perguntas que costumam aparecer

**Preciso deixar o computador ligado?**
No Caminho A, não — roda nos computadores do GitHub. No Caminho B, o servidor alugado fica ligado sozinho.

**Vou receber mensagem toda semana?**
Não. Só quando o portal mudar de verdade. Semanas sem mudança são silenciosas — e silêncio é sinal de que está funcionando.

**E se eu quiser rodar agora, fora do horário?**
Caminho A: aba **Actions** → **Captura do Detran-SP** → **Run workflow**. Caminho B: conecte no servidor e rode `./rodar.sh`.

**Isso é legal? Posso ter problema?**
O robô lê apenas páginas públicas, sem senha e sem consultar dado de ninguém — nada de CPF, placa ou CNH. Ele respeita o arquivo `robots.txt` (as regras que o próprio site publica sobre robôs), se identifica com nome e seu e-mail de contato, e espera 1 segundo entre cada página justamente para não pesar no servidor de um órgão público. Não mexa nessa pausa para deixar mais rápido.

**Posso confiar no que a base responder?**
Trate como ponto de partida, não como palavra final. São páginas informativas do Detran, que mudam sem aviso e às vezes ficam desatualizadas em relação à norma. Por isso cada bloco carrega o endereço de origem: para uso profissional, confira na fonte. E legislação vale pelo texto oficial publicado no Diário Oficial, não pelo resumo do portal.

**Quanto vai custar?**
Caminho A: nada. Caminho B: só a mensalidade do servidor.
