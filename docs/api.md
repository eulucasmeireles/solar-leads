# API do Solar Leads (v1)

Para as suas automações (n8n, GHL, scripts) lerem listas e retirarem contatos sem usar a senha do sistema.

- **Endereço:** `https://SEU-ENDERECO/api/v1`
- **Chave:** crie em **Base → Chaves de API**. Ela aparece uma única vez; guarde-a no cofre da automação.
- **Como enviar:** cabeçalho `Authorization: Bearer SUA_CHAVE` (ou `X-API-Key: SUA_CHAVE`). Chave na URL não é aceita.
- **Limite:** 300 chamadas por minuto por chave. Acima disso a resposta é `429` com `Retry-After`.
- **O que a chave abre:** só `/api/v1`. Ela não entra na página, não cria nem revoga chaves e não apaga nada.
- **Erros:** JSON `{"detail": "mensagem"}` com `400`, `401` (chave), `404`, `409` (lista ainda gerando), `422` (corpo incompleto), `429`.

## Testar a chave

    curl -H "Authorization: Bearer $CHAVE" https://SEU-ENDERECO/api/v1/

Responde o nome da chave e a relação de rotas.

## Rotas

| Método | Caminho | Para quê |
|---|---|---|
| GET | `/base` | Mês da base da Receita, ANEEL, WhatsApp verificados |
| GET | `/cidades?q=montes` | Busca de município |
| GET | `/atividades?q=padaria` | Busca de atividade econômica |
| GET | `/campos` | Campos que cada contato traz |
| GET | `/publico?palavra=solar&palavra=fotovolt&uf=MG` | Quantas empresas têm essas palavras no nome, por atividade e estado |
| GET | `/listas` | Todas as listas |
| POST | `/listas` | Gera uma lista |
| GET | `/listas/{id}` | Status (`na_fila`, `gerando`, `pronta`, `erro`), etapa e resumo |
| GET | `/listas/{id}/filtros` | Opções de cada filtro, com contagem |
| GET | `/listas/{id}/contatos` | Contatos, com filtros e paginação |
| GET | `/listas/{id}/exportar` | CSV para o GHL, com os mesmos filtros |
| POST | `/listas/{id}/retirar` | Entrega os próximos contatos e já os guarda numa pasta |
| GET | `/pastas` | Todas as pastas |
| POST | `/pastas` | Cria uma pasta: `{"nome": "..."}` |
| GET | `/pastas/{id}` | Uma pasta |
| GET | `/pastas/{id}/contatos` | Contatos da pasta |
| POST | `/pastas/{id}/adicionar?lista_id=...&quantidade=100` | Guarda na pasta os N primeiros contatos de uma lista |
| GET | `/pastas/{id}/exportar` | CSV da pasta para o GHL |

## Retirar contatos (o caminho mais usado)

Pega os próximos contatos da lista que **ainda não estão em nenhuma pasta**, guarda na pasta indicada
(criando-a se não existir) e devolve esses contatos. Chamar de novo traz os seguintes; ninguém sai duas vezes.

    curl -X POST "https://SEU-ENDERECO/api/v1/listas/ID_DA_LISTA/retirar?tipo_whatsapp=pessoal&tipo_whatsapp=business" \
      -H "Authorization: Bearer $CHAVE" -H "Content-Type: application/json" \
      -d '{"pasta": "Disparo semana 47", "quantidade": 100}'

Resposta:

    {"pasta": {"id": 7, "nome": "Disparo semana 47", "criada_agora": true},
     "retirados": 100, "restantes": 1124, "linhas": [ {"primeiro_nome": "...", "telefone": "+55...", ...} ]}

`quantidade` vai de 1 a 1000. Quando `retirados` vier 0, a lista acabou para aquele filtro.

## Filtros de `/contatos`, `/exportar` e `/retirar`

Todos opcionais; os que aceitam vários valores se repetem na URL (`uf=MG&uf=SP`).

| Parâmetro | Valores |
|---|---|
| `faixa` | `A` (contato forte), `B` (médio) |
| `tipo_whatsapp` | `pessoal`, `business`; vazio = não verificado |
| `situacao` | `ativa`, `baixada`, `inapta`, `suspensa`, `nula` |
| `papel` | `dono principal`, `outro sócio` |
| `uf`, `cidade`, `bairro`, `atividade` | como aparecem em `/listas/{id}/filtros` |
| `solar`, `oportunidade`, `aptidao_min` | só fazem sentido em listas de público "dono" |
| `com_email=1` | só quem tem e-mail |
| `busca` | texto no nome, empresa, CNPJ, telefone, bairro ou cidade |
| `pasta` | `fora` = quem não está em nenhuma pasta; um id = só quem está naquela |
| `ordem` | `relevancia` (padrão), `chance`, `aptidao`, `nome`, `empresa` |
| `pagina`, `por_pagina` | paginação de `/contatos` (até 500 por página) |

`/contatos` responde `{"total", "pagina", "por_pagina", "resumo", "linhas": [...]}`.

## Gerar uma lista

Por cidade:

    curl -X POST https://SEU-ENDERECO/api/v1/listas -H "Authorization: Bearer $CHAVE" \
      -H "Content-Type: application/json" -d '{"uf": "MG", "cidade": "Montes Claros"}'

Por nome da empresa, num estado ou no Brasil (`uf` vazio):

    -d '{"palavras": ["solar", "fotovolt"], "uf": "", "cnaes": ["4321500"], "so_ativas": true, "publico": "empresa"}'

Campos opcionais: `cnaes` (atividades), `so_ativas`, `incluir_mei`, `so_locais` (padrão `true`),
`publico` (`dono` ou `empresa`). A resposta traz `{"id": "..."}`; consulte `/listas/{id}` até o `status` ser `pronta`
(cerca de um minuto).
