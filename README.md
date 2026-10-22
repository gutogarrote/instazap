# Instazap

Site pessoal em português para preparar vídeos para o Status. Um host serve a página, a API e os arquivos. Sem serviços pagos, conta cloud, API do X, banco externo ou build JavaScript.

**Limites fixos:** entrada de até **240 s e 200.000.000 bytes (200 MB decimais)**; saída ordenada com primeira parte de **30 ou 60 s** (padrão **60 s**) e demais partes de até **60 s**. Com primeira de 30 s, podem ser necessárias **cinco partes** para preservar os 240 s. Vídeos acima do limite são rejeitados, nunca encurtados silenciosamente. H.264/AAC em MP4, yuv420p, até 1280×720 ou 720×1280, sem ampliar imagens pequenas nem cortar o enquadramento. O vídeo é convertido para 30 fps; a recodificação é com perdas. Sem áudio na entrada → sem faixa de áudio na saída.

Instagram por yt-dlp primeiro; X usa o mesmo mecanismo, experimental e sem API paga. Apenas publicações públicas com MP4 direto são elegíveis. **Um link real de Instagram foi baixado com sucesso no diagnóstico do operador/Hermes e o arquivo foi processado e decodificado localmente com primeira parte de 30 e 60 s.** Isso comprova esse caso, sem garantir disponibilidade de outros links; tentativas anteriores encontraram login/rate limit. X continua sem validação real. Upload manual foi validado de ponta a ponta. Não usar para material sem autorização.

## Primeira parte e acompanhamento

O formulário de link aparece primeiro, seguido do upload manual. O controle compartilhado “Primeira parte” vale para os dois caminhos. Ao enviar, “Aguarde… recebendo o link/o arquivo” aparece antes da resposta HTTP, com status acessível e ações bloqueadas durante a requisição. Fila, processamento e resultados ficam logo após os controles, antes de “Como funciona”. Falhas liberam as ações; o polling preserva prévias existentes e não força rolagem.

| Entrada | Primeira de 30 s | Primeira de 60 s (padrão) |
|---|---|---|
| Até 30 s | Uma parte, duração original | Uma parte, duração original |
| 60 s | 30 + 30 | 60 |
| 120 s | 30 + 60 + 30 | 60 + 60 |
| 240 s | 30 + 60 + 60 + 60 + 30 | 60 + 60 + 60 + 60 |

O restante final é preservado. Finais diferentes de áudio/vídeo são medidos pelos quadros e amostras de apresentação, sem tratar duração inflada de pacote AAC como conteúdo perdido. Quando o áudio continua, o último quadro pode ser prolongado; os tetos de cada parte continuam estritos. A causa e a validação real estão em [docs/validacao.md](docs/validacao.md). O limite de entrada continua 240 s/200 MB; selecionar 30 s não permite entradas maiores. A escolha fica em `jobs.first_part_seconds` no SQLite e acompanha cada tarefa até o worker, inclusive após reinício. Bancos antigos recebem migração aditiva com padrão 60, preservando tarefas e resultados existentes.

## Continuar de onde parei (corte manual)

Para quem já compartilhou o começo de um reel no Status pelo próprio Instagram (o compartilhamento nativo pode parar em qualquer ponto, por volta de 15–30 s). Escolha **“Continuar de onde parei”**, cole o link (ou envie o arquivo) e toque em **Carregar vídeo**. O servidor baixa com o mesmo transporte restrito, mede a duração decodificada, gera uma **prévia local normalizada** e **para**: nada é cortado até você escolher.

O player HTML5 (controles nativos) toca essa prévia, ocupa quase toda a largura no celular e é responsivo no desktop. Arraste a barra **Início do corte** (mouse ou toque), use −1 s/−1 quadro/+1 quadro/+1 s, setas/PageUp/PageDown/Home/End ou digite o tempo (`17,4` ou `0:17,4`). O tempo atual fica visível (`0:17,400`) e o botão diz exatamente o que será feito: **Gerar partes a partir de 0:17,400**. Só o trecho do ponto escolhido até o fim vira partes; a introdução já compartilhada não é gerada.

Regra de divisão do restante: o **menor número de partes iguais com no máximo 90 s**. Assim as partes ficam entre 60 e 90 s sempre que possível (restante de 60–90, 120–180 ou 180–270 s). Quando o restante tem menos de 60 s, sai uma parte só; entre 90 e 120 s, duas partes iguais com menos de 60 s são inevitáveis (uma passaria de 90 s). A interface explica isso antes e depois do corte. Nada é descartado e nenhuma parte passa de 90 s.

| Restante após o corte | Partes |
|---|---|
| 29,9 s | 29,9 |
| 75,5 s | 75,5 |
| 105,4 s | 52,7 + 52,7 (menos de 60 s, inevitável) |
| 120 s | 60 + 60 |
| 180 s | 90 + 90 |
| 180,01 s | 60 + 60 + 60,01 |
| 240 s | 80 + 80 + 80 |

**Precisão do corte:** o ponto é lido com resolução de 1 µs e arredondado **para baixo** até o quadro de saída de 30 fps (no máximo 1/30 s antes do ponto). O primeiro quadro gerado é o quadro exibido naquele instante; o áudio começa nesse mesmo instante, com precisão de amostra (48 kHz). As fronteiras internas também ficam na grade de 30 fps, então cada parte fica a menos de um quadro do tamanho ideal; a soma é exatamente o restante medido. Os cálculos usam frações exatas, sem `ceil` de ponto flutuante. A medição usa quadros/amostras decodificados (mesma base das correções anteriores de cauda/AAC), preserva offsets originais entre áudio e vídeo e mantém tetos estritos por faixa e contêiner. O MP4 de saída usa timescale de filme 48000 para que a lista de edição não arredonde o áudio para milissegundos nas junções. **Mesma linha do tempo no player e no corte.** Navegadores tratam de formas diferentes o início do contêiner (o Chromium mostra um MP4 que começa em +5 s a partir de 4,978 s). Por isso o player nunca toca a origem bruta. A prévia é codificada como as partes: tempos relativos à origem comum do demuxer (offsets entre áudio e vídeo preservados, sem zerar cada faixa), a mesma grade de 30 fps e a última imagem mantida durante uma cauda só de áudio. Ela começa em 0 em qualquer navegador, e a imagem exibida no tempo *t* é exatamente o primeiro quadro de uma parte cortada em *t*. Essa imagem é o quadro de origem que estava na tela em algum instante a até ±1/60 s de *t* (reamostragem para 30 fps). Antes do primeiro quadro de vídeo (vídeo que começa depois do áudio), player e parte mostram o primeiro quadro. O áudio começa no mesmo instante da imagem. Testado com início zero, positivo (MP4 +5 s, MPEG-TS ≈1,4 s) e negativo (Matroska −0,021 s). Se a prévia gerada não começar nessa origem, a tarefa falha com mensagem em vez de oferecer um relógio diferente. A prévia tem no máximo 640 px no lado maior, `ultrafast`/CRF 30 e keyframe a cada 1 s: serve para achar o ponto, não para avaliar a qualidade final. Prepará-la leva tempo (medido: 240 s em 720p, acima do uso típico, levou 61 s com um thread e gerou 28 MB).

Ciclo de vida: a origem e a prévia ficam guardadas enquanto a prévia espera a escolha. A expiração é deslizante (1 h, renovada pelo polling da página aberta) com teto absoluto de 6 h desde a criação. Ela sobrevive a reload e reinício do servidor. **Descartar vídeo** apaga na hora. Uma prévia aguardando conta como a tarefa ativa da sessão e reserva espaço como as demais. Ao confirmar o corte, a prévia é apagada e a tarefa volta à fila com o ponto salvo (reinício refaz a partir da origem). Ao terminar ou falhar, a origem é apagada como antes. No modo manual, carregar outro link (**Trocar vídeo**) ou outro arquivo (**Trocar pelo vídeo do aparelho**) descarta a prévia atual primeiro. Mudar para “Dividir o vídeo inteiro” não apaga nem esconde a prévia; um envio nesse modo é recusado com aviso até você cortar ou descartar. Uma prévia que ainda espera, criada por uma versão anterior sem prévia normalizada, é preparada de novo a partir da origem guardada, sem novo download. O modo **“Dividir o vídeo inteiro”** continua idêntico e é o padrão.

API do modo manual (mesma sessão, CSRF e proteções):

- `POST /api/jobs/link` com `{"url":"…","mode":"manual"}`; upload com header `X-Mode: manual`. `mode` aceita somente `"full"` (padrão) ou `"manual"`.
- A tarefa passa por `queued/processing` e chega a `awaiting_cut`, com `source_duration` (duração decodificada) e sem partes. O worker nunca divide nesse estado.
- `GET /api/jobs/{id}/source`: a prévia normalizada (MP4) com Range, somente do dono e somente em `awaiting_cut`. A origem bruta nunca é servida.
- `POST /api/jobs/{id}/cut` com `{"start": 17.4}`: número JSON finito em `[0, source_duration)`. Rejeita (400) NaN, Infinity, booleanos, strings, null, negativos, fim/fora da duração e corpo inválido. A transição é atômica: repetir o mesmo valor devolve 202 sem nova tarefa (retry de rede); outro valor depois do primeiro dá 409; prévia ainda carregando dá 409; outra sessão recebe 404. A resposta inclui o `plan` das partes.

A interface mostra status acessível imediato antes de cada requisição, desabilita ações duplicadas, restaura os controles após rejeição ou falha de rede, ignora respostas de polling iniciadas antes de uma mudança (respostas antigas não ressuscitam uma prévia) e nunca recria o player durante o polling. A posição escolhida fica no `sessionStorage` da aba e volta após reload. Falha do downloader (“Não foi possível obter este link público…”) continua separada de erros de mídia; falha de reprodução da prévia no navegador mostra aviso próprio.

Na API, `POST /api/jobs/link` recebe JSON como `{"url":"https://www.instagram.com/reel/CODIGO/","first_part_seconds":30}`. `POST /api/jobs/upload` continua recebendo **corpo binário bruto**, com header `X-First-Part-Seconds: 30`. Omitir o campo/header usa 60. O JSON aceita somente os inteiros 30 ou 60; o header aceita somente `30` ou `60`. Valores inválidos são rejeitados com 400 antes de criar a tarefa. Sessão, CSRF, origem e demais proteções continuam obrigatórios.

## Iniciar com Python/uv (Linux)

Pré-requisitos: Python ≥3.12, [uv](https://docs.astral.sh/uv/), `ffmpeg` e `ffprobe` ≥6 com libx264/AAC, disponíveis no PATH. Não é necessário instalar dependências do sistema se já disponíveis.

```bash
uv sync --frozen
cp .env.example .env
# Edite .env se necessário. O uvicorn abaixo carrega apenas esse arquivo local.
uv run python scripts/serve.py
```

Abra **http://127.0.0.1:8765**. `Ctrl+C` encerra o app. Não use `--reload`, múltiplos workers ou réplicas. O worker compartilha o processo da API; um lock de arquivo impede dois servidores usando o mesmo diretório. `data/` guarda SQLite e arquivos, e está no `.gitignore`.

Sem `.env`, o comando equivalente é:

```bash
uv run uvicorn instazap.app:app --host 127.0.0.1 --port 8765 --workers 1 --limit-concurrency 16 --timeout-keep-alive 5 --no-access-log --no-proxy-headers
```

Para acessar do celular, prefira Tailscale/VPN já configurada. Em `.env`, defina `INSTAZAP_BIND` com o **IP privado Tailscale do host** e `INSTAZAP_ORIGIN=http://IP-PRIVADO:8765`. O app não instala VPN nem altera a rede. HTTP na VPN funciona com `INSTAZAP_SECURE_COOKIE=0`; isso não deve ser usado em HTTP aberto na internet.

## Docker Compose

```bash
cp .env.example .env
# Padrão .env: bind em 127.0.0.1 e porta 8765.
docker compose up --build -d
docker compose logs --tail=100 instazap
docker compose down
```

Volume nomeado `instazap-data` preserva fila, sessão e resultados entre reinícios. **Não executar `docker compose down -v` se quiser preservar esses dados.** A imagem usa UID/GID 10001, sem privilégios; raiz somente leitura, sem capabilities, 1 CPU, memória 1536 MB, 96 processos e /tmp de 64 MB. Em volume bind manual, o diretório precisa ser gravável pelo UID 10001. Docker não estava instalado neste ambiente: build/execução local **não testados**; o workflow CI inclui build. Confira o resultado da execução correspondente ao commit na aba Actions do repositório.

## Uso

1. Escolha um vídeo e clique em **Dividir meu vídeo**, ou cole a URL HTTPS de uma publicação do Instagram/X.
2. Durante o upload, mantenha a página aberta. Depois de recebido por inteiro, pode fechá-la: a tarefa fica no SQLite e continua no servidor.
3. Reabra no mesmo navegador para restaurar as tarefas. Baixe as partes em ordem e publique manualmente no WhatsApp. ZIP é opcional; os MP4 são os mesmos.
4. Clique em **Excluir agora**, ou aguarde a expiração indicada. A origem é apagada ao concluir ou falhar; partes/ZIP ficam por 24 h após conclusão. Tarefas pendentes expiram 24 h após criação.

Cookies apagados, outro navegador ou expiração da sessão impedem recuperar o acesso. Sessão dura sete dias; ela não é uma conta nem permite recuperação de senha. O horário exibido acompanha o fuso do navegador.

URLs aceitas (HTTPS, hosts exatos permitidos, sem credenciais ou porta):

- `https://www.instagram.com/reel/CODIGO/`, `/reels/CODIGO/` ou `/p/CODIGO/`;
- `https://x.com/usuario/status/ID` (também `twitter.com`).

Parâmetros de compartilhamento como `?igsh=...`, `?utm_source=...`, `?s=...` e `?t=...`, além de fragmentos, são aceitos e descartados após validar a URL original. Apenas a URL canônica HTTPS sem query/fragmento segue para a fila e o downloader; parâmetros não são registrados em logs. Perfis, Stories, lives, carrosséis, páginas arbitrárias, playlists HLS/DASH e formatos de vídeo/áudio separados são recusados. Conteúdo pode estar indisponível, bloqueado no datacenter ou exigir login; nesses casos a interface oferece **Enviar arquivo**. Não forneça senhas/cookies de Instagram ou X. O código não usa sessões pessoais do navegador nem mecanismos de contorno.

## Arquitetura e recursos

```text
Navegador → FastAPI → SQLite (receiving → queued → processing → ready/error)
                         ↓ worker único
             upload local OU yt-dlp restrito
                         ↓ ffprobe local
             ffmpeg por parte → validação → MP4 + ZIP
                         ↓ downloads autorizados + expiração
```

Uploads são corpo binário direto, sem multipart nem spool ilimitado: contagem real de bytes durante `Request.stream()`, independente de Content-Length, com timeout de 120 s. Até oito tarefas armazenadas, uma ativa por sessão, orçamento lógico de disco de 3 GB; reserva conservadora de 450 MB por tarefa pendente e pelo menos 950 MB livres antes de aceitar outra. Fila cheia responde 429. Uma tarefa processa de cada vez; existem limites por subprocesso de memória (1 GiB de espaço virtual), CPU (600 s), arquivo (450 MB), descritores e tempo de parede (probe 30 s, download 210 s, cada parte 600 s). Download tem ainda orçamento compartilhado de 200 MB para **todos os corpos HTTP**, 180 s, 80 requisições, socket de 10 s, no máximo cinco redirecionamentos e sem retries. Logo, um vídeo remoto próximo de 200 MB pode ser recusado pelo custo das páginas de extração.

No Linux, PR_SET_PDEATHSIG encerra o subprocesso se a API morrer, evitando processos órfãos durante recuperação. FFmpeg usa um thread de decoder, um de filtros e um de encoder, em etapas locais. O limite de CPU do Compose restringe o conjunto; execução nativa tem limites de subprocesso, mas não o cgroup do Docker. Não é um serviço para tráfego público em escala nem para mídia hostil de terceiros. O limite lógico não é quota de filesystem: use volume dedicado/quota se precisar de garantia física contra consumo de outros processos. O app não limpa arquivos fora de seu diretório.

A fila persiste antes do processamento. No desligamento normal, a tarefa ativa volta à fila com a origem preservada. No reinício, tarefas `processing` voltam à fila e são refeitas a partir da origem; arquivos parciais não são publicados. Uploads interrompidos viram erro e exigem novo envio. Resultados prontos sobrevivem ao reinício. O worker faz limpeza no início e entre tarefas (ou a cada segundo ocioso); autorização bloqueia arquivos vencidos imediatamente, mesmo antes da remoção física. Servidor desligado não executa limpeza; ela acontece ao iniciar novamente. Uma tarefa longa pode atrasar a remoção física até seu término. Exclusão de tarefa em processamento cancela seu subprocesso em até cerca de 1 s (probe pode levar até 30 s); novos acessos são bloqueados assim que a linha é excluída. Um download já aberto pode terminar, e cópias já baixadas não são revogadas.

## Privacidade e segurança

O **padrão é localhost/rede privada**. Não exponha o worker sem autenticação. Limites de fila/conexão **não são autenticação**. Se decidir publicar:

- Configure `INSTAZAP_ACCESS_SECRET` aleatório (gere conforme `.env.example`), HTTPS, `INSTAZAP_SECURE_COOKIE=1` e `INSTAZAP_ORIGIN` exata.
- A chave é solicitada pela interface e enviada em header, nunca em URL ou armazenamento local. Ela autoriza criar sessões; cada sessão continua isolada por token aleatório de 256 bits, cookie HttpOnly/SameSite=Strict, armazenado só como hash no SQLite. O segredo precisa de proteção contra tentativas na borda; não há bloqueio por IP embutido. Prefira VPN ou gateway de identidade em vez de um link público.
- Sessões já emitidas continuam válidas até sete dias mesmo após trocar a chave; para revogação imediata, pare o app e exclua as sessões no SQLite, preservando os arquivos até TTL (ou apague todo o diretório dedicado se desejar apagar todos os dados).
- Mantenha o diretório de dados privado e dependências/ffmpeg atualizados. O uso nativo compartilha o usuário Unix; a imagem Docker reduz o acesso ao host, mas não é uma sandbox forte para uploads adversariais.

CSRF: toda mutação exige header com token derivado da sessão; validação de Origin quando presente, bloqueio de Sec-Fetch-Site cross-site, sem CORS permissivo. Respostas têm `no-store`, CSP local sem scripts inline e `nosniff`; não há assets remotos, analytics, miniaturas remotas ou fetch de URL no frontend. Downloads e prévias usam a mesma autorização, inclusive Range. A chave compartilhada é adicional; saber um ID não concede acesso ao vídeo.

SSRF: o único RequestHandler do yt-dlp é `RestrictedRH`. Ele aceita apenas HTTPS/443 em domínios estritamente limitados das plataformas/CDNs; resolve e rejeita **todos** os endereços privados, reservados, multicast, link-local e redes de transição IPv6. Conecta diretamente ao IP público validado, mantendo SNI e verificação TLS do hostname original, sem nova resolução DNS. Cada redirect passa novamente pelos mesmos controles. Proxies de ambiente e handlers alternativos são desabilitados **dentro do downloader**, para impedir um transporte que contorne essa validação. Redes que só permitem saída por proxy podem tornar a extração indisponível; não há fallback inseguro. yt-dlp apenas extrai metadados; o código recebe MP4 direto pelo mesmo handler. Downloaders externos e ffmpeg remoto não são invocados. FFmpeg/ffprobe só recebem caminhos locais, protocolo `file` e allowlist de demuxers de vídeo (sem HLS, concat, SVG ou playlists). Sem shell na montagem de comandos. Os testes cobrem DNS privado/misto, redirects, pinning e ausência de handler alternativo.

Os logs de acesso são desativados nos comandos de execução. URLs originais são apagadas dos registros ao terminar/falhar; não se guarda nome do arquivo de origem. SQLite ainda pode reter páginas antigas em WAL/free pages até reutilização: a exclusão é lógica e de arquivos, **não apagamento forense**. Backups, snapshots e logs externos precisam de política própria. As partes também são dados pessoais; não faça backup automático de `data/` se quiser a retenção curta.

## Testar

Todos os vídeos da suíte automatizada são **sintéticos**, gerados localmente por ffmpeg; não são evidência de downloads de redes sociais. A validação privada adicional usou o arquivo real descrito acima, sem incluir mídia, URL ou artefatos no repositório/CI. Não são usados APIs pagos nem rede nos testes pytest.

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check instazap tests scripts
uv run python scripts/smoke.py
# Se houver Chromium/Playwright; reutiliza browser já instalado, se disponível:
uv run --group browser python scripts/smoke.py --browser
# Se não houver Chromium e quiser instalar no seu ambiente:
uv run --group browser playwright install chromium
uv export --no-dev --no-emit-project --format requirements-txt --output-file requirements.txt
uv run pip-audit -r requirements.txt --disable-pip
# Somente para uma publicação pública própria ou autorizada:
uv run python scripts/check_instagram.py 'https://www.instagram.com/reel/CODIGO/'
# Falhas do diagnóstico mostram somente uma categoria fixa, sem URLs ou traceback.
# Se Docker estiver disponível:
docker build -t instazap:local .
```

Os scripts smoke usam temporários, iniciam por padrão somente `127.0.0.1:8766` (`--port` permite outra porta local) e encerram o servidor ao finalizar. Recusam usar a porta se já ocupada. O servidor do smoke é `scripts/smoke_server.py`: o mesmo app e limites, mas o downloader de link copia um vídeo **sintético** local. Ele nunca acessa Instagram/X; prova o processamento local, não downloads reais. Não use esse script para servir usuários. `artifacts/mobile.png`, `desktop.png`, `manual-mobile.png`, `manual-mobile-cutter.png` e `manual-desktop.png` são capturas locais ignoradas pelo Git. Veja [docs/validacao.md](docs/validacao.md) para comandos, resultados e lacunas desta entrega. O workflow CI está configurado para executar pytest, lint, auditoria e build Docker em push/PR. Para verificar o CI, abra a aba Actions do repositório, selecione a execução do commit/branch desejado e confira o status e os logs de cada etapa; checks locais não comprovam execução remota.

## Mapa dos arquivos

| Arquivo | Função |
|---|---|
| `instazap/app.py` | HTTP, sessões, CSRF, upload e downloads |
| `instazap/store.py` | SQLite, fila, TTL, recuperação e reservas de espaço |
| `instazap/worker.py` | Worker único e publicação dos resultados |
| `instazap/media.py` | Probe, cortes, limites e validação |
| `instazap/network.py`, `download.py` | yt-dlp, transporte com proteção SSRF |
| `instazap/process.py` | Subprocessos com limites/cancelamento |
| `instazap/static/` | Interface responsiva em português, sem build |
| `scripts/` | Inicialização e verificações HTTP/navegador/link (`smoke_server.py` só para testes) |
| `tests/` | Testes offline com ffmpeg real |
| `pyproject.toml`, `uv.lock`, `requirements.txt` | Dependências e versões travadas |
| `Dockerfile`, `compose.yaml`, `.env.example` | Execução de um único host |
| `docs/` | Relatório original, decisões atuais e validação |

## Limitações e próximos passos

Acesso por link é de melhor esforço e depende da rede e da plataforma. X não teve validação real. O modo manual foi validado só com mídia sintética e downloader substituído nos testes; nenhum link real de Instagram foi baixado nesta entrega. A prévia é H.264/AAC gerada localmente, então também funciona para MKV/TS e codecs que o navegador não tocaria; se mesmo assim falhar, há aviso e o tempo pode ser digitado. Os testes de navegador usaram só Chromium (toque emulado). Não há publicação automática, contas, edição, seleção de carrossel, sincronização entre navegadores ou vários servidores. Metadados de duração inconsistentes, vídeos danificados e formatos incomuns podem ser recusados em vez de gerar uma saída incompleta. A normalização para 30 fps pode repetir/descartar quadros para ajustar cadência; limites de áudio usam a duração de apresentação MP4/AAC com priming/padding sinalizados no container. Não há promessa de reprodução idêntica bit a bit. Android/iPhone e o upload final no aplicativo WhatsApp precisam de validação em aparelhos reais.

Cloudflare (proxy/acesso, armazenamento ou processamento externo) e Vercel (frontend com worker externo) são opções futuras; **nenhum deles, nem R2, é necessário agora**. Separar serviço/fila/storage só se o uso justificar. O [relatório original](docs/planejamento-original.md) fica preservado como contexto; [as decisões implementadas](docs/decisoes.md) o substituem para esta versão.
