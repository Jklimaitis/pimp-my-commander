import os
import tempfile

# Estado isolado por execução: a coleção é gravada em um diretório temporário,
# fora do checkout, para que os testes nunca toquem em dados reais.
os.environ["PMC_DATA_DIR"] = tempfile.mkdtemp(prefix="pmc-test-")

# Chave fictícia: nenhum teste faz chamada de rede à API do agente.
os.environ.setdefault("OPENCODE_API_KEY", "test-key")
os.environ.setdefault("OPENCODE_BASE_URL", "https://example.invalid/v1")
