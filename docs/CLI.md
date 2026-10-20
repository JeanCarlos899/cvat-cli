# Contrato da CLI `cvat`

Integração com `preannotate` (0.5): propostas podem incluir `frame_names`, array com todos os nomes de imagens na ordem dos índices CVAT. `append` recusa o envio se a ordem ou os nomes divergirem da task de destino. O exporter `preannotate export-proposals` fornece esse campo automaticamente; propostas antigas sem ele continuam aceitas, portanto confira manualmente a task. `import-missing` continua fazendo o vínculo por nomes do XML. `labels` retorna o schema serializado pelo SDK; `projects --project ID` filtra pelo ID indicado.

Este arquivo é a referência operacional para pessoas e agentes. O único ponto de entrada é:

```sh
cvat [opções-globais] COMANDO [argumentos]
```

Todos os caminhos são locais. IDs são inteiros do CVAT. A saída de consultas é JSON em `stdout`; progresso do SDK e erros ficam em `stderr`. Todo caminho de saída deve ser novo: a CLI não substitui arquivos nem pastas existentes. Um comando que modifica o CVAT só o faz quando descrito abaixo.

## Opções globais e autenticação

| Entrada | Tipo/padrão | Uso |
| --- | --- | --- |
| `--env ARQUIVO` | `~/.config/cvat/.env` | Perfil `KEY=value`, com permissão 0600 no POSIX. |
| `--host URL` | `CVAT_HOST` | URL HTTPS do servidor; tem prioridade sobre o perfil. |
| `--org SLUG` | vazio | Organização CVAT; vazio usa o espaço pessoal. |
| `--version` | — | Imprime a versão e encerra. |

O perfil requer `CVAT_HOST` e `CVAT_USERNAME`; `CVAT_PASSWORD` é opcional e, se ausente, a senha é pedida no terminal. Variáveis `CVAT_*` do ambiente têm prioridade sobre o perfil. Nunca passe senha como argumento. Credenciais não aparecem nas saídas nem nos arquivos gerados.

## Consulta e descoberta

| Comando | Entradas | Saída | Efeito remoto |
| --- | --- | --- | --- |
| `ping` | nenhuma | host, usuário e `connected` | autentica, sem mudar dados |
| `formats` | nenhuma | importadores/exportadores habilitados no servidor | nenhum |
| `projects [--project ID]` | filtro opcional | lista de projetos | nenhum |
| `tasks [--project ID]` | filtro opcional | id, nome, projeto e tamanho das tasks | nenhum |
| `jobs --task ID` | ID da task | jobs, faixa de frames, estágio e estado | nenhum |
| `job-info ID` | ID do job | job, task e faixa de frames | nenhum |
| `task-info ID` | ID da task | metadados, jobs, contagens e nomes de frames | nenhum |
| `labels ID` | ID da task | schema de labels do CVAT | nenhum |

`projects --project` é aceito como filtro de compatibilidade, mas o servidor pode não aplicar filtro a projetos. Use o ID retornado para comandos posteriores.

## Projetos e dados de tasks

| Comando | Entradas | Saída | Efeito remoto |
| --- | --- | --- | --- |
| `project-create NOME --labels ARQUIVO.json` | nome e array de labels no schema CVAT | ID e URL | cria projeto; recusa nome existente no workspace |
| `project-rename ID NOME` | ID e novo nome | novo nome | renomeia o projeto |
| `task-create NOME --project ID --files ITENS... [--step N] [--quality 1..100]` | arquivos, ZIPs ou diretórios imediatos | ID criado e tamanho | cria task e envia dados; `step` padrão 1, `quality` padrão 100 |
| `task-upload ID --files ITENS...` | task vazia e arquivos | ID e tamanho | envia dados apenas se a task ainda não tiver frames |

`--files` aceita imagem/vídeo suportado, ZIP ou diretório imediato contendo `.jpg`, `.jpeg`, `.png`, `.bmp`, `.webp`, `.mp4`, `.mov`, `.mkv` ou `.avi`. Nomes-base duplicados são recusados. Em vídeo, `--step N` mantém um a cada N frames. `task-create` imprime o ID antes do upload: se o upload falhar, investigue essa task e use `task-upload` somente se ela estiver vazia.

### Schema de labels

`--labels` recebe um array JSON aceito pelo CVAT, por exemplo:

```json
[
  {
    "name": "smoke",
    "attributes": [
      {
        "name": "density",
        "input_type": "select",
        "values": ["light", "dense"],
        "mutable": true
      }
    ]
  }
]
```

Use `cvat labels TASK_ID` para obter o schema efetivo de uma task antes de produzir propostas automáticas.

## Download e exportação

| Comando | Entradas | Saída | Efeito remoto |
| --- | --- | --- | --- |
| `frames ID --out PASTA [--frames LISTA] [--quality original\|compressed]` | ID da task, pasta nova; lista como `0,2,5:9` | imagens sem recodificação e `frames_manifest.json` | nenhum |
| `export ID ARQUIVO.zip [--format FORMATO] [--images]` | ID, arquivo novo; formato padrão `CVAT for images 1.1` | arquivo exportado pelo CVAT | nenhum |
| `raw-export ID ARQUIVO.json` | ID e JSON novo | labels, IDs e anotações brutas | nenhum |
| `sample VIDEO --out PASTA [--count N]` | vídeo local, pasta nova; N padrão 200 | JPEGs e `manifest.json` | nenhum; requer `ffmpeg`/`ffprobe` |

`frames` usa índices zero-based do CVAT e intervalos inclusivos. A saída só aparece quando todos os downloads terminarem. O manifesto associa índice, nome usado, nome de origem, tamanho e SHA-256. `original` é a qualidade padrão. Quando o CVAT devolve JPEG para uma origem PNG, o arquivo recebe `.jpg` e `source_name` preserva o nome da task.

Para baixar anotações **com imagens** em CVAT 1.1:

```sh
cvat export 2326 dataset.zip --images --format "CVAT for images 1.1"
```

Use `cvat formats` para obter os nomes exatos disponíveis no servidor antes de selecionar outro `--format`.

## Anotações em task vazia

| Comando | Entradas | Saída | Efeito remoto |
| --- | --- | --- | --- |
| `annotate ID --json PROPOSTAS.json` | task vazia e JSON no contrato abaixo | ID e quantidade verificada | substitui as anotações de uma task comprovadamente vazia |
| `import ID ARQUIVO [--format FORMATO]` | task vazia e dataset aceito pelo formato | progresso do SDK | importa dataset; padrão `CVAT 1.1` |

Esses comandos recusam uma task que já tenha shapes, tags ou tracks. Para acrescentar predições a uma task anotada, use a seção seguinte.

### Contrato de `PROPOSTAS.json`

O documento é um objeto. Campos aceitos no nível raiz: `shapes`, `tags`, `tracks` e `version`; `tracks` deve ser ausente ou vazio. Labels e atributos usam **nomes**, nunca IDs do servidor.

```json
{
  "shapes": [
    {
      "label": "smoke",
      "frame": 3,
      "type": "polygon",
      "points": [10.0, 20.0, 30.0, 20.0, 25.0, 40.0],
      "attributes": {"density": "light"},
      "group": 0,
      "occluded": false,
      "outside": false,
      "z_order": 0,
      "rotation": 0
    }
  ],
  "tags": [{"label": "reviewed", "frame": 3, "attributes": {}}]
}
```

`frame` é inteiro zero-based e precisa estar na task. `attributes` é opcional e usa `{nome: valor}`. Valores são validados contra o schema: `select`/`radio` precisam ser uma das opções; `checkbox` aceita `true`/`false`; `number` respeita mínimo, máximo e passo.

Shapes aceitos e tamanhos de `points`: `rectangle` com 4 (`xtl,ytl,xbr,ybr`), `polygon` com pelo menos 6, `polyline` com pelo menos 4, `points` com pelo menos 2, `ellipse` com 4 (`cx,cy,x-direita,y-superior`) e `cuboid` com 16. Todos precisam ser números finitos; retângulos e elipses precisam ter dimensões positivas; polígonos precisam de três vértices distintos e área não nula. `group` e `z_order` são inteiros; `occluded`/`outside` são booleanos; `rotation` é número finito.

## Acrescentar predições sem substituir trabalho manual

| Comando | Entradas | Saída | Efeito remoto |
| --- | --- | --- | --- |
| `append ID --json PROPOSTAS.json [--dry-run]` | contrato JSON acima | prévia com frames preservados e adições | nenhum |
| `append ID --json PROPOSTAS.json --apply --backup B.json --receipt R.json` | JSON e três caminhos | backup, recibo e snapshot verificado | cria somente shapes/tags em frames vazios |
| `import-missing ID ANNOTATIONS.xml\|zip [--dry-run]` | XML/ZIP CVAT for images 1.1 | prévia | nenhum |
| `import-missing ID ARQUIVO --apply --backup B.json --receipt R.json` | XML/ZIP e caminhos novos | backup, recibo e snapshot verificado | cria somente shapes/tags em frames vazios |

Sem `--apply`, a prévia é o padrão. Com `--apply`, `--backup` e `--receipt` são obrigatórios, distintos e novos. A CLI faz backup, relê para detectar alteração concorrente, usa somente a ação remota `create`, relê o resultado e verifica que nenhuma anotação prévia mudou. O recibo ganha também `R.json.saved.json` com o snapshot posterior.

Frames que já têm shapes/tags, e a faixa potencialmente ocupada por tracks, são protegidos. O XML de `import-missing` é mapeado pelo nome completo de cada imagem, não pelo ID da exportação; aceita `box`, `polygon`, `polyline`, `points`, `ellipse` e `tag`. Ele não aceita tracks, DTD nem entidades XML. Para tracks ou máscaras RLE, importe em uma task vazia com `import`.

Se uma chamada `--apply` falhar depois de começar, não execute novamente: consulte o recibo e rode `raw-export` para reconciliar o estado remoto. A CLI impede reutilizar os caminhos daquela tentativa.

## Limites deliberados

A CLI não apaga projetos, tasks ou anotações e não altera uma anotação manual existente. Não treina modelos, executa SAM ou suaviza máscaras. Essas etapas produzem arquivos de proposta que entram em `append` ou `import-missing`.
