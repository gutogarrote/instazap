# Validação: tarefa A, durações equilibradas — 22/10/2026

Worktree `duracoes-equilibradas`, base `origin/main` **608288c**. Somente tarefa A; sem timeline manual, subagentes, push, PR, merge, deploy ou alterações globais. Porta 8765/dados do servidor ativo e outros worktrees preservados. Linux, Python 3.14.5, ffmpeg/ffprobe 6.1.1, um thread de processamento e testes sequenciais. Servidores descartáveis somente em **127.0.0.1:8871**, encerrados pelo script; fontes/probes em TMPDIR. Chromium instalado em cache reutilizado, sem instalar navegador.

## Comandos e resultados desta entrega

| Comando exato | Resultado |
|---|---|
| `uv sync --frozen` | Sucesso, lock existente; 48 pacotes instalados |
| `uv run pytest -q` | **191 passed, 1 warning in 287.66s** |
| `uv run ruff check instazap tests scripts` | **All checks passed!** |
| `uv run python scripts/smoke.py --port 8871` | **HTTP PASS**, downloads [60.0, 1.2], exclusão/404; **SERVER STOPPED** |
| `uv run --group browser python scripts/smoke.py --browser --port 8871` | **HTTP PASS + BROWSER PASS; SERVER STOPPED** |
| `uv run pytest -q tests/test_media_timeline.py -k 'fractional_equal or original_audio_delay'` | **2 passed, 13 deselected, 1 warning in 16.14s**, antes da suíte completa |

O único warning é a depreciação já existente de httpx no TestClient do Starlette. `scripts/smoke.py --help` foi inspecionado antes da execução. O grupo browser instalou somente suas três dependências Python; o Chromium já existia.

## Cobertura e correção de junções fracionárias

Primeira parte 15/30/60/90 (padrão 60) aceita em upload/link; valores inválidos/tipos/coerções são recusados antes da criação. Store e CHECK SQLite validam a escolha. Migração aditiva é testada tanto sem coluna antiga quanto com o CHECK anterior (30,60); bytes de uma saída pronta anterior permanecem intactos. Fila com as quatro escolhas, novas instâncias de Store/app, interrupção/reinício, ZIP/Range/ownership e limites de recursos continuam cobertos.

Partes posteriores usam o menor número de intervalos iguais de até 90 s. Testes racionais incluem restante zero, frações próximas a zero, 60, 90, 120 e 180, vizinhanças desses limites e 225 s. Testes com ffmpeg incluem restos 59,9; 60; 90; 90,1; 119,9; 120; 180 e 190,1 s, primeira seleção maior que o total, múltiplos exatos e rejeição de 240,1 s. Conferem todas as faixas/contêineres sob os tetos estritos e decodificação independente das saídas.

Uma regressão nova encontrou perda real de aproximadamente 14–15 amostras de áudio por junção fracionária: o timescale padrão do MP4 arredondava a lista de edição para milissegundos. A saída agora usa `-movie_timescale 48000`, mantendo precisão de amostra. A verificação usa chirp a 48 kHz, comparando PCM nas três junções e na cauda, além de marcadores visuais próximos a cada corte, com origem a 30000/1001 fps. No corte fracionário o quadro que atravessa a fronteira pode aparecer de um dos lados; o teste exige que sobreviva junto à junção. Outro teste compara conteúdo com áudio atrasado em 250 ms, preservando o offset original. Casos sem áudio, finais desiguais A/V, AAC com duração inflada, VFR/offset e marcador final permanecem. A soma das durações sozinha não é usada como prova de conteúdo.

O teste de link substitui **somente o downloader por fixture sintética** e bloqueia a primeira codificação real: nesse intervalo a API já retorna `original_duration`, estado processing e nenhuma parte pronta. O valor persiste no resultado/recuperação. Downloader que falha oferece upload; arquivo baixado inválido produz erro de processamento distinto. A duração vem do probe local reutilizado, depois do download e antes de codificar; nenhuma requisição remota adicional ou duração presumida de URL.

## Aceitação real no navegador

Chromium em 390×844 e 1440×1000 verifica as quatro escolhas nos dois formulários. POST retido artificialmente confirma status acessível imediatamente, foco visível, controles desabilitados e bloqueio de duplicatas antes da resposta HTTP; rejeições HTTP/conexão restauram os controles. Estados de link e duração desconhecida/medida/recuperada são simulados explicitamente na camada HTTP, sem alegar extração real.

A matriz da UI verifica explicações nos limites viáveis/inviáveis e total menor que cada escolha. Uploads HTTP reais são processados pelo worker para as quatro escolhas com fonte sintética de 61,2 s; 90 produz somente 61,2 s. Outro upload real de 150,1 s com primeira 30 produz duas partes restantes de aproximadamente 60,05 s, dentro do arredondamento de quadro. Downloads são inspecionados com ffprobe. Polling de outro job preserva o nó do player carregado e a rolagem; reload, sessão recuperada e exclusão passam sem erro JavaScript/overflow. Capturas em `artifacts/mobile.png` e `artifacts/desktop.png` são locais, ignoradas pelo Git e foram inspecionadas visualmente. O destaque decorativo desktop foi atualizado para o teto de 90 s; o smoke de navegador foi repetido com sucesso após esse ajuste final de CSS.

## Limites da evidência

**Nenhum link real de Instagram/X foi baixado ou validado nesta entrega.** Fixtures sintéticas mais ffmpeg/Chromium reais comprovam somente processamento e interface. Os registros abaixo são de versões anteriores, com evidência e regras daquelas versões; não são novas execuções nem prova de acesso remoto para esta mudança. WhatsApp em Android/iPhone, CI remoto e Docker não foram executados. Resultados antigos sem fonte disponível mantêm duração original desconhecida; os arquivos prontos são preservados. Recodificação continua com perdas e quantização de quadro/amostra, sem promessa de identidade bit a bit.

---

# Registro histórico: cortes de mídia real do Instagram — 21/10/2026

Worktree `cortes-instagram`, branch `fix/cortes-instagram`, base publicada `fac4bdb`. UI, persistência e primeira parte de 30/60 s aprovadas foram preservadas. Sem push, PR, merge ou deploy; preview 8765 preservado. Smoke somente em `127.0.0.1:8766`, encerrado pelo script. Linux, Python 3.14.5, ffmpeg/ffprobe 6.1.1; subprocessos de mídia com um thread de decodificação/filtros/encoder e os limites existentes, sem mudanças globais.

## Causa observada e correção

O operador/Hermes confirmou download bem-sucedido de **um** link real de Instagram. A falha reproduzida estava no corte/validação, não em login. A mesma origem temporária foi reprocessada nesta sessão, sem repetir a requisição remota. URL, mídia e artefatos privados não fazem parte do repositório ou do CI.

O contêiner e a faixa HE-AAC/48 kHz declaram **78,258708 s**; o vídeo tem **2.345 quadros a 30 fps**, até **78,166667 s**. O primeiro pacote de áudio começa em −0,105375 s e sinaliza `skip_samples=5058`: priming, não conteúdo a repetir no início das partes. O último pacote começa em **78,145292 s** e declara **0,113417 s**, mas decodifica **2.048 amostras = 0,042666667 s**. A duração declarada excede as amostras desse pacote em **3.396 amostras / 70,750 ms**. O PCM decodificado contém **3.753.022 amostras por canal**, até **78,187958333 s**. Logo, áudio significativo continua cerca de 21,292 ms após o vídeo, não os 92,041 ms sugeridos pelos metadados.

A baseline exigia que a segunda parte alcançasse 48,258708 s; o áudio gerado chegava a aproximadamente 48,188 s e disparava a rejeição de diferença >50 ms. Não havia amostras correspondentes aos 70,750 ms adicionais a recuperar. A correção mede timestamps dos quadros decodificados, usando `nb_samples/sample_rate` no áudio, limitado também por duração de apresentação menor quando sinaliza end padding. Não considera silêncio como descartável. Mantém a origem comum do demuxer para preservar offsets entre faixas.

O último quadro pode ser prolongado para cobrir áudio real mais longo. Áudio passa por `aresample=48000:async=1:first_pts=0` antes do corte, normalizando priming/timestamps na origem comum. A conversão para 30 fps ocorre desde essa mesma origem em cada execução; `-ss` é de saída, após filtragem. Buscar na entrada descartava o quadro de 29,97 fps que começava antes do corte e continuava depois dele. O custo dessa escolha é reler/decodificar o prefixo em cada parte, sequencialmente, sem intermediário persistido ou aumento dos limites de memória/threads. Bitrate, resolução, aspecto, CRF, limite de entrada 240 s/200 MB e opções da interface permanecem iguais.

Cada faixa de saída é validada separadamente, além do contêiner. **Nenhuma tolerância permite ultrapassar o teto 30/60 s.** A tolerância inferior de vídeo é um quadro de 30 fps + 1 ms de arredondamento MP4; no áudio é uma amostra de 48 kHz + 1 ms. Ambas são inferiores aos antigos 50 ms. Medir conteúdo em vez de metadados inflados não constitui uma dispensa de validação da cauda.

## Regressões automatizadas e prova da baseline

Fixtures pequenas (64×48) têm vídeo de 61,100 s e áudio de 61,192 s, ou vídeo de 61,200 s e áudio de 61,108 s. Cobrem ambos os sentidos da diferença de 92 ms com primeira de 30/60 s, 30000/1001 fps e VFR com timestamps deslocados em 5 s. Uma variante modifica apenas as tabelas de duração do último pacote AAC/contêiner em 3.396 amostras, reproduzindo a duração inflada sem alterar o payload. Ao remover a duração parcial sinalizada, esse AAC-LC sintético pode expor até um quadro de padding (1.024/48.000 s), explicitamente limitado no teste; isso não amplia a tolerância de saída.

Execução local contra o código de `fac4bdb`: **MediaError reproduzido para primeira de 30 e 60 s** na fixture de AAC inflado. A fixture com um único quadro vermelho de 29,97 fps atravessando a junção também falha na baseline nos dois modos; passa com o corte após filtragem.

Os testes decodificam cada saída independentemente, conferem timestamps iniciais, tetos de vídeo/áudio/contêiner, marcador visual final e aquele quadro da junção. Comparam PCM de um chirp, amostra por amostra em janelas antes/depois do corte e na cauda, exigindo correlação >0,95 e contagem de amostras compatível: duração somada sozinha não basta. A validação recusa uma perda de áudio simulada de 200 ms mesmo com vídeo/contêiner completos. Logs de erro conferidos têm estágio, parte, faixa, real/esperado/teto; não incluem URL, caminho ou mensagem arbitrária de exceção. Falha do worker continua removendo a origem e saídas parciais.

Casos anteriores continuam: sem áudio, orientação/aspecto, exatos 30/60/120/240 s, cinco partes com 30-first, rejeição de 240,1 s, limites HTTP/200 MB, segurança, fila, migração e UI aprovada.

## Validação privada do arquivo real

As duas execuções abaixo usam a versão final e o mesmo arquivo temporário, com ffprobe real e decodificação independente completa de todas as partes:

| Primeira parte | Durações dos MP4 | Áudio concatenado | Vídeo concatenado |
|---|---|---|---|
| 30 s | 30,000 + 48,200 s | 3.753.024 amostras/canal | 2.346 quadros |
| 60 s | 60,000 + 18,200 s | 3.753.024 amostras/canal | 2.346 quadros |

Todas as faixas e contêineres respeitam os tetos e começam em zero. Para comparar PCM, o priming é removido pelo demuxer e o áudio é limitado à duração de apresentação da faixa (`atrim`), excluindo amostras de padding do último quadro AAC que o decoder pode emitir além dessa duração; a decodificação independente completa é conferida separadamente. Os dois modos preservam as **3.753.022 amostras** da origem, acrescentando somente duas amostras (41,667 µs) de arredondamento de apresentação. Comparações de PCM a cada segundo, antes/depois de cada junção e no final têm correlação mínima **0,98346** com a origem. Todos os **2.345 quadros originais** foram comparados em RGB reduzido a 16×16: erro absoluto médio máximo por quadro **0,887/255**, e **0,262/255** no último. O único quadro extra prolonga a imagem final até 78,200 s para cobrir o áudio; não substitui nem remove o quadro original final. A normalização/recodificação é com perdas, sem promessa de identidade bit a bit.

Probe JSON, MP4 e relatório numérico completos ficam somente no scratch privado fora do worktree, para revisão local do Hermes; nenhum deles é asset de CI ou anexo público. Não houve aumento de retenção em produção.

## Checks finais executados

| Comando | Resultado |
|---|---|
| `uv sync --frozen` | Sucesso com lock existente |
| `uv run ruff check instazap tests scripts` | All checks passed! |
| `uv run pytest -q` | **154 passed, 1 warning in 191.63s** |
| `uv run --group browser python scripts/smoke.py --browser` | HTTP PASS e BROWSER PASS; somente 8766; servidor encerrado |
| `uv run pip-audit -r requirements.txt --disable-pip` | No known vulnerabilities found |

Warning já existente: depreciação de httpx no TestClient do Starlette. Browser em cache reutilizado, sem instalar navegador; smoke repetido após a alteração final do corte. Upload real sintético gera 60+1,2 s; fluxo mobile 30-first gera 30+31,2 s; verificações de feedback, escolha 30/60, downloads e prévias estáveis passaram.

Este caso comprova obtenção/processamento de um link real de Instagram, sem garantir acesso universal. X, WhatsApp em aparelho físico, CI remoto e Docker continuam sem nova validação. **As declarações de Instagram não verificado nos registros históricos abaixo foram superadas para este caso específico.**

---

# Registro anterior: primeira parte e feedback — 21/10/2026

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

Exit code 1. Esse wrapper original ocultava a causa. No diagnóstico real independente informado pelo operador nesta revisão, a chamada direta `download(URL_PUBLICA_AUTORIZADA, temp_path)` retornou `yt_dlp.utils.DownloadError`: a requisição da página foi redirecionada para login e o limite de acesso anônimo foi excedido. Essa é a causa observada para aquela tentativa, não evidência de download bem-sucedido nem prova de indisponibilidade permanente do post. A data/hora exata da tentativa independente não foi informada; a observação foi registrada em 21/10/2026. Não houve tentativa de contornar restrições, usar cookies pessoais ou fornecedor pago. Upload manual continua sendo o caminho validado. **X não teve teste público real.**

## Limitações restantes

- Validar os MP4 no WhatsApp em Android/iPhone reais; smoke Chromium não certifica aceitação no aplicativo.
- Extração Instagram/X e Docker precisam de validação independente no host de publicação. Nada foi publicado por esta execução.
- Retenção bloqueia acesso no prazo; remoção física pode esperar uma tarefa em andamento e não é apagamento forense. Arquivos já baixados ou streams já abertos não podem ser revogados.
- Conversão com perdas para 30 fps; AAC tem priming/padding de codec, e a duração de apresentação é sinalizada no MP4. Formatos/metadados inconsistentes podem ser recusados; não há promessa de cobrir todos os codecs do ffmpeg.
- Execução nativa não tem o cgroup de memória/CPU do Compose; volume sem quota tem orçamento lógico, não limite físico do filesystem. Recomendado somente uso privado de baixo volume.
