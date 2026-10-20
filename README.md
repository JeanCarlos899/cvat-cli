# Getter CVAT CLI

CLI para os fluxos de projetos, tasks, frames e anotações no CVAT. Usa o SDK oficial `cvat-sdk==2.62.0`. A versão 0.2 adiciona download direto de frames, geometrias além de retângulos e acréscimos de anotações em frames vazios, preservando trabalho existente.

## Instalação

Python 3.10 ou superior. Em um checkout deste repositório:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/getter-cvat --help
.venv/bin/getter-cvat --version
```

Os exemplos seguintes usam `getter-cvat`; ative a venv ou use o caminho completo acima. Também pode instalar com `pipx install .`. A instalação gera o comando sem depender de um launcher com o caminho do código-fonte.

## Perfil e credenciais

Perfil padrão: `~/.config/getter-cvat/.env`. Use `perfil.env.example` apenas como modelo e mantenha o perfil preenchido fora do repositório:

```sh
mkdir -p ~/.config/getter-cvat
chmod 700 ~/.config/getter-cvat
cp perfil.env.example ~/.config/getter-cvat/.env
chmod 600 ~/.config/getter-cvat/.env
```

Preencha `CVAT_HOST` (HTTPS), `CVAT_USERNAME` e opcionalmente `CVAT_PASSWORD`. Senha vazia gera prompt interativo. Não passe senha em argumentos. Variáveis de ambiente têm prioridade sobre o perfil; `--env /caminho/perfil.env` seleciona outro arquivo; `--host https://cvat.example.org` altera o servidor. `--org slug` seleciona a organização; sem ele é usado o workspace pessoal.

As opções globais vêm antes do comando. Erros HTTP não imprimem objetos de requisição nem headers de autenticação. O perfil contém texto claro, tem permissões verificadas no POSIX e não deve ser compartilhado. Nenhuma credencial é distribuída neste repositório.

## Conexão, projetos, tasks e jobs

```sh
getter-cvat ping
getter-cvat formats
getter-cvat projects
getter-cvat project-create "Nova demo" --labels classes.json
getter-cvat project-rename 150 "Nome revisado"
getter-cvat tasks --project 150
getter-cvat task-create "Amostras" --project 150 --files imagens/
getter-cvat task-create "Video" --project 150 --files video.mp4 --step 32 --quality 100
getter-cvat task-upload 123 --files imagens/
getter-cvat task-info 123
getter-cvat labels 123
getter-cvat jobs --task 123
getter-cvat job-info 456
```

`classes.json` é um array de labels no schema CVAT, com seus atributos. `--files` aceita arquivos, ZIP ou diretórios imediatos de imagens/vídeos. Recursos e basenames repetidos são recusados; use nomes numerados com zeros à esquerda. `task-create` verifica duplicatas e mostra o ID criado antes do upload. Se falhar, confira esse ID e use `task-upload` somente se a task estiver vazia; não recrie cegamente.

## Baixar frames e anotações

```sh
getter-cvat frames 123 --out dataset/
getter-cvat frames 123 --frames 0,2,5:9 --out selecao/
getter-cvat export 123 anotacoes.zip --format "CVAT for images 1.1"
getter-cvat export 123 dataset.zip --images --format "CVAT for images 1.1"
getter-cvat raw-export 123 anotacoes_raw.json
```

`frames` baixa os bytes dos frames sem recodificar as imagens. A saída deve ser uma pasta nova. Ela só é disponibilizada ao concluir todos os downloads e contém `frames_manifest.json` com índice CVAT, nome, tamanho e SHA-256 de cada arquivo. A qualidade padrão é `original`; `--quality compressed` pede a versão compactada ao servidor. Quando o servidor devolve JPEG para um PNG original, o nome recebe extensão `.jpg`; `source_name` no manifesto mantém o nome da task. Em tasks de vídeo cuja metadata contém somente um frame comum, os arquivos recebem nomes `frame_000000.jpg`, etc.; o índice continua sendo o índice CVAT. Imagens com nomes aninhados preservam o caminho relativo, com proteção contra traversal e colisões de nomes.

`--frames` usa índices CVAT zero-based, e os intervalos são inclusivos. Não são necessariamente os números presentes nos nomes dos arquivos de origem. Frames apagados não são baixados implicitamente: selecione IDs ativos. Downloads/exports não substituem arquivos existentes.

`raw-export` salva a estrutura do SDK, IDs de labels e anotações para auditoria/backup. Esse formato não é a entrada de `append`; use JSON por nome de label ou `import-missing` com XML/ZIP.

## Anotar e importar em task vazia

```sh
getter-cvat annotate 123 --json examples/polygons.json
getter-cvat import 123 anotacoes.zip --format "CVAT 1.1"
```

Esses dois comandos recusam tasks que já têm qualquer anotação. `annotate` aceita `shapes` e `tags`. Geometrias JSON: `rectangle` (4 coordenadas), `polygon` (3 ou mais pares), `polyline` (2 ou mais pares), `points` (1 ou mais pares), `ellipse` (centro, extremo direito e extremo superior em 4 coordenadas) e `cuboid` (8 pares). Labels e atributos são referenciados pelo nome e resolvidos no destino; a origem salva é `auto`. Polígonos precisam de três vértices distintos e área não nula. Tracks, skeletons e máscaras RLE não são aceitos nesse JSON; importe um formato CVAT compatível em uma task vazia quando precisar desses recursos.

Cada shape recebe `frame`, `type`, `label`, `points` e opcionalmente `attributes`, `group`, `occluded`, `outside`, `z_order` e `rotation`. Tags usam `frame`, `label`, `attributes` e opcionalmente `group`. Não inclua IDs de shapes/labels do servidor no JSON de entrada.

## Acrescentar só nos frames ainda vazios

Primeiro veja a prévia; nenhum envio ocorre por padrão:

```sh
getter-cvat append 123 --json propostas.json
getter-cvat import-missing 123 anotacoes_cvat.zip --dry-run
```

Para enviar:

```sh
getter-cvat append 123 --json propostas.json --apply \
  --backup backups/antes.json --receipt receipts/envio.json

getter-cvat import-missing 123 anotacoes_cvat.zip --apply \
  --backup backups/antes_xml.json --receipt receipts/envio_xml.json
```

O CLI reserva todos os frames que já têm shapes/tags, preserva também frames potencialmente ocupados por tracks e acrescenta apenas nos restantes. `import-missing` aceita CVAT **for images** XML ou ZIP, mapeia imagens pelo nome completo e suporta boxes, polygons, polylines, points, ellipses e tags. XML de tracks deve ser importado em uma task vazia com `import`. A prévia mostra frames preservados, frames que receberiam adições e quantidades.

O envio cria backup antes, confere mudanças concorrentes, usa apenas `action=create`, salva recibo e relê o servidor para verificar o conteúdo original e as adições. Backup e recibo precisam ser caminhos novos. O snapshot remoto posterior fica ao lado do recibo, com sufixo `.saved.json`.

Se uma requisição falhar depois de começar, não repita cegamente: confira o recibo, `task-info` e um novo `raw-export`. O servidor pode ter concluído apesar de um timeout. Os arquivos existentes bloqueiam reutilizar a tentativa. Um resultado com zero adições não modifica a task nem cria backup.

Detalhes e limites de concorrência: [decisão sobre acréscimos](docs/decisions/001-additive-annotations.md).

## Amostragem local de vídeo

```sh
getter-cvat sample video.mp4 --count 200 --out amostras/
```

Requer `ffmpeg` e `ffprobe` no PATH. Produz exatamente N imagens uniformes incluindo primeiro e último frame, com manifesto. No vídeo VFR, o tempo usa FPS nominal e não equivale a timestamp VFR exato. Esse comando não acessa o CVAT.

## Escopo

Não há exclusão de projetos/tasks ou sobrescrita de anotações existentes. O CLI não treina modelos, não executa SAM e não suaviza contornos: ele baixa dados e transfere propostas preparadas por outros processos. Exportar YOLO de detecção pode perder atributos; para preservar toda a anotação, use CVAT 1.1. Não representa toda a API administrativa do CVAT.

## Desenvolvimento e verificação

```sh
.venv/bin/python -m pip install '.[dev]'
.venv/bin/python -m unittest -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m compileall -q cvat_demo.py cvat_ops.py sampling.py
.venv/bin/python -m build
```

Testes de acréscimos usam servidor simulado e verificam preservação, concorrência e recibo de falha; não precisam de credenciais. Consulte `formats` no seu servidor para os nomes exatos dos formatos disponíveis. SDK: https://docs.cvat.ai/docs/api_sdk/sdk/highlevel-api/
