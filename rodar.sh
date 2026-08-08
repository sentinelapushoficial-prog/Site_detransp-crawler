#!/usr/bin/env bash
# Executa a captura do Detran-SP e avisa no Telegram se algo mudou.
# Este e o arquivo que o agendamento chama. Voce nao precisa edita-lo,
# a nao ser que queira mudar o caminho do projeto.

set -uo pipefail

PROJETO="${PROJETO:-$HOME/detran-sp-crawler}"
cd "$PROJETO" || { echo "Pasta do projeto nao encontrada: $PROJETO"; exit 1; }

# usa o ambiente Python isolado, se existir
if [ -d "$PROJETO/.venv" ]; then
  # shellcheck disable=SC1091
  source "$PROJETO/.venv/bin/activate"
fi

# em servidor sem venv, so existe 'python3'; dentro do venv existe 'python'
PY="$(command -v python || command -v python3)"
[ -z "$PY" ] && { echo "Python nao encontrado."; exit 1; }

mkdir -p "$PROJETO/logs" "$PROJETO/saida"
CARIMBO="$(date +%Y-%m-%d_%H%M)"
LOG="$PROJETO/logs/execucoes.log"

avisar() {
  # nao deixa uma falha de notificacao derrubar a execucao inteira
  "$PY" notificar_telegram.py "$@" >> "$LOG" 2>&1 \
    || echo "[$CARIMBO] aviso do Telegram falhou (veja acima)" >> "$LOG"
}

echo "" >> "$LOG"
echo "===== [$CARIMBO] iniciando captura =====" >> "$LOG"

"$PY" detran_crawler.py --config config.json --incremental >> "$LOG" 2>&1
STATUS=$?

# Pegamos o changelog mais recente por data de modificacao, e nao pelo nome com
# a data de hoje: o crawler nomeia os arquivos no horario de Brasilia e o
# servidor costuma rodar em UTC, entao perto da meia-noite as datas divergem.
CHANGELOG="$(find "$PROJETO/saida" -name 'mudancas-*.md' -newermt '-2 hours' \
             -print 2>/dev/null | head -n1)"
BASE="$(ls -1t "$PROJETO"/saida/detran-sp-base-conhecimento-*.md 2>/dev/null | head -n1)"

if [ $STATUS -ne 0 ]; then
  # falhou: manda as ultimas linhas do log para voce saber o que houve
  DETALHE="$(tail -n 15 "$LOG")"
  avisar --mensagem "Sentinela SP — a captura do Detran-SP FALHOU em $CARIMBO.

Ultimas linhas do log:

$DETALHE"

elif [ -f "$CHANGELOG" ]; then
  # mudou alguma coisa: avisa e manda a base nova junto
  RESUMO="$(head -n 8 "$CHANGELOG")"
  NOVAS="$(grep -c '^- http' "$CHANGELOG" 2>/dev/null || echo 0)"

  avisar --mensagem "Sentinela SP — o portal do Detran-SP mudou.

$RESUMO

Total de paginas afetadas: $NOVAS
A base atualizada vai anexada abaixo. Baixe e substitua a fonte no NotebookLM."

  [ -n "$BASE" ] && avisar --arquivo "$BASE" \
    --legenda "Base do Detran-SP. Suba como fonte no NotebookLM."
  avisar --arquivo "$CHANGELOG" --legenda "Lista do que mudou nesta captura."

elif [ -n "$BASE" ] && [ ! -f "$PROJETO/saida/.primeiro-aviso-enviado" ]; then
  # Primeira captura: ainda nao existe changelog. Sem este aviso, a estreia do
  # agendamento parece uma falha silenciosa. O arquivo-marca garante que este
  # aviso saia UMA vez so (checar a idade da base repetiria o aviso a cada run).
  avisar --mensagem "Sentinela SP — primeira captura do Detran-SP concluida.

A base vai anexada abaixo. Suba no NotebookLM como fonte.
A partir de agora voce so recebe aviso quando o portal mudar."
  avisar --arquivo "$BASE" \
    --legenda "Base do Detran-SP. Suba como fonte no NotebookLM."
  touch "$PROJETO/saida/.primeiro-aviso-enviado"

else
  # rodou bem e nada mudou: silencio, so registra no log
  echo "[$CARIMBO] sem mudancas; nenhum aviso enviado" >> "$LOG"
fi

echo "===== [$CARIMBO] fim (status $STATUS) =====" >> "$LOG"

# guarda apenas as 6 capturas mais recentes, para nao lotar o disco
ls -1t "$PROJETO"/saida/detran-sp-base-conhecimento-*.md 2>/dev/null \
  | tail -n +7 | xargs -r rm --

exit $STATUS
