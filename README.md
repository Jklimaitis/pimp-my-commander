# 🧙‍♂️ Pimp My Commander

Uma plataforma inteligente para análise tática, otimização e descoberta de decks de **Magic: The Gathering (Commander / EDH)** baseada na sua coleção física de cartas.

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
git clone https://github.com/SEU_USUARIO/pimp-my-commander.git
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
OPENCODE_BASE_URL=https://opencode.ai/zen/v1
OPENCODE_MODEL=gemini-2.5-pro
OPENCODE_MAX_TOKENS=32000
```

### 3. Iniciar com Docker Compose
```bash
docker compose up -d
```
Acesse a aplicação no navegador em: **`http://localhost:8000`**

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
