# go-charge-dados

Recolha automática e gratuita dos dados públicos usados pela ferramenta **GO-CHARGE Prospect**.

Corre nos servidores do GitHub (GitHub Actions), que não têm as restrições de rede da empresa, e guarda os resultados neste repositório. Uma vez por mês, o Claude lê estes ficheiros, recalcula o ranking de municípios e atualiza a ferramenta partilhada.

| O quê | Fonte | Frequência | Ficheiro |
|---|---|---|---|
| Todos os postos públicos de carregamento de Portugal (locais, tomadas, potências, operadores) | MOBI.E, via Ponto de Acesso Nacional (IMT), DATEX II | 1× por dia | `data/mobie/postos.json`, `data/mobie/resumo.json` |
| Ocupação da rede (tomadas a carregar / indisponíveis) | MOBI.E, estado em tempo real | de 3 em 3 horas | `data/mobie/ocupacao/AAAA-MM.json` |
| Locais candidatos (hotéis, supermercados, restauração, parques, empresas, concessionários) e postos | OpenStreetMap, via Overpass | 1× por mês (dia 2) | `data/osm/<zona>.json` |

## Criar o repositório (uma vez, ~10 minutos)

1. Entrar no GitHub com uma conta da equipa (ou criar uma, é gratuita).
2. **New repository** → nome `go-charge-dados` → visibilidade **Public** (os dados já são públicos; assim os minutos do GitHub Actions são ilimitados e o Claude consegue ler os ficheiros) → **Create repository**.
3. **Add file → Upload files** → arrastar **todo** o conteúdo desta pasta (incluindo a pasta `.github`, que pode estar oculta: no macOS `Cmd + Shift + .`; no Windows, "Mostrar itens ocultos") → **Commit changes**.
   - Se a pasta `.github` não for carregada (acontece com pastas ocultas): **Add file → Create new file**, escrever o nome `.github/workflows/mobie.yml`, colar o conteúdo do ficheiro com o mesmo nome desta pasta e **Commit**. Repetir para `.github/workflows/osm.yml`.
4. **Settings → Actions → General → Workflow permissions** → **Read and write permissions** → **Save**.
5. Separador **Actions** → se aparecer um aviso, clicar em **I understand my workflows, go ahead and enable them**.
6. Correr a primeira vez à mão: **Actions → MOBI.E (rede pública de carregamento) → Run workflow**. Depois **OpenStreetMap (locais candidatos) → Run workflow** (este demora 30–60 minutos).
7. Enviar ao Claude o endereço do repositório (ex.: `github.com/empresa/go-charge-dados`).

A partir daí, tudo corre sozinho.

## Verificar que está a funcionar

- Separador **Actions**: deve haver execuções com visto verde de 3 em 3 horas.
- `data/mobie/resumo.json`: data de recolha de hoje e o número de tomadas (deve rondar as 17 000 ou mais).
- Se uma execução falhar (vermelho), o GitHub envia um email ao dono do repositório. Falhas pontuais da MOBI.E ou do Overpass são normais; os dados anteriores mantêm-se.

## Licenças e atribuição

- Dados MOBI.E: Ponto de Acesso Nacional (NAP Portugal), de livre acesso.
- Dados OpenStreetMap: © colaboradores do OpenStreetMap, licença ODbL — qualquer uso público deve citar "© OpenStreetMap contributors".
