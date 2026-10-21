# Validação: primeira parte e feedback — 21/10/2026

Worktree `primeira-parte-e-feedback`, branch `feat/primeira-parte-e-feedback`, a partir do último `origin/main` consultado (`770f82f`). Execução nesta sessão compartilhada, sem alteração de configuração global, infraestrutura ou serviços. Nenhum push, PR, merge de publicação ou deploy. Preview ativo em 8765 preservado; smoke isolado em `127.0.0.1:8766`, encerrado pelo próprio script.

## Resultados atuais

| Comando executado | Resultado |
|---|---|
| `uv sync --frozen` | Sucesso com o lock existente |
| `uv run ruff check instazap tests scripts` | All checks passed! |
| `uv run pytest -q` | **140 passed, 1 warning in 80.21s** |
| `uv run --group browser python scripts/smoke.py --browser` | HTTP PASS e BROWSER PASS; servidor encerrado |
| `uv run pip-audit -r requirements.txt --disable-pip` | No known vulnerabilities found |

O warning é a depreciação já existente de httpx no TestClient do Starlette. Chromium em cache reutilizado; nenhuma instalação de navegador. Capturas locais em `artifacts/mobile.png` e `artifacts/desktop.png`, ignoradas pelo Git, foram inspecionadas. O smoke permite `--port`; o padrão agora é 8766 para não disputar a porta do preview.

## Cortes e persistência

Fixtures sintéticas reais de baixa resolução, geradas por ffmpeg, verificam primeira de 30 s com entradas 120, 240, 30, 60, 91,2, 29,9 e 90 s. Saídas respectivas: `30+60+30`, `30+60+60+60+30`, `30`, `30+30`, `30+60+1,2`, `29,9` e `30+60`. O caso de 240 s agora pode gerar cinco partes: limitar artificialmente a quatro perderia conteúdo. Regressões de primeira de 60 s cobrem 120, 240, 60, 61,2 e 119,9 s. Entrada 240,1 s é rejeitada antes de produzir arquivos em ambos os perfis.

Cada parte é medida e decodificada independentemente: H.264/yuv420p, AAC, keyframe inicial, duração de vídeo/áudio/container sem exceder o teto selecionado, fim dos pacotes no ponto esperado. Marcador vermelho e áudio decodificado no final confirmam preservação do conteúdo final. Mantêm-se as tolerâncias de quantização documentadas no registro anterior, sem tolerância para ultrapassar o teto.

Testes da API cobrem rejeição de zero, negativos, frações, strings e outros tipos inválidos no JSON; headers de upload inválidos; padrão 60; ambos os caminhos até o worker real; migração idempotente de banco antigo com tarefas pendentes e resultados existentes; nova instância sobre o mesmo SQLite recuperando uma tarefa de 30 s; duas sessões com escolhas diferentes na fila. Nos testes de link, somente a operação de rede é substituída por mídia sintética — não constitui extração remota real. Os testes de segurança e extração existentes continuam passando.

## Navegador e HTTP

Chromium usa contextos próprios para mobile **390×844**, com touch/emulação móvel, e desktop **1440×1000**. Asserções verificam ordem no DOM e posição de resultados antes das instruções, padrão 60 e ausência de overflow. Para **cada formulário e cada opção 30/60**, o POST fica retido sem resposta: o teste exige feedback visível na viewport, foco no status, `aria-busy=true`, submit desabilitado e somente uma requisição após nova tentativa de submissão. Inspeciona o JSON/header transmitido antes de responder. Rejeições 429 e falhas de rede liberam ações e removem o spinner nos dois caminhos.

Estados de fila/processamento/erro do link são simulados explicitamente. O upload de 61,2 s passa pelo servidor/SQLite/worker/ffmpeg reais: HTTP padrão gera `60+1,2`; upload mobile com primeira de 30 s gera `30+31,2`. Downloads são acionados em ordem, conferidos por nome e medidos por ffprobe. A prévia decodifica; atualizações de outra tarefa preservam o mesmo elemento de vídeo e não alteram a rolagem. Reload, exclusão e 404 após exclusão também passam. Nenhum erro JavaScript observado.

**Limitações:** mídias são sintéticas; Instagram real continua sem comprovação, com observação anterior de bloqueio por login/rate limit. X real, publicação em WhatsApp físico, CI remoto e build Docker não foram testados nesta entrega. Nenhum mecanismo de extração/contorno foi acrescentado.

Os registros abaixo são históricos: contagens de testes, portas e saídas de quatro partes descrevem as revisões anteriores e o perfil de 60 s.

---

# Registro anterior: revisão v1 — 21/10/2026

Worktree `/home/ubuntu/projects/instazap-worktrees/revisao-v1`, branch `fix/revisao-v1`. Execução local em Linux/Python 3.14.5. Configuração global preservada. Esta revisão não executou push, PR, merge ou deploy. Conversão de mídia e controles SSRF de destinos secundários permanecem intactos.

## Resultados desta revisão

Todos os comandos abaixo foram executados em **21/10/2026 (America/Sao_Paulo)**:

| Comando | Resultado real |
|---|---|
| `uv sync --frozen` | Criou `.venv`, instalou 48 pacotes do lock; sucesso |
| `uv run ruff check instazap tests scripts` | `All checks passed!` |
| `uv run pytest -q` | **102 passed, 1 warning in 43.59s** |
| `uv run --group browser python scripts/smoke.py --browser` | HTTP PASS e BROWSER PASS; servidor encerrado |
| `uv run pip-audit -r requirements.txt --disable-pip` | `No known vulnerabilities found` |

O warning continua sendo a depreciação de httpx no TestClient do Starlette. O smoke reutilizou o Chromium em cache, instalou três pacotes do grupo browser e verificou upload sintético → fila persistida → worker → downloads de 60,0 s e 1,2 s → exclusão → 404. No viewport 390×844: duas prévias, decodificação, download, reload e exclusão, sem erros JS ou overflow. Servidor somente em `127.0.0.1:8765`, encerrado pelo script. Capturas locais ignoradas pelo Git.

Os novos testes offline verificam queries/fragmentos de compartilhamento descartados **após** validar a autoridade e o caminho originais, URL canônica no SQLite/fila/subprocesso e na chamada ao extrator, rejeição de portas malformadas/vazias/fora de faixa, credenciais, hosts privados, hosts semelhantes, controles e caminhos codificados. As antigas expectativas de rejeição de query/fragmento foram substituídas: esses componentes são comuns no compartilhamento e não escolhem o destino quando descartados após validação. Os testes de DNS privado/misto, redirects e conexão presa ao IP continuam passando.

O diagnóstico agora recebe do subprocesso limitado somente um token de categoria; o script aceita uma lista fixa de tokens e descarta qualquer saída inesperada. Falhas mostram `NOT VERIFIED: CATEGORIA. Use upload.` e exit code 1, sem URL de origem/CDN, segredos ou traceback. Testes simulam a mensagem real do yt-dlp e saídas/exceções contendo URLs e segredos. Categorias: `LOGIN_REQUIRED_OR_ANONYMOUS_RATE_LIMIT`, `LOGIN_REQUIRED`, `RATE_LIMITED`, `NETWORK_BLOCKED`, `DOWNLOAD_FAILED`, `TIMEOUT`, `PROCESS_FAILED` e `INVALID_URL`. Falhas não reconhecidas continuam genéricas; a categoria não pretende identificar toda causa possível.

**Instagram: download não comprovado.** A observação real independente fornecida pelo operador foi `DownloadError` na chamada direta ao downloader: página redirecionada para login e limite de acesso anônimo excedido. O extrator instalado contém essa mensagem, agora reconhecida como `LOGIN_REQUIRED_OR_ANONYMOUS_RATE_LIMIT`. Esta revisão não repetiu o acesso remoto: a observação é do operador, registrada em 21/10/2026, sem data/hora exata informada para a tentativa. Não foram usados cookies, login ou contorno. Upload manual é o fluxo validado; X continua sem teste público real.

**CI:** os checks acima são locais. Para verificar execução remota, o operador deve abrir a aba Actions do repositório, localizar a execução pelo commit/branch e inspecionar status/logs de lint, pytest, auditoria e build Docker. Esta revisão não executou build Docker nem verificou CI remoto.

O registro anterior abaixo preserva o contexto e os resultados da primeira versão; não representa uma nova execução desses comandos nesta revisão.

## Registro anterior: primeira versão — 21/10/2026

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

Auditoria das dependências de **produção** travadas; não audita bibliotecas nativas do sistema/ffmpeg. **Build e execução Docker NÃO TESTADOS** localmente: executável ausente, sem tentar instalar ou iniciar daemon. Dockerfile/Compose foram revisados estaticamente. CI contém build executável; o operador deve conferir a execução do commit desejado na aba Actions, incluindo o log da etapa Docker. Python 3.12 da imagem/CI não foi executado neste host (teste nativo usou 3.14.5).

## Link público: download NÃO VERIFICADO; falha de login/rate limit observada

A skill agent-reach foi consultada e `agent-reach doctor --json` informou backend Instagram indisponível (sem OpenCLI) e X sem autenticação. Não foi configurada nem acessada conta pessoal. Para exercitar o **próprio downloader implementado**, foi usado um exemplo público histórico presente nos testes do extrator InstagramIE instalado; disponibilidade atual desconhecida:

```console
$ uv run python scripts/check_instagram.py https://www.instagram.com/reel/Chunk8-jurw/
NOT VERIFIED: link unavailable, network restricted, login required, or extractor blocked. Use upload.
```

Exit code 1. Esse wrapper original ocultava a causa. No diagnóstico real independente informado pelo operador nesta revisão, a chamada direta `download('https://www.instagram.com/reel/Chunk8-jurw/', temp_path)` retornou `yt_dlp.utils.DownloadError`: a requisição da página foi redirecionada para login e o limite de acesso anônimo foi excedido. Essa é a causa observada para aquela tentativa, não evidência de download bem-sucedido nem prova de indisponibilidade permanente do post. A data/hora exata da tentativa independente não foi informada; a observação foi registrada em 21/10/2026. Não houve tentativa de contornar restrições, usar cookies pessoais ou fornecedor pago. Upload manual continua sendo o caminho validado. **X não teve teste público real.**

## Limitações restantes

- Validar os MP4 no WhatsApp em Android/iPhone reais; smoke Chromium não certifica aceitação no aplicativo.
- Extração Instagram/X e Docker precisam de validação independente no host de publicação. Nada foi publicado por esta execução.
- Retenção bloqueia acesso no prazo; remoção física pode esperar uma tarefa em andamento e não é apagamento forense. Arquivos já baixados ou streams já abertos não podem ser revogados.
- Conversão com perdas para 30 fps; AAC tem priming/padding de codec, e a duração de apresentação é sinalizada no MP4. Formatos/metadados inconsistentes podem ser recusados; não há promessa de cobrir todos os codecs do ffmpeg.
- Execução nativa não tem o cgroup de memória/CPU do Compose; volume sem quota tem orçamento lógico, não limite físico do filesystem. Recomendado somente uso privado de baixo volume.
