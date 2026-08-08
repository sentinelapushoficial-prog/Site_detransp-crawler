#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
detran_crawler.py — Extrator de conteudo publico do portal do Detran-SP.

Gera um arquivo-fonte unico em Markdown, pronto para ser carregado como UMA
fonte no NotebookLM (Gemini Notebook) e usado pelo Sentinela SP.

O portal novo do Detran-SP (www.detran.sp.gov.br/detransp) e uma aplicacao
JavaScript (ServiceNow Service Portal): o conteudo nao existe no HTML bruto,
so aparece depois que o navegador executa o JS. Por isso o crawler usa
Playwright/Chromium em modo headless em vez de requests+BeautifulSoup.

Uso basico:
    python detran_crawler.py --descobrir          # so mapeia e mostra a arvore
    python detran_crawler.py                      # crawl completo + gera a fonte
    python detran_crawler.py --incremental        # so recaptura o que mudou

Autor: gerado para o projeto Sentinela SP.
Licenca: uso livre.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import logging
import os
import re
import sys
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET

try:
    from playwright.async_api import async_playwright, Error as PWError
except ImportError:  # pragma: no cover
    sys.exit(
        "Falta o Playwright. Instale com:\n"
        "    pip install -r requirements.txt\n"
        "    playwright install chromium"
    )

try:
    from markdownify import markdownify as html_to_md
except ImportError:  # pragma: no cover
    html_to_md = None

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    try:
        from PyPDF2 import PdfReader  # type: ignore
    except ImportError:
        PdfReader = None


VERSAO = "1.2.0"
FUSO_BR = timezone(timedelta(hours=-3))

# NotebookLM aceita ate 500.000 palavras por fonte. Cortamos com folga.
MAX_PALAVRAS_POR_ARQUIVO = 450_000

# Marcadores invisiveis usados so para achar fronteiras seguras de corte.
# Sao removidos antes de gravar o arquivo final.
MARCA_SECAO = "<!--CORTE-SECAO-->"
MARCA_DOC = "<!--CORTE-DOC-->"


# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------

CONFIG_PADRAO: dict = {
    "dominios_permitidos": [
        "www.detran.sp.gov.br",
        "detran.sp.gov.br",
    ],
    # Paginas por onde o crawl comeca. O portal e uma SPA, entao partimos de
    # pontos conhecidos e deixamos o crawler seguir os links renderizados.
    "sementes": [
        "https://www.detran.sp.gov.br/detransp?id=detran_sp_home",
        "https://www.detran.sp.gov.br/detransp/pb/todos_os_servicos?id=todos_os_servicos",
        "https://www.detran.sp.gov.br/detransp/pb/servicos/habilitacao?id=habilitacao",
        "https://www.detran.sp.gov.br/detransp/pb/servicos/veiculos?id=veiculos",
        "https://www.detran.sp.gov.br/detransp/pb/servicos/infracoes?id=infracoes",
        "https://www.detran.sp.gov.br/detransp/pb/unidades?id=unidades",
        "https://www.detran.sp.gov.br/detransp/pb/legislacao?id=legislacao",
        "https://www.detran.sp.gov.br/detransp/pb/institucional?id=institucional",
    ],
    "sitemaps": [
        "https://www.detran.sp.gov.br/sitemap.xml",
        "https://www.detran.sp.gov.br/detransp/sitemap.xml",
    ],
    # Categorias: a primeira regex que casar com a URL ou o titulo define a
    # secao no arquivo final. Ordem importa.
    "categorias": [
        {"nome": "Habilitacao (CNH)", "padroes": [r"habilita", r"\bcnh\b", r"permissao.?para.?dirigir", r"renach"]},
        {"nome": "Veiculos", "padroes": [r"veiculo", r"licenciamento", r"crlv", r"transferenc", r"emplacament", r"vistoria"]},
        {"nome": "Infracoes e Multas", "padroes": [r"infrac", r"multa", r"recurso", r"pontuacao", r"suspens", r"cassac", r"jari"]},
        {"nome": "Legislacao e Atos Normativos", "padroes": [r"legislac", r"portaria", r"resoluc", r"deliberac", r"norma", r"instrucao.?normativa", r"decreto", r"\bctb\b"]},
        {"nome": "Unidades e Atendimento", "padroes": [r"unidade", r"ciretran", r"poupatempo", r"atendiment", r"agendament", r"fale.?conosco", r"ouvidoria", r"telefone", r"endereco"]},
        {"nome": "Taxas e Pagamentos", "padroes": [r"taxa", r"pagament", r"guia", r"gare", r"ipva", r"dpvat", r"boleto"]},
        {"nome": "Servicos Digitais", "padroes": [r"servico", r"digital", r"aplicativo", r"login", r"cadastr"]},
        {"nome": "Institucional", "padroes": [r"institucional", r"sobre", r"quem.?somos", r"transparenc", r"acesso.?a.?informacao", r"lgpd"]},
    ],
    # URLs que nunca entram na base.
    "bloqueios": [
        r"/logout", r"/login", r"\?.*sysparm_", r"/sys_attachment",
        r"/api/", r"/xmlhttp", r"\.(css|js|png|jpe?g|gif|svg|ico|woff2?|ttf|zip|xlsx?|docx?)($|\?)",
        r"/noticias?/", r"/imprensa", r"/sala-de-imprensa",  # noticias fora do escopo
        r"javascript:", r"mailto:", r"tel:", r"whatsapp",
        r"facebook\.com", r"twitter\.com", r"instagram\.com", r"youtube\.com", r"linkedin\.com",
    ],
    # Parametros de URL irrelevantes que geram duplicatas.
    "parametros_ignorados": [
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "gclid", "fbclid", "_ga", "spa", "sysparm_nameofstack",
    ],
    "baixar_pdfs": True,
    "max_paginas": 1500,
    "max_profundidade": 5,
    "concorrencia": 3,
    "pausa_entre_requisicoes_s": 1.0,
    "timeout_pagina_ms": 45000,
    "espera_rede_ociosa_ms": 3500,
    "respeitar_robots": True,
    "user_agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36 SentinelaSP-Crawler/" + VERSAO
        + " (coleta de conteudo publico; contato: ffdasilva1990@gmail.com)"
    ),
    "min_palavras_pagina": 40,
    "pasta_saida": "saida",
    "nome_base_saida": "detran-sp-base-conhecimento",
}


def carregar_config(caminho: str | None) -> dict:
    cfg = json.loads(json.dumps(CONFIG_PADRAO))  # deep copy
    if caminho:
        p = Path(caminho)
        if not p.exists():
            # Nao aborta: o agendamento chama sempre com --config, e sem arquivo
            # os padroes ja sao suficientes para uma captura completa.
            log.warning(
                "config %s nao encontrada; seguindo com as configuracoes padrao",
                p,
            )
            return cfg
        try:
            usuario = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            sys.exit(f"A config {p} nao e um JSON valido: {e}")
        # chaves iniciadas por _ sao comentarios do arquivo de exemplo
        cfg.update({k: v for k, v in usuario.items() if not k.startswith("_")})
    return cfg


# ---------------------------------------------------------------------------
# Utilitarios
# ---------------------------------------------------------------------------

log = logging.getLogger("detran")


def configurar_log(verboso: bool, arquivo: Path | None = None) -> None:
    nivel = logging.DEBUG if verboso else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if arquivo:
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(arquivo, encoding="utf-8"))
    logging.basicConfig(level=nivel, format=fmt, handlers=handlers, force=True)


def sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def agora_br() -> datetime:
    return datetime.now(FUSO_BR)


def canonizar_host(netloc: str, canonicos: list[str]) -> str:
    """www.detran.sp.gov.br e detran.sp.gov.br sao o mesmo site. Sem isso,
    a mesma pagina entra duas vezes na fila e queima o teto de paginas."""
    n = netloc.lower()
    base = n[4:] if n.startswith("www.") else n
    for c in canonicos:
        cl = c.lower()
        cb = cl[4:] if cl.startswith("www.") else cl
        if base == cb:
            return cl
    return n


def normalizar_url(
    url: str,
    base: str,
    parametros_ignorados: list[str],
    hosts_canonicos: list[str] | None = None,
) -> str | None:
    """Resolve, limpa e canoniza uma URL. Devolve None se nao for http(s)."""
    if not url:
        return None
    url = url.strip()
    if url.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    absoluta = urljoin(base, url)
    p = urlparse(absoluta)
    if p.scheme not in ("http", "https"):
        return None

    # remove fragmento e parametros de rastreio, preservando repetidos
    pares = [
        (k, v) for k, v in parse_qsl(p.query, keep_blank_values=False)
        if k not in parametros_ignorados
    ]
    query = urlencode(sorted(pares))

    caminho = re.sub(r"/{2,}", "/", p.path) or "/"
    if caminho != "/" and caminho.endswith("/"):
        caminho = caminho[:-1]

    netloc = canonizar_host(p.netloc, hosts_canonicos or [])
    # O portal serve tudo em https; fixar o esquema evita a mesma pagina entrar
    # duas vezes na fila como http:// e https://. Host com porta explicita
    # (ambiente de teste, homologacao) mantem o esquema original.
    esquema = p.scheme if ":" in netloc else "https"
    return urlunparse((esquema, netloc, caminho, "", query, ""))


def eh_pdf(url: str) -> bool:
    return urlparse(url).path.lower().endswith(".pdf")


def hash_texto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8", "ignore")).hexdigest()[:16]


def contar_palavras(texto: str) -> int:
    return len(texto.split())


def slug(texto: str, limite: int = 60) -> str:
    s = sem_acento(texto).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:limite] or "sem-titulo"


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------

@dataclass
class Documento:
    url: str
    titulo: str
    categoria: str
    tipo: str                 # "pagina" | "pdf"
    texto_md: str
    trilha: list[str] = field(default_factory=list)
    capturado_em: str = ""
    hash_conteudo: str = ""
    palavras: int = 0
    profundidade: int = 0

    def finalizar(self) -> "Documento":
        self.capturado_em = agora_br().isoformat(timespec="seconds")
        # Titulo entra no hash de proposito: paginas diferentes do portal
        # reaproveitam blocos de texto identicos, e sem o titulo a deduplicacao
        # descartaria conteudo legitimo.
        self.hash_conteudo = hash_texto(self.titulo + "\n" + self.texto_md)
        self.palavras = contar_palavras(self.texto_md)
        return self


# ---------------------------------------------------------------------------
# Extracao de conteudo dentro do navegador
# ---------------------------------------------------------------------------

# Roda no contexto da pagina ja renderizada. Remove cromo do site (menus,
# rodape, banners de cookie) e devolve o HTML do bloco de conteudo principal.
JS_EXTRAIR = r"""
() => {
  const LIXO = [
    'script','style','noscript','iframe','svg','nav','header','footer',
    '[role="navigation"]','[role="banner"]','[role="contentinfo"]',
    '.navbar','.nav','.menu','.sidebar','.breadcrumb','.breadcrumbs',
    '.cookie','.cookies','#cookie','.lgpd','.modal','.offcanvas',
    '.sp-nav','.sp-menu','.header','.footer','.skip','.sr-only',
    '.social','.compartilhe','.compartilhar','.chatbot','.chat',
    '[aria-hidden="true"]','.libras','.vlibras','.barra-gov'
  ];

  // clone para nao estragar a pagina viva
  const doc = document.cloneNode(true);
  LIXO.forEach(sel => doc.querySelectorAll(sel).forEach(n => n.remove()));

  const CANDIDATOS = [
    'main','[role="main"]','#sp-page','.sp-page-root','.container-fluid main',
    '#conteudo','#content','.conteudo','.content','article','.page-content',
    '.sp-instance','.body-content'
  ];

  let melhor = null, melhorTam = 0;
  for (const sel of CANDIDATOS) {
    for (const el of doc.querySelectorAll(sel)) {
      const t = (el.innerText || '').trim().length;
      if (t > melhorTam) { melhorTam = t; melhor = el; }
    }
  }
  if (!melhor || melhorTam < 200) melhor = doc.body;

  // trilha de navegacao (breadcrumb) pega do documento vivo
  const trilha = Array.from(
    document.querySelectorAll('.breadcrumb li, .breadcrumbs li, [aria-label="breadcrumb"] li')
  ).map(li => (li.innerText || '').trim()).filter(Boolean);

  // acordeoes/abas fechadas costumam esconder o conteudo util: revela tudo
  melhor.querySelectorAll('[hidden]').forEach(n => n.removeAttribute('hidden'));
  melhor.querySelectorAll('.collapse:not(.show)').forEach(n => n.classList.add('show'));

  const links = Array.from(document.querySelectorAll('a[href]'))
    .map(a => a.getAttribute('href'))
    .filter(Boolean);

  return {
    titulo: (document.querySelector('h1')?.innerText
             || document.title || '').trim(),
    html: melhor.innerHTML,
    texto: (melhor.innerText || '').trim(),
    trilha,
    links
  };
}
"""

# Alguns menus do portal so revelam os links depois de um clique.
JS_ABRIR_MENUS = r"""
async () => {
  // So elementos que expandem conteudo. Clicar em <a href> navegaria a pagina
  // e o texto capturado acabaria atribuido a URL errada.
  const alvos = document.querySelectorAll(
    'button[data-toggle="collapse"], button[data-bs-toggle="collapse"], ' +
    '.accordion-button, [role="button"][aria-expanded="false"], ' +
    'button[aria-expanded="false"], summary'
  );
  let n = 0;
  for (const el of alvos) {
    if (el.closest('a[href]')) continue;
    const h = el.getAttribute('href');
    if (h && h !== '#' && !h.startsWith('#')) continue;
    try { el.click(); n++; } catch (e) {}
    if (n > 60) break;
  }
  return n;
}
"""


# ---------------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------------

class Crawler:
    def __init__(self, cfg: dict, incremental: bool = False):
        self.cfg = cfg
        self.incremental = incremental
        self.saida = Path(cfg["pasta_saida"])
        self.saida.mkdir(parents=True, exist_ok=True)

        self.vistos: set[str] = set()
        self.hashes: set[str] = set()
        self.docs: list[Documento] = []
        self.falhas: list[dict] = []
        self.robots: dict[str, RobotFileParser] = {}

        self.re_bloqueios = [re.compile(p, re.I) for p in cfg["bloqueios"]]
        self.cat_compiladas = [
            (c["nome"], [re.compile(p, re.I) for p in c["padroes"]])
            for c in cfg["categorias"]
        ]

        self.estado_path = self.saida / "estado.json"
        self.estado_anterior = self._carregar_estado()
        # o crawl so pode sobrescrever o estado se tiver terminado inteiro
        self.crawl_completo = False

    def norm(self, url: str, base: str) -> str | None:
        return normalizar_url(
            url, base,
            self.cfg["parametros_ignorados"],
            self.cfg["dominios_permitidos"],
        )

    # -- estado / incremental ------------------------------------------------

    def _carregar_estado(self) -> dict:
        if self.estado_path.exists():
            try:
                return json.loads(self.estado_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("estado.json corrompido; ignorando.")
        return {"paginas": {}, "gerado_em": None}

    def _salvar_estado(self) -> None:
        atuais = {
            d.url: {
                "hash": d.hash_conteudo,
                "titulo": d.titulo,
                "categoria": d.categoria,
                "palavras": d.palavras,
                "capturado_em": d.capturado_em,
            }
            for d in self.docs
        }
        if self.crawl_completo:
            paginas = atuais
        else:
            # Crawl interrompido (teto de paginas, rede caindo): sobrescrever o
            # estado inteiro faria a proxima execucao reportar centenas de
            # paginas como "removidas". Mescla preservando o que nao foi visto.
            paginas = dict(self.estado_anterior.get("paginas", {}))
            paginas.update(atuais)
            log.warning(
                "crawl incompleto: estado mesclado com a captura anterior "
                "(changelog de remocoes fica suprimido nesta execucao)"
            )
        estado = {
            "gerado_em": agora_br().isoformat(timespec="seconds"),
            "versao_crawler": VERSAO,
            "crawl_completo": self.crawl_completo,
            "paginas": paginas,
        }
        self.estado_path.write_text(
            json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def diff_com_anterior(self) -> dict:
        antes = self.estado_anterior.get("paginas", {})
        agora = {d.url: d.hash_conteudo for d in self.docs}
        novas = [u for u in agora if u not in antes]
        # so afirmamos que algo sumiu se o crawl varreu o site inteiro
        removidas = [u for u in antes if u not in agora] if self.crawl_completo else []
        alteradas = [
            u for u in agora
            if u in antes and antes[u].get("hash") != agora[u]
        ]
        return {"novas": novas, "alteradas": alteradas, "removidas": removidas}

    # -- filtros -------------------------------------------------------------

    def dominio_ok(self, url: str) -> bool:
        return urlparse(url).netloc.lower() in {
            d.lower() for d in self.cfg["dominios_permitidos"]
        }

    def bloqueada(self, url: str) -> bool:
        return any(r.search(url) for r in self.re_bloqueios)

    async def carregar_robots(self, contexto) -> None:
        """Busca os robots.txt uma unica vez, no inicio, pelo cliente HTTP do
        Playwright. RobotFileParser.read() usa urlopen sem timeout: chamado de
        dentro do event loop, um servidor lento travaria o crawler para sempre."""
        if not self.cfg["respeitar_robots"]:
            return
        for dominio in self.cfg["dominios_permitidos"]:
            esq = "http" if ":" in dominio else "https"
            raiz = f"{esq}://{dominio.lower()}"
            rp = RobotFileParser()
            rp.set_url(f"{raiz}/robots.txt")
            try:
                resp = await contexto.request.get(f"{raiz}/robots.txt", timeout=15000)
                if resp.ok:
                    rp.parse((await resp.text()).splitlines())
                    log.info("robots.txt carregado de %s", raiz)
                else:
                    rp.allow_all = True
                    log.info("robots.txt ausente em %s (HTTP %s); seguindo liberado",
                             raiz, resp.status)
            except Exception as e:
                rp.allow_all = True
                log.info("robots.txt inacessivel em %s (%s); seguindo liberado", raiz, e)
            self.robots[dominio.lower()] = rp

    def robots_ok(self, url: str) -> bool:
        if not self.cfg["respeitar_robots"]:
            return True
        rp = self.robots.get(urlparse(url).netloc.lower())
        if rp is None:
            return True  # dominio sem robots carregado: nao bloqueia
        try:
            return rp.can_fetch(self.cfg["user_agent"], url)
        except Exception:
            return True

    def aceitar(self, url: str) -> bool:
        return (
            self.dominio_ok(url)
            and not self.bloqueada(url)
            and self.robots_ok(url)
        )

    def categorizar(self, url: str, titulo: str, trilha: list[str]) -> str:
        alvo = sem_acento(f"{url} {titulo} {' '.join(trilha)}").lower()
        for nome, padroes in self.cat_compiladas:
            if any(p.search(alvo) for p in padroes):
                return nome
        return "Outros conteudos"

    # -- descoberta via sitemap ---------------------------------------------

    async def urls_do_sitemap(self, contexto) -> list[str]:
        achadas: list[str] = []
        for sm in self.cfg["sitemaps"]:
            try:
                resp = await contexto.request.get(sm, timeout=20000)
                if not resp.ok:
                    log.debug("sitemap %s -> HTTP %s", sm, resp.status)
                    continue
                corpo = await resp.text()
                raiz = ET.fromstring(corpo)
                ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
                for loc in raiz.iterfind(".//s:loc", ns):
                    if loc.text:
                        achadas.append(loc.text.strip())
                log.info("sitemap %s: %d URLs", sm, len(achadas))
            except Exception as e:
                log.debug("sitemap %s falhou: %s", sm, e)
        return achadas

    # -- PDFs ----------------------------------------------------------------

    async def extrair_pdf(self, contexto, url: str, profundidade: int) -> Documento | None:
        if PdfReader is None:
            log.warning("pypdf ausente; PDF ignorado: %s", url)
            return None
        try:
            resp = await contexto.request.get(url, timeout=60000)
            if not resp.ok:
                self.falhas.append({"url": url, "erro": f"HTTP {resp.status}"})
                return None
            dados = await resp.body()
            leitor = PdfReader(io.BytesIO(dados))
            partes = []
            for pagina in leitor.pages:
                try:
                    partes.append(pagina.extract_text() or "")
                except Exception:
                    continue
            texto = re.sub(r"\n{3,}", "\n\n", "\n\n".join(partes)).strip()
            if contar_palavras(texto) < self.cfg["min_palavras_pagina"]:
                log.debug("PDF sem texto extraivel (provavel imagem): %s", url)
                return None
            titulo = ""
            try:
                titulo = (leitor.metadata.title or "").strip()
            except Exception:
                pass
            titulo = titulo or Path(urlparse(url).path).stem.replace("_", " ").replace("-", " ").title()
            doc = Documento(
                url=url,
                titulo=titulo,
                categoria=self.categorizar(url, titulo, []),
                tipo="pdf",
                texto_md=texto,
                profundidade=profundidade,
            ).finalizar()
            return doc
        except Exception as e:
            self.falhas.append({"url": url, "erro": f"pdf: {e}"})
            return None

    # -- paginas -------------------------------------------------------------

    async def extrair_pagina(self, pagina, url: str, profundidade: int) -> tuple[Documento | None, list[str]]:
        try:
            await pagina.goto(url, wait_until="domcontentloaded",
                              timeout=self.cfg["timeout_pagina_ms"])
            try:
                await pagina.wait_for_load_state(
                    "networkidle", timeout=self.cfg["espera_rede_ociosa_ms"]
                )
            except PWError:
                pass  # SPA com polling nunca fica ociosa; segue mesmo assim

            # o Service Portal costuma pintar o conteudo em duas etapas
            await pagina.wait_for_timeout(1200)
            antes = pagina.url
            try:
                await pagina.evaluate(JS_ABRIR_MENUS)
                await pagina.wait_for_timeout(600)
            except PWError:
                pass
            # se algum clique navegou apesar do filtro, volta: extrair aqui
            # gravaria o texto de outra pagina sob esta URL
            if pagina.url != antes:
                log.debug("clique navegou (%s -> %s); recarregando", antes, pagina.url)
                await pagina.goto(url, wait_until="domcontentloaded",
                                  timeout=self.cfg["timeout_pagina_ms"])
                await pagina.wait_for_timeout(1500)

            dados = await pagina.evaluate(JS_EXTRAIR)
            url_final = pagina.url
        except Exception as e:
            self.falhas.append({"url": url, "erro": str(e)[:200]})
            log.warning("falhou %s -> %s", url, str(e)[:120])
            return None, []

        # base = URL efetiva apos redirect/roteamento da SPA, nao a enfileirada
        links = [
            u for u in (self.norm(h, url_final) for h in dados.get("links", []))
            if u
        ]

        texto_bruto = dados.get("texto") or ""
        if contar_palavras(texto_bruto) < self.cfg["min_palavras_pagina"]:
            log.debug("pagina magra, descartada: %s", url)
            return None, links

        html = dados.get("html") or ""
        if html_to_md:
            md = html_to_md(
                html,
                heading_style="ATX",
                strip=["img", "figure", "picture", "input", "button", "form"],
                bullets="-",
            )
        else:
            md = texto_bruto

        titulo = (dados.get("titulo") or "").strip() or url
        md = self._limpar_md(md)
        md = self._ajustar_headings(md, titulo)
        trilha = [t for t in dados.get("trilha", []) if t]

        doc = Documento(
            url=url,
            titulo=titulo,
            categoria=self.categorizar(url, titulo, trilha),
            tipo="pagina",
            texto_md=md,
            trilha=trilha,
            profundidade=profundidade,
        ).finalizar()
        return doc, links

    @staticmethod
    def _limpar_md(md: str) -> str:
        md = re.sub(r"\n{3,}", "\n\n", md)
        md = re.sub(r"[ \t]{2,}", " ", md)
        # linhas de ruido tipicas de portal publico
        ruido = re.compile(
            r"^\s*(pular para o conte|carregando|voltar ao topo|compartilhe|"
            r"acessibilidade|alto contraste|aumentar fonte|diminuir fonte|"
            r"selecione o idioma|cookies?)\b",
            re.I,
        )
        linhas = [l for l in md.split("\n") if not ruido.match(sem_acento(l))]
        return "\n".join(linhas).strip()

    @staticmethod
    def _ajustar_headings(md: str, titulo: str) -> str:
        """O titulo da pagina ja vira '## ' no arquivo final. Rebaixa os
        headings do corpo para H3+ e remove o H1 repetido logo no inicio."""
        linhas = md.split("\n")
        alvo = sem_acento(titulo).strip().lower()
        saida: list[str] = []
        primeiro_visto = False
        for l in linhas:
            m = re.match(r"^(#{1,6})\s+(.*)$", l)
            if m:
                texto = m.group(2).strip()
                if not primeiro_visto and sem_acento(texto).lower() == alvo:
                    primeiro_visto = True
                    continue  # descarta o H1 duplicado
                primeiro_visto = True
                nivel = min(len(m.group(1)) + 2, 6)
                saida.append("#" * nivel + " " + texto)
            else:
                saida.append(l)
        return re.sub(r"\n{3,}", "\n\n", "\n".join(saida)).strip()

    # -- laco principal ------------------------------------------------------

    async def rodar(self, apenas_descobrir: bool = False) -> None:
        cfg = self.cfg
        fila: list[tuple[str, int]] = []

        async with async_playwright() as pw:
            navegador = await pw.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage", "--no-sandbox"],
            )
            contexto = await navegador.new_context(
                user_agent=cfg["user_agent"],
                locale="pt-BR",
                viewport={"width": 1366, "height": 900},
                ignore_https_errors=True,
            )
            # nao baixa imagem/fonte/midia: crawl ~3x mais rapido
            await contexto.route(
                re.compile(r"\.(png|jpe?g|gif|webp|svg|ico|woff2?|ttf|mp4|webm)($|\?)", re.I),
                lambda rota: asyncio.ensure_future(rota.abort()),
            )

            await self.carregar_robots(contexto)

            for u in await self.urls_do_sitemap(contexto):
                n = self.norm(u, u)
                if n and self.aceitar(n) and n not in self.vistos:
                    self.vistos.add(n)
                    fila.append((n, 0))

            for s in cfg["sementes"]:
                n = self.norm(s, s)
                if n and n not in self.vistos:
                    self.vistos.add(n)
                    fila.append((n, 0))

            log.info("fila inicial: %d URLs", len(fila))

            n_abas = max(1, int(cfg["concorrencia"]))
            paginas_pool = [await contexto.new_page() for _ in range(n_abas)]
            processadas = 0
            t0 = time.time()

            while fila and processadas < cfg["max_paginas"]:
                # uma aba pode ter morrido (crash de renderer, falta de memoria);
                # sem isso, 1/N das URLs restantes falharia em silencio
                for i, pg in enumerate(paginas_pool):
                    if pg.is_closed():
                        log.warning("aba %d morreu; recriando", i)
                        paginas_pool[i] = await contexto.new_page()

                lote = [fila.pop(0) for _ in range(min(len(fila), len(paginas_pool)))]

                async def tratar(item, pagina):
                    url, prof = item
                    if eh_pdf(url):
                        if not cfg["baixar_pdfs"]:
                            return None, []
                        d = await self.extrair_pdf(contexto, url, prof)
                        return d, []
                    return await self.extrair_pagina(pagina, url, prof)

                resultados = await asyncio.gather(
                    *(tratar(item, paginas_pool[i]) for i, item in enumerate(lote)),
                    return_exceptions=True,
                )

                for (url, prof), res in zip(lote, resultados):
                    processadas += 1
                    if isinstance(res, Exception):
                        self.falhas.append({"url": url, "erro": repr(res)[:200]})
                        continue
                    doc, links = res

                    if doc and not apenas_descobrir:
                        if doc.hash_conteudo in self.hashes:
                            log.debug("duplicata ignorada: %s", url)
                        else:
                            self.hashes.add(doc.hash_conteudo)
                            self.docs.append(doc)
                    elif doc and apenas_descobrir:
                        self.docs.append(doc)

                    if prof < cfg["max_profundidade"]:
                        for l in links:
                            if l not in self.vistos and self.aceitar(l):
                                self.vistos.add(l)
                                fila.append((l, prof + 1))

                if processadas % 20 < len(lote):
                    log.info(
                        "%d processadas | %d docs | fila %d | %.0fs",
                        processadas, len(self.docs), len(fila), time.time() - t0,
                    )
                await asyncio.sleep(cfg["pausa_entre_requisicoes_s"])

            # so consideramos a varredura completa se a fila esgotou sozinha
            self.crawl_completo = not fila and processadas < cfg["max_paginas"]
            if not self.crawl_completo:
                log.warning(
                    "crawl interrompido pelo teto de %d paginas; ainda restam %d na fila. "
                    "Aumente --max-paginas para uma base completa.",
                    cfg["max_paginas"], len(fila),
                )

            await navegador.close()

        log.info(
            "fim do crawl: %d paginas visitadas, %d documentos, %d falhas, %.0fs",
            processadas, len(self.docs), len(self.falhas), time.time() - t0,
        )


# ---------------------------------------------------------------------------
# Geracao do arquivo-fonte
# ---------------------------------------------------------------------------

AVISO = (
    "> **Como ler esta base.** Conteudo publico do portal do Detran-SP capturado "
    "automaticamente na data indicada. Cada bloco traz a URL de origem: use-a para "
    "conferir a informacao antes de qualquer uso oficial. Regras de transito e "
    "procedimentos mudam; em caso de divergencia, vale o que esta no site do orgao "
    "e na legislacao vigente."
)


def montar_markdown(docs: list[Documento], meta: dict) -> str:
    por_cat: dict[str, list[Documento]] = defaultdict(list)
    for d in docs:
        por_cat[d.categoria].append(d)
    for lista in por_cat.values():
        lista.sort(key=lambda d: (d.trilha, d.titulo.lower()))

    ordem = [c["nome"] for c in CONFIG_PADRAO["categorias"]]
    cats = sorted(por_cat, key=lambda c: (ordem.index(c) if c in ordem else 99, c))

    out: list[str] = []
    out.append("---")
    out.append(f"titulo: Base de conhecimento Detran-SP")
    out.append(f"fonte: {meta['fonte']}")
    out.append(f"capturado_em: {meta['capturado_em']}")
    out.append(f"documentos: {meta['documentos']}")
    out.append(f"palavras: {meta['palavras']}")
    out.append(f"gerado_por: detran_crawler.py v{VERSAO}")
    out.append("---\n")

    out.append("# Base de conhecimento — Detran-SP (conteudo publico)\n")
    out.append(AVISO + "\n")
    pal = f"{meta['palavras']:,}".replace(",", ".")
    out.append(
        f"Captura de **{meta['capturado_em']}** — {meta['documentos']} documentos, "
        f"{pal} palavras, em {len(cats)} secoes.\n"
    )

    out.append("## Sumario\n")
    for c in cats:
        n = len(por_cat[c])
        out.append(f"- **{c}** ({n} documento{'s' if n != 1 else ''})")
        for d in por_cat[c][:400]:
            out.append(f"  - {d.titulo}")
    out.append("")

    for c in cats:
        out.append("\n" + MARCA_SECAO)
        out.append(f"# SECAO: {c}\n")
        for d in por_cat[c]:
            out.append(f"\n{MARCA_DOC}")
            out.append(f"## {d.titulo}\n")
            out.append(f"- **URL de origem:** {d.url}")
            out.append(f"- **Secao:** {c}")
            if d.trilha:
                out.append(f"- **Caminho no site:** {' > '.join(d.trilha)}")
            out.append(f"- **Tipo:** {'PDF' if d.tipo == 'pdf' else 'Pagina do portal'}")
            out.append(f"- **Capturado em:** {d.capturado_em}\n")
            out.append(d.texto_md.strip())
            out.append("")

    return "\n".join(out)


def _limpar_marcas(texto: str) -> str:
    return texto.replace(MARCA_SECAO + "\n", "").replace(MARCA_DOC + "\n", "")


def dividir_se_preciso(texto: str, base: Path) -> list[Path]:
    """NotebookLM aceita 500 mil palavras por fonte. Corta em partes se passar.

    O corte usa marcadores proprios (MARCA_SECAO/MARCA_DOC) em vez de uma
    linha '---': o markdownify converte qualquer <hr> do portal em '---', e
    cortar ali partiria um documento ao meio, deixando o trecho seguinte sem
    titulo nem URL de origem.
    """
    if contar_palavras(texto) <= MAX_PALAVRAS_POR_ARQUIVO:
        base.write_text(_limpar_marcas(texto), encoding="utf-8")
        return [base]

    # cabecalho = tudo antes da primeira secao (front matter, aviso, sumario)
    corte = texto.find(MARCA_SECAO)
    cabecalho, corpo = texto[:corte], texto[corte:]

    # blocos de secao; os grandes demais viram blocos de documento
    blocos: list[str] = []
    for sec in corpo.split(MARCA_SECAO):
        if not sec.strip():
            continue
        if contar_palavras(sec) <= MAX_PALAVRAS_POR_ARQUIVO:
            blocos.append(sec)
            continue
        pedacos = sec.split(MARCA_DOC)
        titulo_secao = pedacos[0]
        blocos.append(titulo_secao)
        for doc in pedacos[1:]:
            if doc.strip():
                blocos.append(MARCA_DOC + doc)

    partes: list[Path] = []
    atual: list[str] = []
    n_palavras = 0
    idx = 1

    def gravar(corpo_parte: str, i: int) -> Path:
        p = base.with_name(f"{base.stem}-parte{i}{base.suffix}")
        # cada parte carrega o cabecalho: sozinha, ela precisa se explicar
        topo = cabecalho if i == 1 else (
            f"# Base de conhecimento — Detran-SP (parte {i})\n\n{AVISO}\n\n"
            f"> Continuacao de `{base.stem}-parte{i - 1}{base.suffix}`.\n"
        )
        p.write_text(_limpar_marcas(topo + corpo_parte), encoding="utf-8")
        return p

    for b in blocos:
        pb = contar_palavras(b)
        if n_palavras + pb > MAX_PALAVRAS_POR_ARQUIVO and atual:
            partes.append(gravar("".join(atual), idx))
            idx += 1
            atual, n_palavras = [], 0
        if pb > MAX_PALAVRAS_POR_ARQUIVO:
            log.warning(
                "um unico documento tem %d palavras e excede o limite de %d por "
                "fonte do NotebookLM; ficou numa parte propria",
                pb, MAX_PALAVRAS_POR_ARQUIVO,
            )
        atual.append(b)
        n_palavras += pb
    if atual:
        partes.append(gravar("".join(atual), idx))
    return partes


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Extrai o conteudo publico do portal do Detran-SP para uma "
                    "fonte unica de NotebookLM / Sentinela SP."
    )
    ap.add_argument("--config", help="arquivo JSON de configuracao")
    ap.add_argument("--descobrir", action="store_true",
                    help="so mapeia o site e mostra a arvore, sem gerar a base")
    ap.add_argument("--incremental", action="store_true",
                    help="compara com a captura anterior e gera changelog")
    ap.add_argument("--max-paginas", type=int, help="teto de paginas visitadas")
    ap.add_argument("--saida", help="pasta de saida")
    ap.add_argument("--sementes", nargs="*", help="URLs iniciais (substitui as padrao)")
    ap.add_argument("--dominios", nargs="*", help="dominios permitidos (substitui os padrao)")
    ap.add_argument("--sitemaps", nargs="*", help="URLs de sitemap (substitui as padrao)")
    ap.add_argument("--incluir-noticias", action="store_true",
                    help="nao filtra as paginas de noticias/imprensa")
    ap.add_argument("-v", "--verboso", action="store_true")
    args = ap.parse_args()

    cfg = carregar_config(args.config)
    if args.max_paginas:
        cfg["max_paginas"] = args.max_paginas
    if args.saida:
        cfg["pasta_saida"] = args.saida
    if args.sementes:
        cfg["sementes"] = args.sementes
    if args.dominios:
        cfg["dominios_permitidos"] = args.dominios
    if args.sitemaps is not None:
        cfg["sitemaps"] = args.sitemaps
    if args.incluir_noticias:
        cfg["bloqueios"] = [
            b for b in cfg["bloqueios"]
            if "notici" not in b and "imprensa" not in b
        ]

    pasta = Path(cfg["pasta_saida"])
    pasta.mkdir(parents=True, exist_ok=True)
    configurar_log(args.verboso, pasta / "crawler.log")

    log.info("detran_crawler v%s | escopo: %s", VERSAO, cfg["dominios_permitidos"])

    crawler = Crawler(cfg, incremental=args.incremental)
    asyncio.run(crawler.rodar(apenas_descobrir=args.descobrir))

    if not crawler.docs:
        log.error(
            "Nenhum documento extraido. Verifique conectividade, ajuste as sementes "
            "com --sementes ou rode com -v para ver o motivo das falhas."
        )
        return 1

    if args.descobrir:
        print("\n=== ARVORE DESCOBERTA ===")
        por_cat: dict[str, list[Documento]] = defaultdict(list)
        for d in crawler.docs:
            por_cat[d.categoria].append(d)
        for c in sorted(por_cat):
            print(f"\n## {c}  ({len(por_cat[c])})")
            for d in sorted(por_cat[c], key=lambda x: x.url):
                print(f"  [{d.palavras:>5} pal] {d.titulo[:70]}\n            {d.url}")
        print(f"\nTotal: {len(crawler.docs)} documentos, "
              f"{sum(d.palavras for d in crawler.docs)} palavras.")
        print("Nada foi gravado (modo --descobrir).")
        return 0

    diff = crawler.diff_com_anterior()
    houve_mudanca = bool(diff["novas"] or diff["alteradas"] or diff["removidas"])

    if (args.incremental and crawler.estado_anterior.get("gerado_em")
            and not houve_mudanca):
        log.info("Nada mudou desde a captura anterior; base mantida como esta.")
        print("\n=== SEM MUDANCAS ===")
        print("  O portal nao mudou desde a ultima captura. "
              "Nenhum arquivo novo foi gerado.")
        return 0

    crawler._salvar_estado()

    meta = {
        "fonte": "Portal Detran-SP — https://www.detran.sp.gov.br/detransp",
        "capturado_em": agora_br().strftime("%d/%m/%Y %H:%M"),
        "documentos": len(crawler.docs),
        "palavras": sum(d.palavras for d in crawler.docs),
    }
    md = montar_markdown(crawler.docs, meta)

    carimbo = agora_br().strftime("%Y-%m-%d")
    destino = pasta / f"{cfg['nome_base_saida']}-{carimbo}.md"
    arquivos = dividir_se_preciso(md, destino)

    (pasta / "documentos.jsonl").write_text(
        "\n".join(json.dumps(asdict(d), ensure_ascii=False) for d in crawler.docs),
        encoding="utf-8",
    )
    (pasta / "falhas.json").write_text(
        json.dumps(crawler.falhas, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if crawler.estado_anterior.get("gerado_em"):
        linhas = [
            f"# Mudancas desde {crawler.estado_anterior['gerado_em']}",
            f"\nCaptura atual: {meta['capturado_em']}\n",
            f"- Novas: {len(diff['novas'])}",
            f"- Alteradas: {len(diff['alteradas'])}",
            f"- Removidas: {len(diff['removidas'])}\n",
        ]
        for rotulo, chave in (("NOVAS", "novas"), ("ALTERADAS", "alteradas"), ("REMOVIDAS", "removidas")):
            if diff[chave]:
                linhas.append(f"\n## {rotulo}")
                linhas += [f"- {u}" for u in diff[chave]]
        (pasta / f"mudancas-{carimbo}.md").write_text("\n".join(linhas), encoding="utf-8")
        log.info("changelog: +%d novas, ~%d alteradas, -%d removidas",
                 len(diff["novas"]), len(diff["alteradas"]), len(diff["removidas"]))

    print("\n=== PRONTO ===")
    for a in arquivos:
        n = f"{contar_palavras(a.read_text(encoding='utf-8')):,}".replace(",", ".")
        print(f"  fonte NotebookLM : {a}  ({n} palavras)")
    print(f"  dados estruturados: {pasta / 'documentos.jsonl'}")
    print(f"  falhas           : {len(crawler.falhas)} (ver falhas.json)")
    if len(arquivos) > 1:
        print("\n  ATENCAO: a base passou de 450 mil palavras e foi dividida.")
        print("  Suba cada parte como uma fonte separada no NotebookLM.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
