# Validação executada — 21/10/2026

Worktree `primeira-versao`, branch `feat/primeira-versao`. Linux, Python 3.14.5, uv 0.11.16, ffmpeg/ffprobe 6.1.1. Sem alteração de configuração global de modelos, do sistema ou de serviços. Nenhum push, PR, merge ou deploy. Os servidores de teste usaram exclusivamente `127.0.0.1:8765` e foram encerrados.

## Suíte final

Comandos reais:

```console
$ uv sync --frozen
Checked 48 packages in 0.46ms
$ uv run pytest -q
....................................................................... [100%]
71 passed, 1 warning in 42.93s
$ uv run ruff check instazap tests scripts
All checks passed!
$ uv lock --check
Resolved 53 packages in 0.92ms
```

O único warning é a depreciação do uso de httpx no TestClient do Starlette, que sugere httpx2; não é falha de teste. O lock inclui o grupo opcional de Playwright, não instalado por `uv sync` padrão. Testes não acessam APIs/redes externas.

Os arquivos de mídia são **fixtures sintéticas de TESTE**, geradas localmente por ffmpeg (cor com marcador vermelho no final e seno de 880 Hz). Não representam downloads de Instagram/X. Asserções verificam:

| Entrada | Saída verificada |
|---|---|
| 120 s | 2 partes, 60 s cada, nenhuma acima de 60 s |
| 240 s | 4 partes, 60 s cada, nenhuma acima de 60 s |
| 60 s | 1 parte, sem arquivo adicional |
| 61,2 s | 60 s + 1,2 s |
| 119,9 s | 60 s + 59,9 s |
| 240,1 s | Rejeição antes de gerar qualquer parte |
| 2,3 s sem áudio | Uma parte sem faixa de áudio |
| Rotação 90°, vertical, SAR 2:1, 1920×1080 | Orientação física correta, pixels quadrados e redução até 1280×720 |

Todos os MP4 são inspecionados com ffprobe e decodificados isoladamente com ffmpeg: H.264/yuv420p, AAC quando aplicável, primeiro pacote de vídeo keyframe, final dos pacotes de vídeo até a fronteira esperada, duração de apresentação de áudio/vídeo/container ≤60 s. Os testes verificam pixels do marcador final vermelho e áudio decodificado no final; precisão comparada dentro da quantização de quadro/amostra (tolerância de até 34 ms para pacotes, 25 ms para duração dos fixtures, sem tolerância para exceder 60 s).

Cobertura adicional: sessão e arquivos isolados, cookie HttpOnly/Strict, segredo compartilhado, CSRF/origem, Range/preview/ZIP, exclusão/expiração física e de autorização, limites de fila/espaço, arquivo inválido, upload vazio, rejeição de **201.000.000 bytes reais transmitidos em chunks** com remoção do parcial, erros de links oferecendo upload, reinício em novas instâncias com mesmo SQLite/cookie, recuperação de processamento e upload interrompidos, cancelamento ao excluir e reenvio à fila no desligamento normal. Teste Linux confirma que matar o pai encerra o subprocesso (PR_SET_PDEATHSIG), evitando ffmpeg órfão concorrente com recuperação.

SSRF é testado sem rede real: allowlist original, esquemas e domínios secundários, privados/metadata/CGNAT/IPv6/multicast, DNS misto, byte budget, redirecionamento a metadata sem abrir socket e conexão ao IP resolvido mantendo hostname TLS. O diretor yt-dlp contém **somente** o handler restrito. Arquivos concat/HLS locais também são rejeitados por ffprobe, evitando leitura transversal entre tarefas por playlists.

## HTTP real e navegador

```console
$ uv run python scripts/smoke.py
HTTP PASS: upload → persisted queue → worker → downloads [60.0, 1.2] → delete → 404
SERVER STOPPED: 127.0.0.1:8765

$ uv run --group browser python scripts/smoke.py --browser
HTTP PASS: upload → persisted queue → worker → downloads [60.0, 1.2] → delete → 404
BROWSER PASS: 390×844, upload, 2 previews, video decode, download, reload, delete; no JS errors/overflow
SERVER STOPPED: 127.0.0.1:8765
```

O smoke inicia o comando nativo `scripts/serve.py` com dados temporários; envia vídeo sintético de 61,2 s por socket HTTP, consulta a fila, baixa os arquivos e mede durações reais. Chromium existente no ambiente foi reutilizado, sem instalação de navegador. A interface móvel foi inspecionada visualmente em `artifacts/mobile.png`; também existe `artifacts/desktop.png` (1440×1000). Capturas são artefatos locais ignorados pelo Git. O navegador confirmou decodificação da prévia, download via UI, restauração após reload e exclusão. Não foram usados cookies de contas externas.

## Dependências e Docker

```console
$ uv export --no-dev --no-emit-project --format requirements-txt --output-file requirements.txt
Resolved 53 packages in 1ms
$ uv run pip-audit -r requirements.txt --disable-pip
No known vulnerabilities found
$ docker --version
/bin/bash: line 1: docker: command not found
```

Auditoria das dependências de **produção** travadas; não audita bibliotecas nativas do sistema/ffmpeg. **Build e execução Docker NÃO TESTADOS** localmente: executável ausente, sem tentar instalar ou iniciar daemon. Dockerfile/Compose foram revisados estaticamente. CI contém build executável, mas não foi acionado remotamente. Python 3.12 da imagem/CI não foi executado neste host (teste nativo usou 3.14.5).

## Link público: NÃO VERIFICADO

A skill agent-reach foi consultada e `agent-reach doctor --json` informou backend Instagram indisponível (sem OpenCLI) e X sem autenticação. Não foi configurada nem acessada conta pessoal. Para exercitar o **próprio downloader implementado**, foi usado um exemplo público histórico presente nos testes do extrator InstagramIE instalado; disponibilidade atual desconhecida:

```console
$ uv run python scripts/check_instagram.py https://www.instagram.com/reel/Chunk8-jurw/
NOT VERIFIED: link unavailable, network restricted, login required, or extractor blocked. Use upload.
```

Exit code 1. Não houve evidência de download bem-sucedido; a mensagem genérica não permite atribuir com precisão a falha a exclusão do post, rede do ambiente, plataforma ou extrator. Não houve tentativa de contornar restrições, usar cookies pessoais ou fornecedor pago. Upload manual continua sendo o caminho validado. **X não teve teste público real.**

## Limitações restantes

- Validar os MP4 no WhatsApp em Android/iPhone reais; smoke Chromium não certifica aceitação no aplicativo.
- Extração Instagram/X e Docker precisam de validação independente no host de publicação. Nada foi publicado por esta execução.
- Retenção bloqueia acesso no prazo; remoção física pode esperar uma tarefa em andamento e não é apagamento forense. Arquivos já baixados ou streams já abertos não podem ser revogados.
- Conversão com perdas para 30 fps; AAC tem priming/padding de codec, e a duração de apresentação é sinalizada no MP4. Formatos/metadados inconsistentes podem ser recusados; não há promessa de cobrir todos os codecs do ffmpeg.
- Execução nativa não tem o cgroup de memória/CPU do Compose; volume sem quota tem orçamento lógico, não limite físico do filesystem. Recomendado somente uso privado de baixo volume.
