# Deploying capita

How the public server at `capita.leeswecho.net` (an AWS EC2 machine running Ubuntu 26.04) is set
up. The player, developer view and control servers run as systemd services. Caddy sits in front of them, serving
HTTPS with a Let's Encrypt certificate it obtains and renews by itself.

| Address | Goes to |
|---------|---------|
| `https://capita.leeswecho.net/` | player page (`player/player.py`), `127.0.0.1:8000` |
| `https://capita.leeswecho.net:9443/` | developer view (`server/server.py`), `127.0.0.1:9000` |
| `https://capita.leeswecho.net:8443/` | control server, `127.0.0.1:8001` |
| `http://capita.leeswecho.net/` | redirects to HTTPS |
| `http://capita.leeswecho.net:8000/`, `:9000/`, `:8001/` | the Python servers directly, without HTTPS |

The player page shows only what the chosen nation knows. The developer view sees the whole game state
and its mechanics, and the control page is the developer's controls.

The servers have no authentication. Anyone who can reach the control page can commit turns.

## Files

| File | Installed at |
|------|--------------|
| `capita-player.service` | `/etc/systemd/system/capita-player.service` |
| `capita-view.service` | `/etc/systemd/system/capita-view.service` |
| `capita-control.service` | `/etc/systemd/system/capita-control.service` |
| `Caddyfile` | `/etc/caddy/Caddyfile` |

## Setting up a new machine

1. Point the hostname's DNS A record at the machine, and allow inbound TCP 80, 443, 8443, 9443, 8000,
   9000 and 8001 in the security group (8000, 9000 and 8001 only for direct HTTP access).
2. Install the dependencies and clone the repository as `ubuntu`:

   ```sh
   sudo apt-get update
   sudo apt-get install -y python3-numpy python3-yaml caddy
   git clone https://github.com/leeswecho/capita.git ~/capita
   ```

3. Create turn 0:

   ```sh
   cd ~/capita/server && python3 ../engine/engine.py init
   ```

4. Install the services and the Caddy config, then start everything:

   ```sh
   cd ~/capita/deploy
   sudo cp capita-player.service capita-view.service capita-control.service /etc/systemd/system/
   sudo cp Caddyfile /etc/caddy/Caddyfile
   sudo systemctl daemon-reload
   sudo systemctl enable --now capita-player capita-view capita-control
   sudo systemctl enable caddy && sudo systemctl restart caddy
   ```

   Caddy gets the certificate within a few seconds; `journalctl -u caddy` shows "certificate
   obtained successfully".

## Day to day

- **Update after a push:** `cd ~/capita && git pull && sudo systemctl restart capita-player capita-view capita-control`
- **Logs:** `journalctl -u capita-player -f`, `journalctl -u capita-view -f`, `journalctl -u capita-control -f`, `journalctl -u caddy -f`
- **Start the game over:**

  ```sh
  sudo systemctl stop capita-player capita-view capita-control
  rm -rf ~/capita/state
  (cd ~/capita/server && python3 ../engine/engine.py init)
  sudo systemctl start capita-player capita-view capita-control
  ```

- **After changing the Caddyfile:** copy it to `/etc/caddy/Caddyfile`, then
  `sudo systemctl reload caddy`.
