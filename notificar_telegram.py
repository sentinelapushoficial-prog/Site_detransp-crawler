#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notificar_telegram.py — avisa no Telegram quando o portal do Detran-SP muda.

Manda a mensagem e, opcionalmente, o proprio arquivo .md da base como anexo —
assim voce baixa direto no celular e sobe no NotebookLM sem precisar entrar no
servidor.

Uso:
    python notificar_telegram.py --teste
    python notificar_telegram.py --mensagem "Deu certo"
    python notificar_telegram.py --mensagem "Base nova" --arquivo saida/base.md

Nao precisa instalar nada: usa so a biblioteca padrao do Python.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import uuid
from pathlib import Path
from urllib import request, error

# Limites do Telegram (api.telegram.org)
MAX_CARACTERES_MENSAGEM = 4000      # o limite real e 4096; deixamos folga
MAX_BYTES_ARQUIVO = 45 * 1024 * 1024  # o limite real e 50 MB


def carregar_config(caminho: str | None) -> dict:
    """Le o token e o chat_id. Variaveis de ambiente vencem do arquivo."""
    cfg: dict = {}
    p = Path(caminho or "notificacao.json")
    if p.exists():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            sys.exit(f"ERRO: o arquivo {p} nao e um JSON valido ({e}).")

    cfg["token"] = os.environ.get("TELEGRAM_TOKEN") or cfg.get("token", "")
    cfg["chat_id"] = os.environ.get("TELEGRAM_CHAT_ID") or cfg.get("chat_id", "")
    cfg.setdefault("api_base", "https://api.telegram.org")

    if not cfg["token"] or not cfg["chat_id"]:
        sys.exit(
            "ERRO: falta configurar o Telegram.\n"
            f"Crie o arquivo {p} com este conteudo:\n"
            '  {"token": "SEU_TOKEN_DO_BOTFATHER", "chat_id": "SEU_NUMERO"}\n'
            "Veja o PASSO-A-PASSO.md, secao 'Criar o bot do Telegram'."
        )
    return cfg


def _multipart(campos: dict[str, str], arquivo: Path | None,
               nome_campo: str = "document") -> tuple[bytes, str]:
    """Monta um corpo multipart/form-data na mao (evita depender do requests)."""
    limite = f"----detran{uuid.uuid4().hex}"
    partes: list[bytes] = []

    for chave, valor in campos.items():
        partes.append(
            f"--{limite}\r\n"
            f'Content-Disposition: form-data; name="{chave}"\r\n\r\n'
            f"{valor}\r\n".encode("utf-8")
        )

    if arquivo is not None:
        tipo = mimetypes.guess_type(arquivo.name)[0] or "application/octet-stream"
        partes.append(
            f"--{limite}\r\n"
            f'Content-Disposition: form-data; name="{nome_campo}"; '
            f'filename="{arquivo.name}"\r\n'
            f"Content-Type: {tipo}\r\n\r\n".encode("utf-8")
        )
        partes.append(arquivo.read_bytes())
        partes.append(b"\r\n")

    partes.append(f"--{limite}--\r\n".encode("utf-8"))
    return b"".join(partes), f"multipart/form-data; boundary={limite}"


def chamar(cfg: dict, metodo: str, campos: dict[str, str],
           arquivo: Path | None = None) -> dict:
    url = f"{cfg['api_base']}/bot{cfg['token']}/{metodo}"
    corpo, content_type = _multipart(campos, arquivo)
    req = request.Request(url, data=corpo, method="POST")
    req.add_header("Content-Type", content_type)
    try:
        with request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as e:
        detalhe = e.read().decode("utf-8", "ignore")[:400]
        if e.code == 401:
            raise SystemExit(
                "ERRO: o Telegram recusou o token. Confira se voce copiou o "
                "token inteiro do BotFather, sem espacos."
            )
        if e.code == 400 and "chat not found" in detalhe:
            raise SystemExit(
                "ERRO: chat nao encontrado. Voce precisa MANDAR UMA MENSAGEM "
                "para o seu bot no Telegram antes do primeiro envio, e conferir "
                "se o chat_id esta certo."
            )
        raise SystemExit(f"ERRO do Telegram (HTTP {e.code}): {detalhe}")
    except error.URLError as e:
        raise SystemExit(
            f"ERRO de conexao com o Telegram: {e.reason}. "
            "O servidor tem acesso a internet?"
        )


def enviar_mensagem(cfg: dict, texto: str) -> None:
    # mensagem longa vira varias; o Telegram corta em 4096 caracteres
    for i in range(0, len(texto), MAX_CARACTERES_MENSAGEM):
        pedaco = texto[i:i + MAX_CARACTERES_MENSAGEM]
        chamar(cfg, "sendMessage", {
            "chat_id": str(cfg["chat_id"]),
            "text": pedaco,
            "disable_web_page_preview": "true",
        })


def enviar_arquivo(cfg: dict, caminho: Path, legenda: str = "") -> None:
    if not caminho.exists():
        print(f"aviso: arquivo nao encontrado, pulando: {caminho}")
        return
    tamanho = caminho.stat().st_size
    if tamanho > MAX_BYTES_ARQUIVO:
        mb = tamanho / 1024 / 1024
        enviar_mensagem(cfg, (
            f"O arquivo {caminho.name} tem {mb:.0f} MB e passou do limite de "
            "50 MB do Telegram, entao nao pude anexar. Ele esta no servidor, "
            f"em: {caminho.resolve()}"
        ))
        return
    campos = {"chat_id": str(cfg["chat_id"])}
    if legenda:
        campos["caption"] = legenda[:1000]
    chamar(cfg, "sendDocument", campos, arquivo=caminho)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Envia aviso no Telegram sobre a base do Detran-SP."
    )
    ap.add_argument("--config", help="arquivo JSON com token e chat_id "
                                     "(padrao: notificacao.json)")
    ap.add_argument("--mensagem", help="texto a enviar")
    ap.add_argument("--arquivo", action="append", default=[],
                    help="arquivo a anexar (pode repetir)")
    ap.add_argument("--legenda", default="", help="legenda do anexo")
    ap.add_argument("--teste", action="store_true",
                    help="manda uma mensagem de teste e sai")
    args = ap.parse_args()

    cfg = carregar_config(args.config)

    if args.teste:
        enviar_mensagem(cfg, (
            "Teste do Sentinela SP.\n\n"
            "Se voce esta lendo isso, o aviso automatico do crawler do "
            "Detran-SP esta funcionando."
        ))
        print("Mensagem de teste enviada. Confira o Telegram.")
        return 0

    if not args.mensagem and not args.arquivo:
        ap.error("informe --mensagem, --arquivo ou --teste")

    if args.mensagem:
        enviar_mensagem(cfg, args.mensagem)
    for a in args.arquivo:
        enviar_arquivo(cfg, Path(a), args.legenda)

    print("Aviso enviado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
