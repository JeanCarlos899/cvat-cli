# Acréscimos preservam frames anotados

`annotate` e `import` mantêm a regra original: só podem operar em tasks sem shapes, tags ou tracks. Não são atalhos para substituir trabalho existente.

`append` (JSON por nomes de labels/atributos) e `import-missing` (CVAT for images XML/ZIP) são operações distintas. A prévia é o comportamento padrão. `--apply` exige caminhos novos para backup e recibo.

O frame inteiro fica reservado quando já existe qualquer shape ou tag, independentemente da classe e da origem manual/automática. Tracks reservam conservadoramente do primeiro keyframe até o fim da task, inclusive elementos aninhados, para evitar colisão com interpolação. Isso pode deixar frames que poderiam receber novas anotações sem acréscimos; é uma escolha deliberada de preservação.

Os IDs de frame no JSON são índices CVAT zero-based. Importações XML são mapeadas pelo nome completo da imagem, não pelo atributo `image.id` do arquivo. Imagens ausentes/duplicadas são recusadas. Não há fallback pelo basename, porque isso pode associar frames diferentes por engano.

O envio usa PATCH com action=create, nunca substituição do conjunto. Há uma segunda leitura antes do envio e comparação integral do estado com a cópia de segurança. Depois, o CLI lê novamente, confirma IDs e conteúdo das anotações antigas e confere as adições (incluindo geometria/atributos) sem depender da ordem da resposta.

Essa segunda leitura reduz, mas não elimina, a janela de edição concorrente entre leitura e PATCH. Não é um bloqueio transacional do servidor. Evite editar os mesmos frames durante o envio.

O recibo é criado antes da requisição. Falha/timeout depois de iniciar a requisição pode deixar o estado remoto incerto. O CLI não repete automaticamente o PATCH; o backup e recibo existentes bloqueiam reutilizar a mesma tentativa. Leia novamente a task antes de preparar outra operação. A verificação posterior pode detectar mudanças concorrentes; nunca tenta revertê-las ou apagar anotações automaticamente.

O fluxo adicional não muda contratos do CVAT, frontend ou app. Não depende de SAM ou filtros de suavização: esses processos entregam os polígonos; o CLI só valida, transfere e verifica.
