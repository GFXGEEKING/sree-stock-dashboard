# Deployment (Contabo VPS)

Live at: **https://sreestocktrading.duckdns.org**

## Architecture
```
Internet → nginx (80/443, Let's Encrypt)
             ├── /            → /opt/sree-stocks/frontend/dist (static Vite build)
             └── /api, /docs  → uvicorn 127.0.0.1:8000 (systemd: sree-stocks.service)
Git remote `vps` → root@13.140.141.93:/srv/git/sree-stocks.git (bare, HEAD=main)
```
The crypto dashboard (port 8001) is untouched. ufw allows 22/80/443 only.

## Update workflow (from the dev machine)

```bash
git add -A && git commit -m "..." && git push vps main
ssh root@13.140.141.93 'cd /opt/sree-stocks && bash deploy/deploy-vps.sh'
```

The deploy script is **idempotent** — it pulls code, reinstalls deps, rebuilds the
frontend, and restarts nginx + the backend (first run provisions everything).

## Manual commands on the VPS

```bash
systemctl status sree-stocks        # backend + scheduler + agent
journalctl -u sree-stocks -f        # live logs
certbot certificates                # TLS (auto-renews)
ufw status                          # firewall
```

## Notes
- Backend runs with **1 uvicorn worker** (SQLite + one APScheduler instance —
  multiple workers would duplicate the agent and race the DB).
- First scan after a fresh deploy takes 2–3 minutes (populates the SQLite candle
  cache); later scans are fast.
- TLS cert via certbot --nginx (registered without email).
- Frontend is built once per deploy (no Node process at runtime).

## GitHub mirror (optional)
`git push vps main` is the deployment channel. To also publish on GitHub:
1. Create an empty repo on github.com (no README).
2. Add your SSH public key: `cat ~/.ssh/id_ed25519.pub` → GitHub → Settings → SSH keys.
3. `git remote add github git@github.com:<user>/sree-stock-dashboard.git && git push -u github main`
