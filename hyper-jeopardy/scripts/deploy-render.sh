#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Create the "hyper-jeopardy" web service on Render from this repo + branch.
#
# Run this from a machine with normal internet access — the Claude Code cloud
# sandbox blocks api.render.com, so the deploy has to originate outside it.
#
# Requires: bash, curl, jq, and your Render API key.
#
#   export RENDER_API_KEY=rnd_xxxxxxxx
#   bash hyper-jeopardy/scripts/deploy-render.sh
#
# It creates a PUBLIC service (no access gate) so testers can just open the
# onrender.com URL it prints. To put it behind the C64 access code later:
#   • add env var AUTH_SECRET (same value as the other services — copy it from
#     the central-industrial-auth env group) → proxy.ts turns the gate on, and
#   • attach the custom domain jeopardy.centralindustrial.ai + its DNS record.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
: "${RENDER_API_KEY:?Set RENDER_API_KEY first (export RENDER_API_KEY=rnd_...)}"

API=https://api.render.com/v1
BRANCH=claude/multiplayer-trivia-concepts-wg2hjq
NAME=hyper-jeopardy
auth=(-H "Authorization: Bearer ${RENDER_API_KEY}" -H "Accept: application/json" -H "Content-Type: application/json")

echo "→ Looking up your Render account + an existing service (for repo/region)…"
OWNER=$(curl -fsS "${auth[@]}" "$API/owners?limit=1" | jq -r '.[0].owner.id')
SVCS=$(curl -fsS "${auth[@]}" "$API/services?limit=50")
REPO=$(echo "$SVCS" | jq -r '[.[].service.repo // empty][0] // "https://github.com/nurtrino/central-industrial"')
REGION=$(echo "$SVCS" | jq -r '[.[].service.serviceDetails.region // empty][0] // "oregon"')
EXISTS=$(echo "$SVCS" | jq -r --arg n "$NAME" '[.[].service | select(.name==$n) | .id][0] // empty')
echo "  owner=$OWNER  repo=$REPO  region=$REGION"

if [ -n "$EXISTS" ]; then
  echo "→ Service '$NAME' already exists ($EXISTS); triggering a deploy of $BRANCH instead."
  curl -fsS "${auth[@]}" -X POST "$API/services/$EXISTS/deploys" -d '{"clearCache":"do_not_clear"}' >/dev/null
  SID=$EXISTS
else
  echo "→ Creating web service '$NAME' (Docker, branch $BRANCH)…"
  BODY=$(jq -n --arg owner "$OWNER" --arg repo "$REPO" --arg region "$REGION" --arg branch "$BRANCH" --arg name "$NAME" '{
    type: "web_service",
    name: $name,
    ownerId: $owner,
    repo: $repo,
    branch: $branch,
    autoDeploy: "yes",
    rootDir: "hyper-jeopardy",
    serviceDetails: {
      runtime: "docker",
      plan: "starter",
      region: $region,
      healthCheckPath: "/api/health",
      envSpecificDetails: { dockerfilePath: "./Dockerfile", dockerContext: "." },
      envVars: [
        { key: "NODE_ENV", value: "production" },
        { key: "HOME_URL", value: "https://centralindustrial.ai" }
      ]
    }
  }')
  RESP=$(curl -fsS "${auth[@]}" -X POST "$API/services" -d "$BODY")
  SID=$(echo "$RESP" | jq -r '.service.id // .id')
  echo "  created service id: $SID"
fi

URL=$(curl -fsS "${auth[@]}" "$API/services/$SID" | jq -r '.serviceDetails.url // .service.serviceDetails.url // empty')
echo
echo "✓ Deploy started. Watch build logs in the Render dashboard."
echo "  Play-test URL (live once the build finishes, ~3-6 min):"
echo "    ${URL:-https://$NAME.onrender.com}"
echo
echo "  /display  = the shared TV screen ·  /  = each player's phone controller"
