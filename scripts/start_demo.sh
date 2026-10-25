#!/usr/bin/env bash
# One-command demo start (Git Bash on Windows). Safe to rerun: it stops what it started before.
#   starts the API (:8000), the web app (:3000) and two cloudflared quick tunnels,
#   repoints both Line agents' tool URLs at the new API tunnel, runs the health check,
#   and prints the /live, /approve and /cases URLs plus both phone numbers.
# Never prints the demo key, caller numbers or any member data. SKIP_BUILD=1 skips the web build.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WIN="$(cd "$ROOT" && pwd -W)"
LOGS="$ROOT/logs"
mkdir -p "$LOGS"
cd "$ROOT" || exit 1
CLOUDFLARED='C:\Program Files (x86)\cloudflared\cloudflared.exe'
[ -f "/c/Program Files (x86)/cloudflared/cloudflared.exe" ] || CLOUDFLARED='cloudflared'

ps() { powershell.exe -NoProfile -NonInteractive -Command "$1"; }

echo "1/6 stopping old processes (API, web, tunnels)"
ps 'Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force; foreach ($p in 8000,3000) { Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force } }' >/dev/null 2>&1
sleep 1
rm -f "$LOGS/tunnel_api.err" "$LOGS/tunnel_web.err"

if [ "${SKIP_BUILD:-0}" != "1" ]; then
  echo "2/6 building the web app"
  (cd web && npm run build >"$LOGS/web_build.out" 2>&1) || { echo "web build failed; see logs/web_build.out"; exit 1; }
else
  echo "2/6 skipping the web build"
fi

echo "3/6 starting API, web and tunnels"
ps "Start-Process -FilePath python -ArgumentList '-m','uv','run','python','-m','uvicorn','guardline.api:app','--port','8000' -WorkingDirectory '$WIN' -RedirectStandardOutput '$WIN\\logs\\api.out' -RedirectStandardError '$WIN\\logs\\api.err' -WindowStyle Hidden"
ps "Start-Process -FilePath cmd.exe -ArgumentList '/c','npm run start -- -p 3000' -WorkingDirectory '$WIN\\web' -RedirectStandardOutput '$WIN\\logs\\web.out' -RedirectStandardError '$WIN\\logs\\web.err' -WindowStyle Hidden"
ps "Start-Process -FilePath '$CLOUDFLARED' -ArgumentList 'tunnel','--url','http://localhost:8000','--no-autoupdate' -RedirectStandardError '$WIN\\logs\\tunnel_api.err' -RedirectStandardOutput '$WIN\\logs\\tunnel_api.out' -WindowStyle Hidden"
ps "Start-Process -FilePath '$CLOUDFLARED' -ArgumentList 'tunnel','--url','http://localhost:3000','--no-autoupdate' -RedirectStandardError '$WIN\\logs\\tunnel_web.err' -RedirectStandardOutput '$WIN\\logs\\tunnel_web.out' -WindowStyle Hidden"

tunnel_url() { grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOGS/tunnel_$1.err" 2>/dev/null | tail -1; }
API_URL=""; WEB_URL=""
for _ in $(seq 1 40); do
  API_URL="$(tunnel_url api)"; WEB_URL="$(tunnel_url web)"
  [ -n "$API_URL" ] && [ -n "$WEB_URL" ] && break
  sleep 1
done
[ -n "$API_URL" ] && [ -n "$WEB_URL" ] || { echo "tunnels did not start; see logs/tunnel_*.err"; exit 1; }

echo "4/6 waiting for the API and the web app"
for _ in $(seq 1 60); do curl -s -o /dev/null localhost:8000/api/health && break; sleep 1; done
curl -s -o /dev/null localhost:8000/api/health || { echo "API did not start; see logs/api.err"; exit 1; }
for _ in $(seq 1 30); do curl -s -o /dev/null localhost:3000/call && break; sleep 1; done
for _ in $(seq 1 30); do
  A="$(curl -s -o /dev/null -w '%{http_code}' "$API_URL/api/health")"; W="$(curl -s -o /dev/null -w '%{http_code}' "$WEB_URL/approve")"
  [ "$A" = "200" ] && [ "$W" = "200" ] && break
  sleep 2
done

echo "5/6 repointing the Line agents at the new API tunnel"
SETUP="$(python -m uv run python scripts/line_setup.py "$API_URL" 2>&1 | tail -3)"
NUMBERS="$(echo "$SETUP" | grep -o 'English number.*')"

echo "6/6 health check"
python -m uv run python -m guardline.health 2>&1 | cut -c1-40

echo
echo "================ DEMO READY ================"
echo "Live view  (laptop, big screen): $WEB_URL/live"
echo "Member app (second device):      $WEB_URL/approve"
echo "Cases dashboard:                 $WEB_URL/cases"
echo "Phone web fallback:              $WEB_URL/call"
echo "Phone numbers:                   ${NUMBERS:-see line_setup output}"
echo "The pages ask for the demo key once (DEMO_KEY in guardline/.env); it is saved in that browser."
echo "Open /approve on the second device first and tap 'Turn on alerts'."
