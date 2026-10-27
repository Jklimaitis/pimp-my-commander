# 🧙‍♂️ Pimp My Commander

Pimp My Commander une inteligência artificial e a sua coleção física de Magic: The Gathering. Em vez de sugerir cartas caras que você não tem, ele avalia seu deck (curva de mana, compra de cartas, ramp, remoções e plano de vitória) e recomenda melhorias priorizando o que já está nas suas pastas e caixas de sapato. Também identifica comandantes viáveis e gera rascunhos de decks de 100 cartas sob medida para o seu acervo. Construído em Python (FastAPI), TailwindCSS e Docker.

---

## ⚡ Principais Funcionalidades

- **Diagnóstico Tático com IA**: Análise profunda da curva de mana, compra de cartas, remoções, ramp e plano de jogo do seu deck, sugerindo adições e cortes táticos.
- **Priorização da Coleção**: Recomendações que dão prioridade a cartas que você já possui físicas em casa.
- **Importação Direta do ManaBox**: Cole o link público de qualquer deck do ManaBox ou texto/CSV da sua coleção.
- **Descobridor de Decks Viáveis**: Analisa sua coleção inteira, identifica comandantes viáveis e calcula porcentagens de prontidão com cruzamento em tempo real de staples do EDHREC.
- **Gerador de Rascunho Automático**: Monta um esqueleto de deck de 100 cartas sob medida para qualquer comandante da sua coleção.
- **Proxy e Cache Local de Imagens**: Exibição rápida das cartas via Scryfall com cache em disco e tolerância a falhas.

---

## 🚀 Como Rodar

### Pré-requisitos
- [Docker](https://www.docker.com/) e Docker Compose (recomendado) ou Python 3.11+

### 1. Clonar o repositório
```bash
git clone https://github.com/Jklimaitis/pimp-my-commander.git
cd pimp-my-commander
```

### 2. Configurar Variáveis de Ambiente
Copie o arquivo de exemplo e insira sua chave da API:
```bash
cp .env.example .env
```
Edite o arquivo `.env`:
```env
OPENCODE_API_KEY=sua_chave_aqui
OPENCODE_BASE_URL=https://opencode.ai/inference/go/v1
OPENCODE_MODEL=deepseek-v4.1-flash
OPENCODE_MAX_TOKENS=32000
```
O padrão é o DeepSeek pelo OpenCode Go. Para usar o Gemini 2.5 Pro via OpenCode Zen, troque o endereço e o modelo pelas linhas comentadas no `.env.example`.

### 3. Iniciar com Docker Compose
```bash
docker compose up -d
```
Acesse a aplicação no navegador em: **`http://localhost:8001`**

Por padrão o serviço escuta apenas em `127.0.0.1`. Para expor na rede local, defina `PMC_BIND` no `.env` (por exemplo `PMC_BIND=0.0.0.0` ou o IP desta máquina). Para desenvolvimento com recarga automática, acrescente `--reload` ao final do `command` no `docker-compose.yml`.

### 4. Rodar os testes
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```
Os testes usam um diretório temporário para a coleção, então não leem nem alteram a sua base local em `data/`.

---

## 🛠️ Stack Tecnológica

- **Backend**: Python 3.12+ / FastAPI / Uvicorn
- **Frontend**: HTML5 / TailwindCSS / Vanilla JavaScript (SPA reativa e sem dependências de build)
- **Integrações de MTG**:
  - [Scryfall API](https://scryfall.com/docs/api) (dados de cartas, imagens e normalização multilíngue)
  - [EDHREC API](https://edhrec.com/) (dados de sinergia, inclusão de staples e temas)
  - [ManaBox](https://manabox.app/) (importação de listas e links públicos)
- **Modelo de IA**: LLM OpenAI-compatível (OpenCode / Gemini 2.5 Pro)

---

## 🔒 Segurança e Privacidade

- O arquivo `.env` com suas chaves de API é ignorado pelo Git (`.gitignore`).
- A pasta `data/` com o JSON da sua coleção e decks construídos é mantida estritamente local no seu disco e não é versionada.
