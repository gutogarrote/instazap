# Instazap

Site pessoal em português para preparar vídeos para o Status. Um host serve a página, a API e os arquivos. Sem serviços pagos, conta cloud, API do X, banco externo ou build JavaScript.

**Limites fixos:** entrada de até **240 s e 200.000.000 bytes (200 MB decimais)**; saída de uma a quatro partes ordenadas de até **60 s**. Vídeos acima do limite são rejeitados, nunca encurtados silenciosamente. H.264/AAC em MP4, yuv420p, até 1280×720 ou 720×1280, sem ampliar imagens pequenas nem cortar o enquadramento. O vídeo é convertido para 30 fps; a recodificação é com perdas. Sem áudio na entrada → sem faixa de áudio na saída.

Instagram por yt-dlp primeiro; X usa o mesmo mecanismo, experimental e sem API paga. Apenas publicações públicas com MP4 direto são elegíveis. **Download real de Instagram/X NÃO está verificado neste ambiente.** No diagnóstico independente de Instagram, o yt-dlp retornou `DownloadError`: redirecionamento para login e limite de acesso anônimo (rate limit). Upload manual foi validado de ponta a ponta. Não usar para material sem autorização.

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

Todos os vídeos de teste são **sintéticos**, gerados localmente por ffmpeg; não são evidência de downloads de redes sociais. Não são usados APIs pagos nem rede nos testes pytest.

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

Os scripts smoke usam temporários, iniciam somente `127.0.0.1:8765` e encerram o servidor ao finalizar. Recusam usar a porta se já ocupada. `artifacts/mobile.png` e `desktop.png` são capturas locais ignoradas pelo Git. Veja [docs/validacao.md](docs/validacao.md) para comandos, resultados e lacunas desta entrega. O workflow CI está configurado para executar pytest, lint, auditoria e build Docker em push/PR. Para verificar o CI, abra a aba Actions do repositório, selecione a execução do commit/branch desejado e confira o status e os logs de cada etapa; checks locais não comprovam execução remota.

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
| `scripts/` | Inicialização e verificações HTTP/navegador/link |
| `tests/` | Testes offline com ffmpeg real |
| `pyproject.toml`, `uv.lock`, `requirements.txt` | Dependências e versões travadas |
| `Dockerfile`, `compose.yaml`, `.env.example` | Execução de um único host |
| `docs/` | Relatório original, decisões atuais e validação |

## Limitações e próximos passos

Acesso por link é de melhor esforço e depende da rede e da plataforma. X não teve validação real. Não há publicação automática, contas, edição, seleção de carrossel, sincronização entre navegadores ou vários servidores. Metadados de duração inconsistentes, vídeos danificados e formatos incomuns podem ser recusados em vez de gerar uma saída incompleta. A normalização para 30 fps pode repetir/descartar quadros para ajustar cadência; limites de áudio usam a duração de apresentação MP4/AAC com priming/padding sinalizados no container. Não há promessa de reprodução idêntica bit a bit. Android/iPhone e o upload final no aplicativo WhatsApp precisam de validação em aparelhos reais.

Cloudflare (proxy/acesso, armazenamento ou processamento externo) e Vercel (frontend com worker externo) são opções futuras; **nenhum deles, nem R2, é necessário agora**. Separar serviço/fila/storage só se o uso justificar. O [relatório original](docs/planejamento-original.md) fica preservado como contexto; [as decisões implementadas](docs/decisoes.md) o substituem para esta versão.
