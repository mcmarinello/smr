# Resumo do Curso de Deploy, Monitoria e Observabilidade com IA

**Fontes:**
- Aula 03: Deploy, monitoria e observabilidade de sistemas com IA (parte 01)
- Aula 04: Deploy, monitoria e observabilidade de sistemas com IA (parte 02)
- Aula 05: Integrações e Automações de IA com Hermes Agent (parte 1)

**Objetivo:** Deploy replicável de sistemas Django com Docker Swarm + Traefik + Cloudflare + Prometheus/Grafana + MCP integrado a IA.

---

## 1. Docker Swarm — Passos de Deploy

### Arquitetura Alvo
- **Django App** (container)
- **Celery Worker** (tarefas pesadas, IA)
- **Celery Beat** (agendamento)
- **Redis** (cache + broker de filas Celery)
- **PostgreSQL** (banco de dados)
- **Traefik** (reverse proxy / load balancer / SSL)
- **Prometheus, Grafana, Loki, Promtail, Node Exporter, cAdvisor** (monitoria)

### Pré-requisitos no Servidor
1. Criar VPS (recomendado: Rockinger KVM2+ com mínimo 2 CPUs, Ubuntu 24.04 LTS)
2. Gerar chave SSH e adicionar na VPS (desabilita login por senha)
3. Criar usuário Linux dedicado (ex: `deploy`) e adicionar ao grupo `sudo`
4. Copiar chaves SSH autorizadas do root para o novo usuário
5. Acessar via `ssh deploy@<IP_VPS>`

### Configuração do Servidor (uma vez na vida)
```bash
# Atualizar sistema
sudo apt update && sudo apt upgrade -y

# Instalar utilitários
sudo apt install git htop net-tools ufw fail2ban -y

# Configurar timezone
sudo timedatectl set-timezone America/Sao_Paulo

# Configurar Fail2ban (anti brute force)
sudo systemctl enable fail2ban && sudo systemctl start fail2ban

# Criar swap (4GB recomendado para VPS de 16GB RAM)
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Otimizar conexões Linux (tuning de produção)
# Aumentar limite de conexões simultâneas de 20k para 65k por porta
# Aumentar limite de arquivos abertos (~2M)

# Configurar firewall (UFW)
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS
sudo ufw enable
```

### Instalar Docker + Docker Swarm
```bash
# Instalar Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Tuning do Docker (logs, métricas)
# Configurar daemon.json com limites de log e porta 9323 para métricas

# Inicializar Docker Swarm
docker swarm init --advertise-addr <IP_VPS>

# Adicionar labels ao node (organização)
docker node update --label-add infra=true --label-add app=true <NODE>
```

### Autenticar no GitHub Container Registry (GHCR)
```bash
# Gerar chave SSH para o servidor
ssh-keygen -t ed25519 -C "deploy@vps" -f ~/.ssh/id_ed25519 -N ""

# Cadastrar chave pública no GitHub (Settings > SSH Keys)

# Configurar git para usar SSH
# Criar ~/.ssh/config com Host github.com

# Gerar token de acesso clássico no GitHub (Settings > Developer Settings > Tokens)
# Permissões: write:packages, read:packages, delete:packages

# Login no GHCR
echo <TOKEN> | docker login ghcr.io -u <GITHUB_USER> --password-stdin
```

### Clonar e Build
```bash
# Clonar projeto
cd ~/deploy
git clone git@github.com:<user>/<repo>.git

# Autenticar GitHub no servidor (via SSH)
# Criar ~/.ssh/config:
#   Host github.com
#     HostName github.com
#     User git
#     IdentityFile ~/.ssh/id_ed25519

# Exportar variáveis e build da imagem
export GITHUB_USER=<seu_usuario>
export IMAGE_NAME=<nome_do_projeto>
export VERSION=$(git -C ~/deploy/<repo> rev-parse --short HEAD)

# Build da imagem
docker build -t ghcr.io/$GITHUB_USER/$IMAGE_NAME:$VERSION \
             -t ghcr.io/$GITHUB_USER/$IMAGE_NAME:latest .

# Push para o registro
docker push ghcr.io/$GITHUB_USER/$IMAGE_NAME:$VERSION
docker push ghcr.io/$GITHUB_USER/$IMAGE_NAME:latest
```

### Docker Stack Deploy
```bash
# Criar redes Docker
docker network create --driver overlay traefik-public
docker network create --driver overlay scc-v1-internal
docker network create --driver overlay scc-v1-ingress

# Criar secrets (Cloudflare API token)
docker secret create cloudflare-api-token <token>

# Deploy
docker stack deploy -c docker-stack.yml scc-v1

# Verificar status
docker stack services scc-v1
docker service ls
```

### Réplicas e Escala Horizontal
- **App Django:** mínimo 2 réplicas (para rolling update sem downtime)
- **Celery Worker:** mínimo 2 réplicas
- **Celery Beat:** 1 réplica (agendador)
- **PostgreSQL:** 1 (escalar conforme necessidade)
- **Redis:** 1
- **Traefik:** 1 (escalar conforme necessidade)

### Script de Deploy (deploy.sh)
O script automatiza:
1. Build da nova imagem com tag do hash do último commit
2. Push para o GHCR
3. Rolling update das réplicas (uma por vez, sem downtime)
4. Verificação de saúde pós-deploy

---

## 2. Traefik — Configuração

### Por que Traefik e não Nginx?
- Traefik não é apenas um web server, é uma aplicação completa
- Gerencia SSL automaticamente via Let's Encrypt + Cloudflare
- Load balancing nativo
- Dashboard de métricas de rede integrado
- Biblioteca de plugins (Traefik Labs)
- Limpeza automática de cache do Cloudflare via API

### Configuração no docker-stack.yml
- Traefik é o único serviço na rede `traefik-public` (acessível externamente)
- Integra com Cloudflare via API token (CF_DNS_API_TOKEN)
- Gerencia certificados SSL via Let's Encrypt com desafio DNS-01
- Dashboard habilitado com autenticação (htpasswd)

### Dashboard do Traefik
- Acessível via subdomínio (ex: `traefik.scc.digital`)
- Mostra: rotas, serviços, middlewares, erros, métricas de rede
- Senha gerada via `htpasswd` e configurada nos labels
- Plugins disponíveis: monitoramento de brute force, integração com Cloudflare

### Redes Docker Swarm
- **traefik-public:** Rede exposta à internet (apenas Traefik)
- **<projeto>-internal:** Rede interna (app, DB, Redis, Celery)
- **<projeto>-ingress:** Rede para acesso externo (Celery Worker precisa acessar APIs externas como OpenAI)

---

## 3. Cloudflare DNS-01 TLS

### Passo a Passo
1. Criar conta no Cloudflare (plano gratuito)
2. Adicionar domínio no Cloudflare
3. Criar registros DNS:
   - `scc.digital` → tipo A → IP do servidor (somente DNS)
   - `*.scc.digital` → tipo A → IP do servidor (somente DNS) — para subdomínios
4. Trocar Name Servers no registrador (ex: Rockinger) pelos do Cloudflare
5. Aguardar propagação (2-3 horas)

### Integração Traefik + Cloudflare
1. Gerar API Token no Cloudflare (permissão: Zone > DNS > Edit, domínio específico)
2. Criar secret Docker com o token:
   ```bash
   echo -n "<TOKEN>" | docker secret create cloudflare-api-token -
   ```
3. Traefik usa o token para:
   - Gerenciar registros DNS (desafio DNS-01 para SSL)
   - Limpar cache do Cloudflare automaticamente no deploy
   - Obter certificados SSL via Let's Encrypt

### Variáveis de Ambiente Importantes (Traefik)
- `CF_DNS_API_TOKEN` (lido do Docker secret)
- `ACME_EMAIL` (email para Let's Encrypt)
- `ACME_DNSCHALLENGE_RESOLVERS` (DNS do Cloudflare)
- `ACME_DNSCHALLENGE_PROVIDER: cloudflare`
- `TRAEFIK_CERTIFICATESRESOLVERS_LETSENCRYPT_ACME_DNSCHALLENGE: true`

---

## 4. Monitoria e Observabilidade

### Stack de Monitoria (segunda stack separada)
- **Prometheus:** Coleta de métricas de todos os containers e da máquina
- **Grafana:** Dashboard visual para métricas e logs
- **Loki:** Armazenamento de logs (base de dados de logs com linguagem de query)
- **Promtail:** Coletor de logs (lê stdout/stderr dos containers e envia ao Loki)
- **Node Exporter:** Métricas da máquina inteira (CPU, RAM, rede, disco)
- **cAdvisor:** Métricas por container individual (CPU, memória, rede)
- **Grafana MCP:** Servidor MCP do Grafana para acesso via IA

### Fluxo de Métricas
```
Node Exporter + cAdvisor → Prometheus → Grafana
         ↓ (via endpoints internos)
Prometheus coleta métricas em tempo real
Grafana consulta Prometheus para dashboards
```

### Fluxo de Logs
```
Containers → Promtail → Loki → Grafana
```

### Configuração do Prometheus
- Jobs configurados para: Node Exporter, cAdvisor, Django App (via django-prometheus), Traefik
- Retenção de métricas: 30 dias (configurável)
- Comunicação via rede interna do Docker Swarm

### Configuração do Loki
- Retenção de logs: 168 horas (7 dias, configurável)
- Linguagem de query proprietária para filtrar logs
- Acessível pelo Grafana

### Dashboards Recomendados no Grafana
1. **Dashboard personalizado SCC:** Métricas específicas do projeto (requisições por método, status HTTP, latência por rota, erros de query, CPU por container)
2. **Node Exporter Full (ID 1860):** Métricas detalhadas da VPS (CPU, RAM, disco, rede)
3. **cAdvisor Exporter:** Métricas por container individual
4. **Loki Grafana Overview:** Visão geral dos logs cruzados com métricas da máquina
5. **Django Dashboard (ID 1718):** Métricas específicas do Django (endpoint performance, queries do banco, latência)

### django-prometheus
- Biblioteca que expõe endpoints de métricas do Django para o Prometheus
- Instalar: `pip install django-prometheus`
- Adicionar em `INSTALLED_APPS` e `ROOT_URLCONF`
- Métricas: latência por endpoint, tempo de queries, erros de conexão com banco

### Scripts de Deploy de Monitoria
- Script separado para deploy do monitoring stack
- Configurações específicas na pasta `monitoring/`
- Inclui: prometheus.yml, grafana dashboards JSON, alertas

---

## 5. Integrações e Automações com Hermes Agent

### MCP Server do Django (django-mcp-server)
- Plugin que sobe um servidor MCP junto com o Django
- Permite à IA acessar toda a base de dados do sistema via linguagem natural
- Autenticado via superusuário do Django (Base64 do user:pass)
- Funcionalidades: CRUD completo de todas as entidades, métricas de uso, relatórios, manipulação em tempo real

### Como Implementar
1. Instalar: `pip install django-mcp-server`
2. Adicionar em `INSTALLED_APPS`: `mcp_server`
3. Criar arquivo `mcp.py` na pasta `core/` do projeto
4. Definir herança de `MCPTool` e implementar tools (funções que a IA pode chamar)
5. Criar superusuário Django para autenticação
6. Gerar Base64 do credentials: `echo -n "user:pass" | base64`

### Casos de Uso Demonstrados
- Verificar quantos alunos usam a plataforma em tempo real
- Analisar base de usuários e criar notificações personalizadas
- Fechar reportes de bugs via IA
- Criar conquistas e perfis personalizados automaticamente
- Relatórios diários de uso do sistema via WhatsApp

### Grafana MCP
- Servidor MCP oficial do Grafana
- Permite à IA acessar dashboards, métricas, logs, regras de alerta
- Pode rodar queries personalizadas no Prometheus
- Pode criar alertas automaticamente via IA
- Autenticado via Service Account Token do Grafana

### Conexão com Hermes Agent
- Cadastrar MCP no Hermes Agent via linguagem natural
- Hermes usa MCP para: monitorar sistema, gerar relatórios, criar alertas, manipular dados
- Criação de skills personalizadas para uso de MCP

### Automações com Hermes Agent
- **Cron jobs:** Agendar relatórios diários no WhatsApp/Telegram
- **Monitoria contínua:** Analisar logs, métricas, bugs em tempo real
- **Notificações personalizadas:** Criar notificações inteligentes para usuários
- **Pipeline de conteúdo:** Pesquisa → Geração de imagens (GPT Image 2) → Postagem (via Composio)
- **Análise de engajamento:** Métricas de posts no Instagram via Composio

### Composio (Hub de Integrações)
- Plforma gratuita (20k requisições/mês) que centraliza integrações
- Conecta: Instagram, Meta Ads, Google Workspace, YouTube, Nuvemshop, ClickUp, etc.
- Autenticação via OAuth2 (mesmo fluxo das APIs oficiais)
- Hermes usa o CLI do Composio para manipular integrações
- Login via OAuth: `composio login`

### Central de Operações
- Portal web autenticado (Basic Auth via Traefik)
- Mostra: relatórios de conteúdo gerado, imagens, posts agendados
- Criado via prompt para o Hermes Agent
- Versionado no GitHub

### Ferramentas Configuradas no Hermes
- **Firecrawl:** Web scraping com IA (1000 req/mês grátis via API key, ou gratuito via Keyless)
- **Vision (GPT-5.6 Sol):** Análise de imagens
- **Gen Image (GPT Image 2 Medium):** Geração de imagens
- **DuckDuckGo Search:** Busca web simples (grátis, sem limites)
- **Composio CLI + MCP:** Integrações com serviços externos

### OpenCode Go (R$10/mês)
- Modelo recomendado para Hermes 24/7: DeepSeek V3 Flash (bons limites + bom modelo)
- GLM 5.12 para planejamento e código crítico
- GPT-5.6 Sol para tarefas que precisam de qualidade alta
- GPT-5.6 Luna para operações com muitas chamadas (custo baixíssimo)

---

## Resumo Rápido — Checklist de Deploy

1. [ ] Criar VPS (Rockinger KVM2+ / Contabo)
2. [ ] Configurar SSH key + usuário deploy
3. [ ] Instalar Docker + inicializar Swarm
4. [ ] Tuning do servidor (swap, conexões, firewall, fail2ban)
5. [ ] Autenticar no GHCR (token)
6. [ ] Clonar projeto
7. [ ] Criar arquivo `.env` com credenciais
8. [ ] Criar redes Docker (traefik-public, internal, ingress)
9. [ ] Comprar domínio + configurar no Cloudflare (DNS + Name Servers)
10. [ ] Gerar API Token no Cloudflare + criar Docker secret
11. [ ] Build e push da imagem
12. [ ] Deploy com `docker stack deploy`
13. [ ] Verificar logs e funcionamento
14. [ ] Deploy da stack de monitoria (Prometheus, Grafana, Loki, etc.)
15. [ ] Configurar Grafana (dashboards, service account token)
16. [ ] Configurar MCP Server (Django + Grafana)
17. [ ] Conectar MCP no Hermes Agent
18. [ ] Criar cron jobs de monitoria e relatórios

---

## Comandos Úteis

```bash
# Ver serviços
docker stack services scc-v1
docker stack services monitoring

# Ver logs em tempo real
docker service logs -f scc-v1_app
docker service logs -f scc-v1_traefik

# Rolling update manual
docker service update --image ghcr.io/user/app:new_tag scc-v1_app

# Verificar redes
docker network ls
docker network inspect traefik-public

# Verificar secrets
docker secret ls
```

---

*Resumo gerado a partir das transcrições do curso IA Master Elite (Encontros 3, 4 e 5).*
